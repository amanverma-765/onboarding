from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CaseStatus(StrEnum):
    DRAFT = "DRAFT"
    INFORMATION_GATHERING = "INFORMATION_GATHERING"
    PROVISIONING = "PROVISIONING"
    READY_FOR_FIRST_DAY = "READY_FOR_FIRST_DAY"
    ACTIVE_ONBOARDING = "ACTIVE_ONBOARDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class TaskStatus(StrEnum):
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    ESCALATED = "ESCALATED"


TaskOwnerType = Literal["IT", "HR", "MANAGER", "EMPLOYEE"]

MANDATORY_CANDIDATE_FIELDS: tuple[str, ...] = (
    "full_name",
    "personal_email",
    "department",
    "role_title",
    "manager_email",
    "start_date",
)


class CandidateDraft(BaseModel):
    """Candidate attributes collected during the intake phase."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(
        default=None, description="Full legal name of candidate"
    )
    personal_email: EmailStr | None = Field(
        default=None, description="Personal email address"
    )
    role_title: str | None = Field(default=None, description="Job title")
    department: str | None = Field(default=None, description="Assigned department")
    manager_name: str | None = Field(default=None, description="Name of hiring manager")
    manager_email: EmailStr | None = Field(
        default=None, description="Work email of hiring manager"
    )
    start_date: date | None = Field(default=None, description="First working day")

    @property
    def name(self) -> str | None:
        """Alias for full_name for conversational compatibility."""
        return self.full_name

    @field_validator("full_name", "role_title", "department", "manager_name")
    @classmethod
    def sanitize_strings(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = re.sub(r"[\r\n\t]+", " ", v).strip()
        if len(cleaned) > 100:
            cleaned = cleaned[:100]
        return cleaned or None


class Employee(BaseModel):
    """Permanent, verified company employee entity."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    company_email: str | None = None
    personal_email: EmailStr
    full_name: str
    role_title: str
    department: str
    manager_id: UUID | None = None
    start_date: date
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OnboardingTask(BaseModel):
    """Discrete executable task in the onboarding DAG."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    task_key: str
    owner_type: TaskOwnerType
    status: TaskStatus = TaskStatus.WAITING_DEPENDENCY
    requires_approval: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    retry_count: int = 0
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskDependency(BaseModel):
    """Prerequisite relationship between two onboarding tasks."""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    depends_on_task_id: UUID


class ApprovalRequest(BaseModel):
    """Explicit human sign-off gate for Tier 2 privileged tasks."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    case_id: UUID
    approver_email: EmailStr
    status: ApprovalStatus = ApprovalStatus.PENDING
    verp_token_hash: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    decision_reason: str | None = None


class InboundEmail(BaseModel):
    """Incoming RFC 5322 email representation."""

    model_config = ConfigDict(extra="forbid")

    sender: EmailStr
    recipient: EmailStr
    subject: str
    body: str
    message_id: str | None = None


class OutboundEmail(BaseModel):
    """Outgoing message drafted by the agent coordinator."""

    model_config = ConfigDict(extra="forbid")

    recipient: EmailStr
    subject: str
    body: str
    reply_to: EmailStr | None = None
    in_reply_to: str | None = None


class AuditEvent(BaseModel):
    """Immutable audit entry tracking domain events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    event_type: str
    actor_type: Literal["SYSTEM", "AGENT", "USER"]
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OnboardingCase(BaseModel):
    """Aggregate root tracking a hire's onboarding lifecycle."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    case_number: str
    status: CaseStatus = CaseStatus.DRAFT
    employee_id: UUID | None = None
    candidate_data: CandidateDraft = Field(default_factory=CandidateDraft)
    missing_fields: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
