from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from onboarding.domain.models import (
    AuditEvent,
    Employee,
    OnboardingCase,
    OnboardingTask,
    TaskStatus,
)
from onboarding.domain.ports import GoogleWorkspacePort, SlackPort
from onboarding.infrastructure.mock_adapters import (
    MockGoogleWorkspaceAdapter,
    MockSlackAdapter,
)
from onboarding.infrastructure.repository import CaseRepository


class TaskExecutionEngine:
    """Evaluates DAG readiness and executes autonomous Tier 1 tasks."""

    def __init__(
        self,
        repository: CaseRepository,
        google_port: GoogleWorkspacePort | None = None,
        slack_port: SlackPort | None = None,
        max_retries: int = 3,
    ) -> None:
        self.repository = repository
        self.google_port: GoogleWorkspacePort = (
            google_port or MockGoogleWorkspaceAdapter()
        )
        self.slack_port: SlackPort = slack_port or MockSlackAdapter()
        self.max_retries = max_retries

    async def execute_autonomous_tasks(self, case_id: UUID) -> list[OnboardingTask]:
        """Execute unblocked Tier 1 tasks until no ready autonomous tasks remain."""
        case = self.repository.get_case(case_id)
        if case is None or case.employee_id is None:
            return []

        employee = self.repository.get_employee(case.employee_id)
        if employee is None:
            return []

        executed_tasks: list[OnboardingTask] = []

        while True:
            all_tasks = self.repository.get_tasks_for_case(case_id)
            ready_autonomous = [
                t
                for t in all_tasks
                if t.status == TaskStatus.READY and not t.requires_approval
            ]
            if not ready_autonomous:
                break

            for task in ready_autonomous:
                await self._execute_single_task(case, employee, task)
                executed_tasks.append(task)
                self._advance_dag_dependencies(case_id)

        return executed_tasks

    async def _execute_single_task(
        self,
        case: OnboardingCase,
        employee: Employee,
        task: OnboardingTask,
    ) -> None:
        idempotency_key = f"{case.id}:{task.task_key}"
        task.status = TaskStatus.IN_PROGRESS
        task.updated_at = datetime.now(UTC)

        audit_events: list[AuditEvent] = []

        if task.task_key == "CREATE_GOOGLE_WORKSPACE":
            # Generate corporate email: first.last@company.internal
            clean_name = re.sub(r"[^a-zA-Z0-9\s]", "", employee.full_name).lower()
            tokens = clean_name.split()
            first = tokens[0] if tokens else "user"
            last = tokens[-1] if len(tokens) > 1 else "corp"
            primary_email = f"{first}.{last}@company.internal"

            result = await self.google_port.create_user(
                full_name=employee.full_name,
                primary_email=primary_email,
                personal_recovery_email=str(employee.personal_email),
                department=employee.department,
                idempotency_key=idempotency_key,
            )

            if result.success:
                employee.company_email = primary_email
                employee.updated_at = datetime.now(UTC)
                self.repository.save_employee(employee)

                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC)
                task.payload = result.metadata
                task.error_message = None

                audit_events.append(
                    AuditEvent(
                        case_id=case.id,
                        event_type="TASK_COMPLETED",
                        actor_type="SYSTEM",
                        actor_id="google_workspace_port",
                        payload={
                            "task_key": task.task_key,
                            "external_id": result.external_id,
                            "reconciled": result.metadata.get("reconciled", False),
                            "company_email": primary_email,
                        },
                    )
                )
            else:
                self._handle_task_failure(
                    case,
                    task,
                    result.error_message,
                    result.retryable,
                    audit_events,
                )

        elif task.task_key == "INVITE_SLACK":
            target_email = employee.company_email or str(employee.personal_email)
            channels = ["general", f"team-{employee.department.lower()}"]

            result = await self.slack_port.invite_user(
                email=target_email,
                full_name=employee.full_name,
                channels=channels,
                idempotency_key=idempotency_key,
            )

            if result.success:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC)
                task.payload = result.metadata
                task.error_message = None

                audit_events.append(
                    AuditEvent(
                        case_id=case.id,
                        event_type="TASK_COMPLETED",
                        actor_type="SYSTEM",
                        actor_id="slack_port",
                        payload={
                            "task_key": task.task_key,
                            "external_id": result.external_id,
                            "reconciled": result.metadata.get("reconciled", False),
                            "channels": channels,
                        },
                    )
                )
            else:
                self._handle_task_failure(
                    case,
                    task,
                    result.error_message,
                    result.retryable,
                    audit_events,
                )

        self.repository.save_case(case)
        for event in audit_events:
            self.repository.save_audit_event(event)

    def _handle_task_failure(
        self,
        case: OnboardingCase,
        task: OnboardingTask,
        error_message: str | None,
        retryable: bool,
        audit_events: list[AuditEvent],
    ) -> None:
        task.error_message = error_message
        task.updated_at = datetime.now(UTC)

        if retryable and task.retry_count < self.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.READY
            audit_events.append(
                AuditEvent(
                    case_id=case.id,
                    event_type="TASK_RETRY_SCHEDULED",
                    actor_type="SYSTEM",
                    actor_id="task_engine",
                    payload={
                        "task_key": task.task_key,
                        "retry_count": task.retry_count,
                        "error_message": error_message,
                    },
                )
            )
        else:
            task.status = TaskStatus.FAILED
            audit_events.append(
                AuditEvent(
                    case_id=case.id,
                    event_type="TASK_FAILED",
                    actor_type="SYSTEM",
                    actor_id="task_engine",
                    payload={
                        "task_key": task.task_key,
                        "retry_count": task.retry_count,
                        "error_message": error_message,
                        "retryable": retryable,
                    },
                )
            )

    def _advance_dag_dependencies(self, case_id: UUID) -> None:
        all_tasks = self.repository.get_tasks_for_case(case_id)
        task_map = {t.id: t for t in all_tasks}
        dependencies = self.repository.get_task_dependencies_for_case(case_id)

        # Map each task to its prerequisite task IDs
        prereqs_map: dict[UUID, set[UUID]] = {}
        for dep in dependencies:
            prereqs_map.setdefault(dep.task_id, set()).add(dep.depends_on_task_id)

        for task in all_tasks:
            if task.status != TaskStatus.WAITING_DEPENDENCY:
                continue

            prereqs = prereqs_map.get(task.id, set())
            all_prereqs_completed = all(
                task_map[pid].status == TaskStatus.COMPLETED
                for pid in prereqs
                if pid in task_map
            )

            if all_prereqs_completed:
                # Transition according to approval requirements
                old_status = task.status
                if task.requires_approval:
                    task.status = TaskStatus.WAITING_APPROVAL
                else:
                    task.status = TaskStatus.READY

                task.updated_at = datetime.now(UTC)

                self.repository.save_audit_event(
                    AuditEvent(
                        case_id=case_id,
                        event_type="TASK_STATUS_TRANSITION",
                        actor_type="SYSTEM",
                        actor_id="task_engine",
                        payload={
                            "task_key": task.task_key,
                            "from_status": old_status.value,
                            "to_status": task.status.value,
                            "requires_approval": task.requires_approval,
                        },
                    )
                )
