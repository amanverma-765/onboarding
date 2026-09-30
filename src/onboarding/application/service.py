from __future__ import annotations

import itertools
import re
from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from onboarding.domain.extractor import (
    CandidateExtractor,
    NluExtractionResult,
    RuleBasedCandidateExtractor,
    compute_missing_fields,
)
from onboarding.domain.models import (
    AuditEvent,
    CandidateDraft,
    CaseStatus,
    Employee,
    InboundEmail,
    OnboardingCase,
    OnboardingTask,
    OutboundEmail,
    TaskDependency,
    TaskStatus,
)
from onboarding.infrastructure.repository import (
    CaseRepository,
    InMemoryCaseRepository,
)


class InboundProcessingResult(BaseModel):
    """Result of processing an inbound onboarding email."""

    model_config = ConfigDict(extra="forbid")

    case: OnboardingCase
    extraction: NluExtractionResult
    employee: Employee | None = None
    tasks: list[OnboardingTask] = Field(default_factory=list)
    task_dependencies: list[TaskDependency] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
    outbound_email: OutboundEmail | None = None


def merge_candidate_drafts(
    existing: CandidateDraft, update: CandidateDraft
) -> CandidateDraft:
    """Merge newly provided candidate fields into an existing draft."""
    return CandidateDraft(
        full_name=update.full_name or existing.full_name,
        personal_email=update.personal_email or existing.personal_email,
        role_title=update.role_title or existing.role_title,
        department=update.department or existing.department,
        manager_name=update.manager_name or existing.manager_name,
        manager_email=update.manager_email or existing.manager_email,
        start_date=update.start_date or existing.start_date,
    )


def build_default_task_dag(
    case_id: UUID,
) -> tuple[list[OnboardingTask], list[TaskDependency]]:
    """Construct the initial declarative task DAG with prerequisite edges."""
    create_gw = OnboardingTask(
        case_id=case_id,
        task_key="CREATE_GOOGLE_WORKSPACE",
        owner_type="IT",
        status=TaskStatus.READY,
        requires_approval=False,
    )
    invite_slack = OnboardingTask(
        case_id=case_id,
        task_key="INVITE_SLACK",
        owner_type="IT",
        status=TaskStatus.WAITING_DEPENDENCY,
        requires_approval=False,
    )
    invite_github = OnboardingTask(
        case_id=case_id,
        task_key="INVITE_GITHUB",
        owner_type="IT",
        status=TaskStatus.WAITING_DEPENDENCY,
        requires_approval=True,
    )
    order_laptop = OnboardingTask(
        case_id=case_id,
        task_key="ORDER_LAPTOP",
        owner_type="IT",
        status=TaskStatus.WAITING_APPROVAL,
        requires_approval=True,
    )

    tasks = [create_gw, invite_slack, invite_github, order_laptop]
    dependencies = [
        TaskDependency(
            task_id=invite_slack.id,
            depends_on_task_id=create_gw.id,
        ),
        TaskDependency(
            task_id=invite_github.id,
            depends_on_task_id=create_gw.id,
        ),
    ]
    return tasks, dependencies


class OnboardingService:
    """Application service coordinating the onboarding intake lifecycle."""

    _case_counter = itertools.count(1)
    _CASE_REF_REGEX = re.compile(r"ONB-\d{4}-\d+", re.IGNORECASE)

    def __init__(
        self,
        repository: CaseRepository | None = None,
        extractor: CandidateExtractor | None = None,
    ) -> None:
        self.repository: CaseRepository = repository or InMemoryCaseRepository()
        self.extractor: CandidateExtractor = extractor or RuleBasedCandidateExtractor()

    def _next_case_number(self) -> str:
        num = next(self._case_counter)
        year = datetime.now(UTC).year
        return f"ONB-{year}-{num:04d}"

    def process_inbound_email(self, email: InboundEmail) -> InboundProcessingResult:
        """Process inbound email, handle follow-ups, and manage DAG state."""
        # 1. Thread / Case Correlation
        case_ref_match = self._CASE_REF_REGEX.search(email.subject + " " + email.body)
        existing_case: OnboardingCase | None = None
        if case_ref_match:
            existing_case = self.repository.find_case_by_number(
                case_ref_match.group(0).upper()
            )

        if existing_case is not None:
            return self._handle_follow_up_email(existing_case, email)

        return self._handle_new_intake_email(email)

    def _handle_new_intake_email(self, email: InboundEmail) -> InboundProcessingResult:
        extraction = self.extractor.extract(email)
        case_number = self._next_case_number()

        case = OnboardingCase(
            case_number=case_number,
            status=CaseStatus.DRAFT,
            candidate_data=extraction.candidate_data,
            missing_fields=list(extraction.missing_fields),
        )

        audit_events: list[AuditEvent] = []
        creation_event = AuditEvent(
            case_id=case.id,
            event_type="CASE_CREATED",
            actor_type="USER",
            actor_id=str(email.sender),
            payload={
                "subject": email.subject,
                "case_number": case.case_number,
                "extracted_intent": extraction.intent.value,
            },
        )
        audit_events.append(creation_event)

        if extraction.security_flags:
            sec_event = AuditEvent(
                case_id=case.id,
                event_type="SECURITY_FLAG_DETECTED",
                actor_type="SYSTEM",
                actor_id="sanitizer",
                payload={"flags": extraction.security_flags},
            )
            audit_events.append(sec_event)

        if case.missing_fields:
            # Transition to INFORMATION_GATHERING
            case.status = CaseStatus.INFORMATION_GATHERING
            case.updated_at = datetime.now(UTC)

            transition_event = AuditEvent(
                case_id=case.id,
                event_type="STATUS_TRANSITION",
                actor_type="SYSTEM",
                actor_id="policy_gate",
                payload={
                    "from_status": CaseStatus.DRAFT.value,
                    "to_status": CaseStatus.INFORMATION_GATHERING.value,
                    "missing_fields": case.missing_fields,
                },
            )
            audit_events.append(transition_event)

            candidate_label = case.candidate_data.full_name or "New Hire"
            fields_list = "\n".join(f"- {field}" for field in case.missing_fields)
            body = (
                f"Hello,\n\n"
                f"We received your request to onboard {candidate_label} "
                f"[{case.case_number}].\n\n"
                f"To proceed with provisioning, we still require the following:\n"
                f"{fields_list}\n\n"
                f"Please reply to this email with these details.\n\n"
                f"Best regards,\n"
                f"Onboarding Coordination Agent"
            )

            outbound_email = OutboundEmail(
                recipient=email.sender,
                subject=(
                    f"Clarification needed: Onboarding {candidate_label} "
                    f"[{case.case_number}]"
                ),
                body=body,
                in_reply_to=email.message_id,
            )

            clarification_event = AuditEvent(
                case_id=case.id,
                event_type="CLARIFICATION_REQUESTED",
                actor_type="AGENT",
                actor_id="onboarding_agent",
                payload={
                    "recipient": str(email.sender),
                    "missing_fields": case.missing_fields,
                },
            )
            audit_events.append(clarification_event)

            self.repository.save_case(case)
            for evt in audit_events:
                self.repository.save_audit_event(evt)

            return InboundProcessingResult(
                case=case,
                extraction=extraction,
                audit_events=audit_events,
                outbound_email=outbound_email,
            )

        # Complete fields on initial intake -> atomically transition to PROVISIONING
        return self._transition_to_provisioning(
            case=case,
            extraction=extraction,
            sender=email.sender,
            in_reply_to=email.message_id,
            initial_audit_events=audit_events,
            from_status=CaseStatus.DRAFT,
        )

    def _handle_follow_up_email(
        self, case: OnboardingCase, email: InboundEmail
    ) -> InboundProcessingResult:
        extraction = self.extractor.extract(email)
        audit_events: list[AuditEvent] = []

        # Merge candidate data
        merged_candidate = merge_candidate_drafts(
            case.candidate_data, extraction.candidate_data
        )
        case.candidate_data = merged_candidate
        case.missing_fields = compute_missing_fields(merged_candidate)
        case.updated_at = datetime.now(UTC)

        update_event = AuditEvent(
            case_id=case.id,
            event_type="CANDIDATE_DATA_UPDATED",
            actor_type="USER",
            actor_id=str(email.sender),
            payload={
                "provided_fields": [
                    k
                    for k, v in extraction.candidate_data.model_dump().items()
                    if v is not None
                ],
                "remaining_missing": case.missing_fields,
            },
        )
        audit_events.append(update_event)

        if extraction.security_flags:
            sec_event = AuditEvent(
                case_id=case.id,
                event_type="SECURITY_FLAG_DETECTED",
                actor_type="SYSTEM",
                actor_id="sanitizer",
                payload={"flags": extraction.security_flags},
            )
            audit_events.append(sec_event)

        if case.missing_fields:
            # Still incomplete: remain in INFORMATION_GATHERING
            candidate_label = case.candidate_data.full_name or "New Hire"
            fields_list = "\n".join(f"- {field}" for field in case.missing_fields)
            body = (
                f"Hello,\n\n"
                f"Thank you for the update on {candidate_label} "
                f"[{case.case_number}].\n\n"
                f"We still require the following information before provisioning:\n"
                f"{fields_list}\n\n"
                f"Please reply with these remaining details.\n\n"
                f"Best regards,\n"
                f"Onboarding Coordination Agent"
            )
            outbound_email = OutboundEmail(
                recipient=email.sender,
                subject=(
                    f"Clarification needed: Onboarding {candidate_label} "
                    f"[{case.case_number}]"
                ),
                body=body,
                in_reply_to=email.message_id,
            )

            clarification_event = AuditEvent(
                case_id=case.id,
                event_type="CLARIFICATION_REQUESTED",
                actor_type="AGENT",
                actor_id="onboarding_agent",
                payload={
                    "recipient": str(email.sender),
                    "missing_fields": case.missing_fields,
                },
            )
            audit_events.append(clarification_event)

            self.repository.save_case(case)
            for evt in audit_events:
                self.repository.save_audit_event(evt)

            return InboundProcessingResult(
                case=case,
                extraction=extraction,
                audit_events=audit_events,
                outbound_email=outbound_email,
            )

        # All fields now present: transition from INFORMATION_GATHERING to PROVISIONING
        return self._transition_to_provisioning(
            case=case,
            extraction=extraction,
            sender=email.sender,
            in_reply_to=email.message_id,
            initial_audit_events=audit_events,
            from_status=case.status,
        )

    def _transition_to_provisioning(
        self,
        case: OnboardingCase,
        extraction: NluExtractionResult,
        sender: EmailStr,
        in_reply_to: str | None,
        initial_audit_events: list[AuditEvent],
        from_status: CaseStatus,
    ) -> InboundProcessingResult:
        """Atomically materialize Employee, transition case, and initialize Task DAG."""
        audit_events = list(initial_audit_events)

        c = case.candidate_data
        assert c.personal_email is not None
        assert c.full_name is not None
        assert c.role_title is not None
        assert c.department is not None
        assert c.start_date is not None

        # 1. Materialize Employee
        employee = Employee(
            personal_email=c.personal_email,
            full_name=c.full_name,
            role_title=c.role_title,
            department=c.department,
            start_date=c.start_date,
        )
        case.employee_id = employee.id
        case.status = CaseStatus.PROVISIONING
        case.updated_at = datetime.now(UTC)

        emp_event = AuditEvent(
            case_id=case.id,
            event_type="EMPLOYEE_MATERIALIZED",
            actor_type="SYSTEM",
            actor_id="state_machine",
            payload={
                "employee_id": str(employee.id),
                "full_name": employee.full_name,
                "personal_email": employee.personal_email,
                "role_title": employee.role_title,
                "department": employee.department,
            },
        )
        audit_events.append(emp_event)

        trans_event = AuditEvent(
            case_id=case.id,
            event_type="STATUS_TRANSITION",
            actor_type="SYSTEM",
            actor_id="policy_gate",
            payload={
                "from_status": from_status.value,
                "to_status": CaseStatus.PROVISIONING.value,
                "employee_id": str(employee.id),
            },
        )
        audit_events.append(trans_event)

        # 2. Instantiate Task DAG
        tasks, dependencies = build_default_task_dag(case.id)

        dag_event = AuditEvent(
            case_id=case.id,
            event_type="TASK_DAG_INITIALIZED",
            actor_type="SYSTEM",
            actor_id="task_engine",
            payload={
                "tasks": [t.task_key for t in tasks],
                "dependency_count": len(dependencies),
            },
        )
        audit_events.append(dag_event)

        # 3. Draft outbound email
        candidate_label = employee.full_name
        body = (
            f"Hello,\n\n"
            f"All required information for {candidate_label} "
            f"[{case.case_number}] has been received.\n"
            f"The permanent employee record has been created and the onboarding "
            f"task workflow is now in active provisioning.\n\n"
            f"Best regards,\n"
            f"Onboarding Coordination Agent"
        )
        outbound_email = OutboundEmail(
            recipient=sender,
            subject=f"Onboarding Started: {candidate_label} [{case.case_number}]",
            body=body,
            in_reply_to=in_reply_to,
        )

        notif_event = AuditEvent(
            case_id=case.id,
            event_type="PROVISIONING_INITIATED",
            actor_type="AGENT",
            actor_id="onboarding_agent",
            payload={"recipient": str(sender)},
        )
        audit_events.append(notif_event)

        # 4. Atomic commit across all entities
        self.repository.commit_provisioning_transition(
            case=case,
            employee=employee,
            tasks=tasks,
            dependencies=dependencies,
            audit_events=audit_events,
        )

        return InboundProcessingResult(
            case=case,
            extraction=extraction,
            employee=employee,
            tasks=tasks,
            task_dependencies=dependencies,
            audit_events=audit_events,
            outbound_email=outbound_email,
        )

    def trigger_start_date_arrival(
        self, case_id: UUID, current_date: date | None = None
    ) -> list[OutboundEmail]:
        """Advance case to ACTIVE_ONBOARDING and draft Day 1 welcome emails."""
        case = self.repository.get_case(case_id)
        if case is None or case.status != CaseStatus.READY_FOR_FIRST_DAY:
            return []

        if case.employee_id is None:
            return []

        employee = self.repository.get_employee(case.employee_id)
        if employee is None:
            return []

        if current_date is not None and current_date < employee.start_date:
            return []

        case.status = CaseStatus.ACTIVE_ONBOARDING
        case.updated_at = datetime.now(UTC)

        audit_events: list[AuditEvent] = []
        audit_events.append(
            AuditEvent(
                case_id=case.id,
                event_type="STATUS_TRANSITION",
                actor_type="SYSTEM",
                actor_id="service",
                payload={
                    "from_status": CaseStatus.READY_FOR_FIRST_DAY.value,
                    "to_status": CaseStatus.ACTIVE_ONBOARDING.value,
                },
            )
        )

        # 1. Draft Day 1 welcome email to personal address
        personal_body = (
            f"Hello {employee.full_name},\n\n"
            f"Welcome to your first day! We are thrilled to have you join the team.\n"
            f"Your corporate Google account ({employee.company_email or 'pending'}) "
            f"and Slack workspace access have been configured.\n\n"
            f"Please check your inbox and Slack to begin your day 1 orientation.\n\n"
            f"Best regards,\n"
            f"Onboarding Coordination Agent"
        )
        email_personal = OutboundEmail(
            recipient=employee.personal_email,
            subject=(
                f"Welcome to the team, {employee.full_name}! (Day 1 Guide) "
                f"[{case.case_number}]"
            ),
            body=personal_body,
        )

        # 2. Draft Day 1 welcome email to company address
        company_recipient = employee.company_email or employee.personal_email
        company_body = (
            f"Hello {employee.full_name},\n\n"
            f"Welcome to your first day! Your company account is active.\n"
            f"Please review your introductory checklist: complete required "
            f"security training and schedule your first 1-on-1 with your manager.\n\n"
            f"Best regards,\n"
            f"Onboarding Coordination Agent"
        )
        email_company = OutboundEmail(
            recipient=company_recipient,
            subject=(
                f"Welcome to your first day, {employee.full_name}! [{case.case_number}]"
            ),
            body=company_body,
        )

        audit_events.append(
            AuditEvent(
                case_id=case.id,
                event_type="DAY_ONE_WELCOME_SENT",
                actor_type="AGENT",
                actor_id="service",
                payload={
                    "personal_email": employee.personal_email,
                    "company_email": employee.company_email,
                },
            )
        )

        # 3. Create first-week tasks
        task_training = OnboardingTask(
            case_id=case.id,
            task_key="COMPLETE_SECURITY_TRAINING",
            owner_type="EMPLOYEE",
            status=TaskStatus.READY,
        )
        task_1on1 = OnboardingTask(
            case_id=case.id,
            task_key="SCHEDULE_1ON1_MANAGER",
            owner_type="MANAGER",
            status=TaskStatus.READY,
        )

        self.repository.save_case(case)
        self.repository.save_tasks([task_training, task_1on1])
        for event in audit_events:
            self.repository.save_audit_event(event)

        return [email_personal, email_company]

    def complete_first_week_tasks(self, case_id: UUID) -> OnboardingCase | None:
        """Mark first-week tasks completed and transition case to COMPLETED."""
        case = self.repository.get_case(case_id)
        if case is None or case.status != CaseStatus.ACTIVE_ONBOARDING:
            return case

        tasks = self.repository.get_tasks_for_case(case_id)
        for task in tasks:
            if task.status in (TaskStatus.READY, TaskStatus.IN_PROGRESS):
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC)

        all_terminal = all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in tasks
        )
        if all_terminal:
            case.status = CaseStatus.COMPLETED
            case.updated_at = datetime.now(UTC)
            self.repository.save_case(case)
            self.repository.save_tasks(tasks)
            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="STATUS_TRANSITION",
                    actor_type="SYSTEM",
                    actor_id="service",
                    payload={
                        "from_status": CaseStatus.ACTIVE_ONBOARDING.value,
                        "to_status": CaseStatus.COMPLETED.value,
                    },
                )
            )
            self.repository.save_audit_event(
                AuditEvent(
                    case_id=case.id,
                    event_type="CASE_COMPLETED",
                    actor_type="AGENT",
                    actor_id="service",
                    payload={"case_number": case.case_number},
                )
            )

        return case
