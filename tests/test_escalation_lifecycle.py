from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from onboarding.application.escalation import EscalationCoordinator
from onboarding.application.service import OnboardingService
from onboarding.application.task_engine import TaskExecutionEngine
from onboarding.domain.models import (
    ApprovalStatus,
    CaseStatus,
    InboundEmail,
    TaskStatus,
)
from onboarding.infrastructure.mock_adapters import (
    MockGoogleWorkspaceAdapter,
    MockSlackAdapter,
)
from onboarding.infrastructure.repository import InMemoryCaseRepository


def test_approval_escalation_schedule_24h_48h_72h() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=MockGoogleWorkspaceAdapter(),
            slack_port=MockSlackAdapter(),
        )
        escalation = EscalationCoordinator(repository=repo)

        email = InboundEmail(
            sender="hr@company.internal",
            recipient="onboarding@company.internal",
            subject="Onboard Sarah Chen",
            body=(
                "Name: Sarah Chen\n"
                "Personal Email: sarah.chen@gmail.com\n"
                "Role: Senior Backend Engineer\n"
                "Department: Engineering\n"
                "Manager: mike@company.internal\n"
                "Start Date: 2026-10-15\n"
            ),
            message_id="msg-esc-1",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        await engine.execute_autonomous_tasks(case_id)
        pending = engine.create_pending_approval_requests(case_id)
        req, _ = pending[0]

        start_time = req.requested_at

        # 1. Check at T + 10h -> No reminders due yet
        emails_10h = escalation.evaluate_pending_approvals(
            current_time=start_time + timedelta(hours=10)
        )
        assert len(emails_10h) == 0

        # 2. Check at T + 25h -> Gentle reminder to manager
        emails_25h = escalation.evaluate_pending_approvals(
            current_time=start_time + timedelta(hours=25)
        )
        assert len(emails_25h) >= 1
        gentle_email = emails_25h[0]
        assert gentle_email.recipient == "mike@company.internal"
        assert "[REMINDER]" in gentle_email.subject
        assert len(gentle_email.cc) == 0

        # Idempotency check: calling again at T + 26h should not re-send 24h reminder
        emails_26h = escalation.evaluate_pending_approvals(
            current_time=start_time + timedelta(hours=26)
        )
        assert len(emails_26h) == 0

        # 3. Check at T + 50h -> Urgent reminder with HR CC'd
        emails_50h = escalation.evaluate_pending_approvals(
            current_time=start_time + timedelta(hours=50)
        )
        assert len(emails_50h) >= 1
        urgent_email = emails_50h[0]
        assert urgent_email.recipient == "mike@company.internal"
        assert "hr@company.internal" in [str(c) for c in urgent_email.cc]
        assert "[URGENT REMINDER]" in urgent_email.subject

        # 4. Check at T + 75h -> Escalation alert to IT/HR admin & status ESCALATED
        emails_75h = escalation.evaluate_pending_approvals(
            current_time=start_time + timedelta(hours=75)
        )
        assert len(emails_75h) >= 1
        admin_email = emails_75h[0]
        assert admin_email.recipient == "it-admin@company.internal"
        assert "[ESCALATION]" in admin_email.subject

        updated_req = repo.get_approval_request_for_task(req.task_id)
        assert updated_req is not None
        assert updated_req.status == ApprovalStatus.ESCALATED

        # Audit events verified
        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "APPROVAL_REMINDER_SENT" in event_types
        assert "APPROVAL_ESCALATED" in event_types

    asyncio.run(run())


def test_start_date_safety_override_at_24h_before_start() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=MockGoogleWorkspaceAdapter(),
            slack_port=MockSlackAdapter(),
        )
        escalation = EscalationCoordinator(repository=repo)

        # Start date is 2026-10-15
        email = InboundEmail(
            sender="hr@company.internal",
            recipient="onboarding@company.internal",
            subject="Onboard Sarah Chen",
            body=(
                "Name: Sarah Chen\n"
                "Personal Email: sarah.chen@gmail.com\n"
                "Role: Senior Backend Engineer\n"
                "Department: Engineering\n"
                "Manager: mike@company.internal\n"
                "Start Date: 2026-10-15\n"
            ),
            message_id="msg-override-1",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        # Autonomous tasks complete Google Workspace + Slack
        await engine.execute_autonomous_tasks(case_id)
        engine.create_pending_approval_requests(case_id)

        # Current time is 3 days before start: 2026-10-12
        before_cutoff = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
        assert not escalation.evaluate_start_date_safety_override(
            case_id, current_time=before_cutoff
        )
        assert repo.get_case(case_id).status == CaseStatus.PROVISIONING  # type: ignore[union-attr]

        # Current time is Start Date - 12 hours (e.g. 2026-10-14 14:00 UTC)
        cutoff_time = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
        triggered = escalation.evaluate_start_date_safety_override(
            case_id, current_time=cutoff_time
        )
        assert triggered is True

        # Case advances to READY_FOR_FIRST_DAY so employee can start
        case_after = repo.get_case(case_id)
        assert case_after is not None
        assert case_after.status == CaseStatus.READY_FOR_FIRST_DAY

        # Unapproved Tier 2 tasks are marked SKIPPED (paused)
        tasks = repo.get_tasks_for_case(case_id)
        tier2_tasks = [t for t in tasks if t.requires_approval]
        for t in tier2_tasks:
            assert t.status == TaskStatus.SKIPPED

        # Core Tier 1 tools remained completed
        tier1_tasks = [t for t in tasks if not t.requires_approval]
        for t in tier1_tasks:
            assert t.status == TaskStatus.COMPLETED

        # Audit events verified
        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "START_DATE_SAFETY_OVERRIDE_TRIGGERED" in event_types
        assert "STATUS_TRANSITION" in event_types

    asyncio.run(run())
