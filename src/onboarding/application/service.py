from __future__ import annotations

import itertools
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from onboarding.domain.extractor import (
    CandidateExtractor,
    NluExtractionResult,
    RuleBasedCandidateExtractor,
)
from onboarding.domain.models import (
    AuditEvent,
    CaseStatus,
    InboundEmail,
    OnboardingCase,
    OutboundEmail,
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
    audit_events: list[AuditEvent] = Field(default_factory=list)
    outbound_email: OutboundEmail | None = None


class OnboardingService:
    """Application service coordinating the onboarding intake lifecycle."""

    _case_counter = itertools.count(1)

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
        """Process inbound email, mutate state, and draft outbound replies."""
        extraction = self.extractor.extract(email)
        case_number = self._next_case_number()

        case = OnboardingCase(
            case_number=case_number,
            status=CaseStatus.DRAFT,
            candidate_data=extraction.candidate_data,
            missing_fields=list(extraction.missing_fields),
        )

        audit_events: list[AuditEvent] = []

        # 1. Audit case creation
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

        # 2. Audit any security alerts triggered by prompt injection pre-scanner
        if extraction.security_flags:
            sec_event = AuditEvent(
                case_id=case.id,
                event_type="SECURITY_FLAG_DETECTED",
                actor_type="SYSTEM",
                actor_id="sanitizer",
                payload={"flags": extraction.security_flags},
            )
            audit_events.append(sec_event)

        outbound_email: OutboundEmail | None = None

        # 3. Deterministic state transition gate
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

            # Draft outbound clarification email
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

        else:
            # Complete fields: transition directly to PROVISIONING
            case.status = CaseStatus.PROVISIONING
            case.updated_at = datetime.now(UTC)

            transition_event = AuditEvent(
                case_id=case.id,
                event_type="STATUS_TRANSITION",
                actor_type="SYSTEM",
                actor_id="policy_gate",
                payload={
                    "from_status": CaseStatus.DRAFT.value,
                    "to_status": CaseStatus.PROVISIONING.value,
                },
            )
            audit_events.append(transition_event)

            candidate_label = case.candidate_data.full_name or "New Hire"
            body = (
                f"Hello,\n\n"
                f"All required information for {candidate_label} "
                f"[{case.case_number}] has been received.\n"
                f"The onboarding workflow has transitioned to active provisioning.\n\n"
                f"Best regards,\n"
                f"Onboarding Coordination Agent"
            )

            outbound_email = OutboundEmail(
                recipient=email.sender,
                subject=f"Onboarding Started: {candidate_label} [{case.case_number}]",
                body=body,
                in_reply_to=email.message_id,
            )

            notif_event = AuditEvent(
                case_id=case.id,
                event_type="PROVISIONING_INITIATED",
                actor_type="AGENT",
                actor_id="onboarding_agent",
                payload={"recipient": str(email.sender)},
            )
            audit_events.append(notif_event)

        # 4. Persist to repository
        self.repository.save_case(case)
        for evt in audit_events:
            self.repository.save_audit_event(evt)

        return InboundProcessingResult(
            case=case,
            extraction=extraction,
            audit_events=audit_events,
            outbound_email=outbound_email,
        )
