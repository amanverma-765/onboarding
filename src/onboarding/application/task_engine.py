from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from onboarding.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    CaseStatus,
    Employee,
    InboundEmail,
    OnboardingCase,
    OnboardingTask,
    OutboundEmail,
    TaskStatus,
)
from onboarding.domain.ports import (
    GitHubPort,
    GoogleWorkspacePort,
    HardwarePort,
    SlackPort,
)
from onboarding.domain.verp import VerpTokenService
from onboarding.infrastructure.mock_adapters import (
    MockGitHubAdapter,
    MockGoogleWorkspaceAdapter,
    MockHardwareAdapter,
    MockSlackAdapter,
)
from onboarding.infrastructure.repository import CaseRepository


class ApprovalReplyResult(BaseModel):
    """Result of processing an inbound approval reply."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "APPROVED", "REJECTED", "AMBIGUOUS", "UNAUTHORIZED", "ALREADY_DECIDED"
    ]
    approval_request: ApprovalRequest | None = None
    task: OnboardingTask | None = None
    outbound_email: OutboundEmail | None = None
    message: str


class TaskExecutionEngine:
    """Evaluates DAG readiness and executes autonomous and approved tasks."""

    _APPROVE_PATTERN = re.compile(
        r"\b(approve|approved|proceed|confirm|go ahead|looks good)\b",
        re.IGNORECASE,
    )
    _REJECT_PATTERN = re.compile(
        r"\b(reject|rejected|deny|denied|hold off|cancel|no)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        repository: CaseRepository,
        google_port: GoogleWorkspacePort | None = None,
        slack_port: SlackPort | None = None,
        github_port: GitHubPort | None = None,
        hardware_port: HardwarePort | None = None,
        verp_service: VerpTokenService | None = None,
        max_retries: int = 3,
    ) -> None:
        self.repository = repository
        self.google_port: GoogleWorkspacePort = (
            google_port or MockGoogleWorkspaceAdapter()
        )
        self.slack_port: SlackPort = slack_port or MockSlackAdapter()
        self.github_port: GitHubPort = github_port or MockGitHubAdapter()
        self.hardware_port: HardwarePort = hardware_port or MockHardwareAdapter()
        self.verp_service: VerpTokenService = verp_service or VerpTokenService()
        self.max_retries = max_retries

    def create_pending_approval_requests(
        self, case_id: UUID
    ) -> list[tuple[ApprovalRequest, OutboundEmail]]:
        """Generate VERP ApprovalRequests for WAITING_APPROVAL tasks."""
        case = self.repository.get_case(case_id)
        if case is None:
            return []

        tasks = self.repository.get_tasks_for_case(case_id)
        approver = case.candidate_data.manager_email
        if not approver:
            return []

        created: list[tuple[ApprovalRequest, OutboundEmail]] = []
        for task in tasks:
            if task.status != TaskStatus.WAITING_APPROVAL:
                continue
            existing = self.repository.get_approval_request_for_task(task.id)
            if existing is not None:
                continue

            token = self.verp_service.generate_token(case.id, task.id, approver)
            token_hash = self.verp_service.hash_token(token)

            approval_req = ApprovalRequest(
                task_id=task.id,
                case_id=case.id,
                approver_email=approver,
                status=ApprovalStatus.PENDING,
                verp_token_hash=token_hash,
            )
            self.repository.save_approval_request(approval_req)

            candidate_label = case.candidate_data.full_name or "New Hire"
            reply_to = self.verp_service.format_reply_to(token)
            body = (
                f"Hello,\n\n"
                f"Approval is required to proceed with {task.task_key} for "
                f"{candidate_label} [{case.case_number}].\n\n"
                f"To approve, reply directly to this email with 'Approve'.\n"
                f"To reject, reply with 'Reject'.\n\n"
                f"Best regards,\n"
                f"Onboarding Coordination Agent"
            )

            email = OutboundEmail(
                recipient=approver,
                subject=(
                    f"Approval Required: {task.task_key} for {candidate_label} "
                    f"[{case.case_number}]"
                ),
                body=body,
                reply_to=reply_to,
            )

            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="APPROVAL_REQUESTED",
                    actor_type="AGENT",
                    actor_id="task_engine",
                    payload={
                        "task_key": task.task_key,
                        "approver": approver,
                        "reply_to": reply_to,
                    },
                )
            )
            created.append((approval_req, email))

        return created

    async def handle_approval_reply(self, email: InboundEmail) -> ApprovalReplyResult:
        """Process manager approval reply through anti-spoofing and ambiguity gates."""
        # 1. Extract and lookup VERP token
        token = self.verp_service.extract_token_from_email(str(email.recipient))
        if not token:
            token = self.verp_service.extract_token_from_email(
                email.subject + " " + email.body
            )

        if not token:
            return ApprovalReplyResult(
                status="UNAUTHORIZED",
                message="Missing cryptographic VERP token in return address",
            )

        token_hash = self.verp_service.hash_token(token)
        approval_req = self.repository.find_approval_request_by_token_hash(token_hash)
        if approval_req is None:
            return ApprovalReplyResult(
                status="UNAUTHORIZED",
                message="Invalid or unrecognized VERP token",
            )

        case = self.repository.get_case(approval_req.case_id)
        if case is None:
            return ApprovalReplyResult(
                status="UNAUTHORIZED",
                message="Referenced onboarding case not found",
            )

        tasks = self.repository.get_tasks_for_case(case.id)
        task = next((t for t in tasks if t.id == approval_req.task_id), None)
        if task is None:
            return ApprovalReplyResult(
                status="UNAUTHORIZED", message="Referenced task not found"
            )

        # 2. Sender verification
        if email.sender.lower() != approval_req.approver_email.lower():
            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="UNAUTHORIZED_APPROVAL_ATTEMPT",
                    actor_type="USER",
                    actor_id=str(email.sender),
                    payload={
                        "expected_approver": approval_req.approver_email,
                        "task_key": task.task_key,
                    },
                )
            )
            return ApprovalReplyResult(
                status="UNAUTHORIZED",
                approval_request=approval_req,
                task=task,
                message=(
                    f"Sender {email.sender} does not match designated "
                    f"approver {approval_req.approver_email}"
                ),
            )

        # 3. Already decided check
        if approval_req.status != ApprovalStatus.PENDING:
            return ApprovalReplyResult(
                status="ALREADY_DECIDED",
                approval_request=approval_req,
                task=task,
                message=(
                    f"Approval request already in status {approval_req.status.value}"
                ),
            )

        # 4. Ambiguity Gate Evaluation
        body = email.body
        is_approval = bool(self._APPROVE_PATTERN.search(body))
        is_rejection = bool(self._REJECT_PATTERN.search(body))

        if is_approval and not is_rejection:
            approval_req.status = ApprovalStatus.APPROVED
            approval_req.decided_at = datetime.now(UTC)
            approval_req.decision_reason = body.strip()[:200]
            self.repository.save_approval_request(approval_req)

            task.status = TaskStatus.READY
            task.updated_at = datetime.now(UTC)
            self.repository.save_tasks([task])

            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="APPROVAL_GRANTED",
                    actor_type="USER",
                    actor_id=str(email.sender),
                    payload={
                        "task_key": task.task_key,
                        "reason": approval_req.decision_reason,
                    },
                )
            )

            # Execute approved task immediately
            employee = self.repository.get_employee(case.employee_id)  # type: ignore[arg-type]
            if employee is not None:
                await self._execute_single_task(case, employee, task)
                self._advance_dag_dependencies(case.id)
                self.evaluate_case_readiness(case.id)

            return ApprovalReplyResult(
                status="APPROVED",
                approval_request=approval_req,
                task=task,
                message=f"Task {task.task_key} approved and executed",
            )

        if is_rejection and not is_approval:
            approval_req.status = ApprovalStatus.REJECTED
            approval_req.decided_at = datetime.now(UTC)
            approval_req.decision_reason = body.strip()[:200]
            self.repository.save_approval_request(approval_req)

            task.status = TaskStatus.SKIPPED
            task.updated_at = datetime.now(UTC)
            self.repository.save_tasks([task])

            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="APPROVAL_REJECTED",
                    actor_type="USER",
                    actor_id=str(email.sender),
                    payload={
                        "task_key": task.task_key,
                        "reason": approval_req.decision_reason,
                    },
                )
            )

            # Skip downstream-only dependents
            self._skip_dependent_tasks(case.id, task.id)
            self.evaluate_case_readiness(case.id)

            return ApprovalReplyResult(
                status="REJECTED",
                approval_request=approval_req,
                task=task,
                message=f"Task {task.task_key} rejected and marked SKIPPED",
            )

        # Ambiguous chatter -> remain PENDING and draft clarification email
        self.repository.save_audit_event(
            AuditEvent(
                case_id=case.id,
                event_type="APPROVAL_AMBIGUITY_DETECTED",
                actor_type="AGENT",
                actor_id="ambiguity_gate",
                payload={
                    "task_key": task.task_key,
                    "sender": str(email.sender),
                    "received_text": body[:200],
                },
            )
        )

        candidate_label = case.candidate_data.full_name or "New Hire"
        clarification_body = (
            f"Hello,\n\n"
            f"We received your message regarding {task.task_key} for "
            f"{candidate_label} [{case.case_number}], but could not determine "
            f"if you intended to approve or reject the request.\n\n"
            f"Please reply with an explicit 'Approve' or 'Reject' to proceed.\n\n"
            f"Best regards,\n"
            f"Onboarding Coordination Agent"
        )
        clarification_email = OutboundEmail(
            recipient=email.sender,
            subject=(
                f"Clarification Needed: Approval for {task.task_key} "
                f"[{case.case_number}]"
            ),
            body=clarification_body,
            reply_to=email.recipient,
            in_reply_to=email.message_id,
        )

        return ApprovalReplyResult(
            status="AMBIGUOUS",
            approval_request=approval_req,
            task=task,
            outbound_email=clarification_email,
            message="Reply ambiguous; clarification email drafted",
        )

    def _skip_dependent_tasks(self, case_id: UUID, rejected_task_id: UUID) -> None:
        """Mark tasks that exclusively depend on the rejected task as SKIPPED."""
        deps = self.repository.get_task_dependencies_for_case(case_id)
        all_tasks = self.repository.get_tasks_for_case(case_id)
        task_map = {t.id: t for t in all_tasks}

        for dep in deps:
            if dep.depends_on_task_id == rejected_task_id:
                target = task_map.get(dep.task_id)
                if target and target.status == TaskStatus.WAITING_DEPENDENCY:
                    target.status = TaskStatus.SKIPPED
                    target.updated_at = datetime.now(UTC)
                    self.repository.save_tasks([target])
                    self.repository.save_audit_event(
                        AuditEvent(
                            case_id=case_id,
                            event_type="TASK_SKIPPED",
                            actor_type="SYSTEM",
                            actor_id="task_engine",
                            payload={
                                "task_key": target.task_key,
                                "reason": (
                                    f"Prerequisite task {rejected_task_id} was rejected"
                                ),
                            },
                        )
                    )

    def evaluate_case_readiness(self, case_id: UUID) -> OnboardingCase | None:
        """Advance case to READY_FOR_FIRST_DAY if all DAG tasks are terminal."""
        case = self.repository.get_case(case_id)
        if case is None or case.status != CaseStatus.PROVISIONING:
            return case

        tasks = self.repository.get_tasks_for_case(case_id)
        if not tasks:
            return case

        all_terminal = all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in tasks
        )
        if all_terminal:
            case.status = CaseStatus.READY_FOR_FIRST_DAY
            case.updated_at = datetime.now(UTC)
            self.repository.save_case(case)
            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="STATUS_TRANSITION",
                    actor_type="SYSTEM",
                    actor_id="task_engine",
                    payload={
                        "from_status": CaseStatus.PROVISIONING.value,
                        "to_status": CaseStatus.READY_FOR_FIRST_DAY.value,
                    },
                )
            )
        return case

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

        self.evaluate_case_readiness(case_id)
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

        elif task.task_key == "INVITE_GITHUB":
            target_email = employee.company_email or str(employee.personal_email)
            result = await self.github_port.invite_org_member(
                email=target_email,
                teams=["engineering"],
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
                        actor_id="github_port",
                        payload={"task_key": task.task_key},
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

        elif task.task_key == "ORDER_LAPTOP":
            result = await self.hardware_port.order_laptop(
                employee_id=str(employee.id),
                role_title=employee.role_title,
                shipping_address={"country": "US"},
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
                        actor_id="hardware_port",
                        payload={"task_key": task.task_key},
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
        self.repository.save_tasks([task])
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

        prereqs_map: dict[UUID, set[UUID]] = {}
        for dep in dependencies:
            prereqs_map.setdefault(dep.task_id, set()).add(dep.depends_on_task_id)

        for task in all_tasks:
            if task.status != TaskStatus.WAITING_DEPENDENCY:
                continue

            prereqs = prereqs_map.get(task.id, set())
            all_prereqs_completed = all(
                task_map[pid].status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
                for pid in prereqs
                if pid in task_map
            )

            if all_prereqs_completed:
                old_status = task.status
                if task.requires_approval:
                    task.status = TaskStatus.WAITING_APPROVAL
                else:
                    task.status = TaskStatus.READY

                task.updated_at = datetime.now(UTC)
                self.repository.save_tasks([task])

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
