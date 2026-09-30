from __future__ import annotations

import asyncio
from datetime import date

from onboarding.application.service import OnboardingService
from onboarding.application.task_engine import TaskExecutionEngine
from onboarding.domain.models import (
    CaseStatus,
    InboundEmail,
    TaskStatus,
)
from onboarding.infrastructure.mock_adapters import (
    MockGitHubAdapter,
    MockGoogleWorkspaceAdapter,
    MockHardwareAdapter,
    MockSlackAdapter,
)
from onboarding.infrastructure.repository import InMemoryCaseRepository


def test_full_lifecycle_from_readiness_to_active_and_completed() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=MockGoogleWorkspaceAdapter(),
            slack_port=MockSlackAdapter(),
            github_port=MockGitHubAdapter(),
            hardware_port=MockHardwareAdapter(),
        )

        # 1. Complete onboarding intake
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
            message_id="msg-1",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id
        assert result.case.status == CaseStatus.PROVISIONING

        # 2. Execute Tier 1 tasks (Google Workspace + Slack)
        await engine.execute_autonomous_tasks(case_id)

        # 3. Create and approve Tier 2 tasks (GitHub + Hardware)
        pending_requests = engine.create_pending_approval_requests(case_id)
        assert len(pending_requests) == 2

        for _req, email_req in pending_requests:
            reply = InboundEmail(
                sender="mike@company.internal",
                recipient=email_req.reply_to,  # type: ignore[arg-type]
                subject="Re: Approval Required",
                body="Approved! Proceed with setup.",
                message_id="rep-1",
            )
            reply_res = await engine.handle_approval_reply(reply)
            assert reply_res.status == "APPROVED"

        # 4. Verify all tasks are completed and case reached READY_FOR_FIRST_DAY
        case = repo.get_case(case_id)
        assert case is not None
        assert case.status == CaseStatus.READY_FOR_FIRST_DAY

        tasks = repo.get_tasks_for_case(case_id)
        assert all(t.status == TaskStatus.COMPLETED for t in tasks)

        # 5. Start Date Arrival -> Transitions to ACTIVE_ONBOARDING
        # Check start date gate: before start date should not trigger
        early_emails = service.trigger_start_date_arrival(
            case_id, current_date=date(2026, 10, 1)
        )
        assert len(early_emails) == 0
        assert repo.get_case(case_id).status == CaseStatus.READY_FOR_FIRST_DAY  # type: ignore[union-attr]

        # Trigger on start date
        welcome_emails = service.trigger_start_date_arrival(
            case_id, current_date=date(2026, 10, 15)
        )
        assert len(welcome_emails) == 2

        personal_welcome, company_welcome = welcome_emails
        assert personal_welcome.recipient == "sarah.chen@gmail.com"
        assert "Welcome to the team" in personal_welcome.subject
        assert "sarah.chen@company.internal" in personal_welcome.body

        assert company_welcome.recipient == "sarah.chen@company.internal"
        assert "Welcome to your first day" in company_welcome.subject
        assert "security training" in company_welcome.body

        active_case = repo.get_case(case_id)
        assert active_case is not None
        assert active_case.status == CaseStatus.ACTIVE_ONBOARDING

        # 6. Verify First-week tasks generated
        active_tasks = repo.get_tasks_for_case(case_id)
        active_keys = {t.task_key for t in active_tasks}
        assert "COMPLETE_SECURITY_TRAINING" in active_keys
        assert "SCHEDULE_1ON1_MANAGER" in active_keys

        # 7. Complete first week tasks -> Case transitions to COMPLETED
        final_case = service.complete_first_week_tasks(case_id)
        assert final_case is not None
        assert final_case.status == CaseStatus.COMPLETED

        # 8. Verify Audit Trail covers entire lifecycle
        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "STATUS_TRANSITION" in event_types
        assert "DAY_ONE_WELCOME_SENT" in event_types
        assert "CASE_COMPLETED" in event_types

    asyncio.run(run())


def test_rejected_tasks_still_permit_ready_for_first_day() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=MockGoogleWorkspaceAdapter(),
            slack_port=MockSlackAdapter(),
        )

        email = InboundEmail(
            sender="hr@company.internal",
            recipient="onboarding@company.internal",
            subject="Onboard Alex",
            body=(
                "Name: Alex Smith\n"
                "Personal Email: alex.smith@gmail.com\n"
                "Role: Customer Support\n"
                "Department: Support\n"
                "Manager: mike@company.internal\n"
                "Start Date: 2026-10-15\n"
            ),
            message_id="msg-alex-1",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        await engine.execute_autonomous_tasks(case_id)

        # Reject both Tier 2 requests (GitHub access and Laptop not needed)
        pending_requests = engine.create_pending_approval_requests(case_id)
        for _req, email_req in pending_requests:
            reply = InboundEmail(
                sender="mike@company.internal",
                recipient=email_req.reply_to,  # type: ignore[arg-type]
                subject="Re: Approval Required",
                body="Reject. Not needed for this role.",
                message_id="rep-alex-rej",
            )
            await engine.handle_approval_reply(reply)

        case = repo.get_case(case_id)
        assert case is not None
        # Tasks are COMPLETED or SKIPPED -> case still reaches READY_FOR_FIRST_DAY
        assert case.status == CaseStatus.READY_FOR_FIRST_DAY

    asyncio.run(run())
