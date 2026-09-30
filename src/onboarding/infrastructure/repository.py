from __future__ import annotations

from typing import Protocol
from uuid import UUID

from onboarding.domain.models import AuditEvent, OnboardingCase


class CaseRepository(Protocol):
    """Protocol for storing and retrieving cases and audit logs."""

    def save_case(self, case: OnboardingCase) -> None: ...

    def get_case(self, case_id: UUID) -> OnboardingCase | None: ...

    def save_audit_event(self, event: AuditEvent) -> None: ...

    def get_audit_events(self, case_id: UUID) -> list[AuditEvent]: ...


class InMemoryCaseRepository:
    """Thread-safe in-memory store for cases and audit logs."""

    def __init__(self) -> None:
        self._cases: dict[UUID, OnboardingCase] = {}
        self._audit_events: dict[UUID, list[AuditEvent]] = {}

    def save_case(self, case: OnboardingCase) -> None:
        self._cases[case.id] = case

    def get_case(self, case_id: UUID) -> OnboardingCase | None:
        return self._cases.get(case_id)

    def save_audit_event(self, event: AuditEvent) -> None:
        self._audit_events.setdefault(event.case_id, []).append(event)

    def get_audit_events(self, case_id: UUID) -> list[AuditEvent]:
        return list(self._audit_events.get(case_id, []))
