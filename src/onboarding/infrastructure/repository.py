from __future__ import annotations

from typing import Protocol
from uuid import UUID

from onboarding.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    Employee,
    OnboardingCase,
    OnboardingTask,
    TaskDependency,
)


class CaseRepository(Protocol):
    """Protocol for storing and querying onboarding aggregate state."""

    def save_case(self, case: OnboardingCase) -> None: ...

    def get_case(self, case_id: UUID) -> OnboardingCase | None: ...

    def find_case_by_number(self, case_number: str) -> OnboardingCase | None: ...

    def save_employee(self, employee: Employee) -> None: ...

    def get_employee(self, employee_id: UUID) -> Employee | None: ...

    def save_tasks(self, tasks: list[OnboardingTask]) -> None: ...

    def get_tasks_for_case(self, case_id: UUID) -> list[OnboardingTask]: ...

    def save_task_dependencies(self, dependencies: list[TaskDependency]) -> None: ...

    def get_task_dependencies_for_case(self, case_id: UUID) -> list[TaskDependency]: ...

    def save_audit_event(self, event: AuditEvent) -> None: ...

    def get_audit_events(self, case_id: UUID) -> list[AuditEvent]: ...

    def save_approval_request(self, request: ApprovalRequest) -> None: ...

    def get_approval_request_for_task(
        self, task_id: UUID
    ) -> ApprovalRequest | None: ...

    def get_approval_requests_for_case(
        self, case_id: UUID
    ) -> list[ApprovalRequest]: ...

    def find_approval_request_by_token_hash(
        self, token_hash: str
    ) -> ApprovalRequest | None: ...

    def get_all_pending_approval_requests(self) -> list[ApprovalRequest]: ...

    def commit_provisioning_transition(
        self,
        case: OnboardingCase,
        employee: Employee,
        tasks: list[OnboardingTask],
        dependencies: list[TaskDependency],
        audit_events: list[AuditEvent],
    ) -> None: ...


class InMemoryCaseRepository:
    """In-memory store for cases, employees, tasks, and audit logs."""

    def __init__(self) -> None:
        self._cases: dict[UUID, OnboardingCase] = {}
        self._cases_by_number: dict[str, UUID] = {}
        self._employees: dict[UUID, Employee] = {}
        self._tasks: dict[UUID, list[OnboardingTask]] = {}
        self._dependencies: dict[UUID, list[TaskDependency]] = {}
        self._audit_events: dict[UUID, list[AuditEvent]] = {}
        self._approval_requests_by_task: dict[UUID, ApprovalRequest] = {}
        self._approval_requests_by_case: dict[UUID, list[ApprovalRequest]] = {}
        self._approval_requests_by_token_hash: dict[str, ApprovalRequest] = {}

    def save_case(self, case: OnboardingCase) -> None:
        self._cases[case.id] = case
        self._cases_by_number[case.case_number] = case.id

    def get_case(self, case_id: UUID) -> OnboardingCase | None:
        return self._cases.get(case_id)

    def find_case_by_number(self, case_number: str) -> OnboardingCase | None:
        case_id = self._cases_by_number.get(case_number)
        if case_id is None:
            return None
        return self._cases.get(case_id)

    def save_employee(self, employee: Employee) -> None:
        self._employees[employee.id] = employee

    def get_employee(self, employee_id: UUID) -> Employee | None:
        return self._employees.get(employee_id)

    def save_tasks(self, tasks: list[OnboardingTask]) -> None:
        for task in tasks:
            task_list = self._tasks.setdefault(task.case_id, [])
            for i, existing in enumerate(task_list):
                if existing.id == task.id:
                    task_list[i] = task
                    break
            else:
                task_list.append(task)

    def get_tasks_for_case(self, case_id: UUID) -> list[OnboardingTask]:
        return list(self._tasks.get(case_id, []))

    def save_task_dependencies(self, dependencies: list[TaskDependency]) -> None:
        for dep in dependencies:
            dep_list = self._dependencies.setdefault(dep.task_id, [])
            exists = any(
                d.depends_on_task_id == dep.depends_on_task_id for d in dep_list
            )
            if not exists:
                dep_list.append(dep)

    def get_task_dependencies_for_case(self, case_id: UUID) -> list[TaskDependency]:
        case_tasks = self.get_tasks_for_case(case_id)
        task_ids = {t.id for t in case_tasks}
        result: list[TaskDependency] = []
        for tid in task_ids:
            result.extend(self._dependencies.get(tid, []))
        return result

    def save_audit_event(self, event: AuditEvent) -> None:
        self._audit_events.setdefault(event.case_id, []).append(event)

    def get_audit_events(self, case_id: UUID) -> list[AuditEvent]:
        return list(self._audit_events.get(case_id, []))

    def save_approval_request(self, request: ApprovalRequest) -> None:
        self._approval_requests_by_task[request.task_id] = request
        self._approval_requests_by_token_hash[request.verp_token_hash] = request
        reqs = self._approval_requests_by_case.setdefault(request.case_id, [])
        for i, existing in enumerate(reqs):
            if existing.id == request.id:
                reqs[i] = request
                return
        reqs.append(request)

    def get_approval_request_for_task(self, task_id: UUID) -> ApprovalRequest | None:
        return self._approval_requests_by_task.get(task_id)

    def get_approval_requests_for_case(self, case_id: UUID) -> list[ApprovalRequest]:
        return list(self._approval_requests_by_case.get(case_id, []))

    def find_approval_request_by_token_hash(
        self, token_hash: str
    ) -> ApprovalRequest | None:
        return self._approval_requests_by_token_hash.get(token_hash)

    def get_all_pending_approval_requests(self) -> list[ApprovalRequest]:
        return [
            req
            for req in self._approval_requests_by_task.values()
            if req.status in (ApprovalStatus.PENDING, ApprovalStatus.ESCALATED)
        ]

    def commit_provisioning_transition(
        self,
        case: OnboardingCase,
        employee: Employee,
        tasks: list[OnboardingTask],
        dependencies: list[TaskDependency],
        audit_events: list[AuditEvent],
    ) -> None:
        """Atomic commit simulating transactional integrity."""
        self.save_case(case)
        self.save_employee(employee)
        self.save_tasks(tasks)
        self.save_task_dependencies(dependencies)
        for event in audit_events:
            self.save_audit_event(event)
