from __future__ import annotations

import asyncio

from onboarding.application.service import OnboardingService
from onboarding.application.task_engine import TaskExecutionEngine
from onboarding.domain.models import (
    ApprovalStatus,
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


def test_verp_approval_generation_and_explicit_approval_execution() -> None:
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

        # Run Tier 1 tasks so Google Workspace and Slack complete,
        # which unblocks INVITE_GITHUB into WAITING_APPROVAL
        await engine.execute_autonomous_tasks(case_id)

        # Generate pending approval requests for Tier 2 tasks
        pending_requests = engine.create_pending_approval_requests(case_id)
        assert len(pending_requests) == 2  # INVITE_GITHUB and ORDER_LAPTOP

        gh_req, gh_email = next(
            (r, em)
            for r, em in pending_requests
            if repo.get_tasks_for_case(case_id)[
                [t.id for t in repo.get_tasks_for_case(case_id)].index(r.task_id)
            ].task_key
            == "INVITE_GITHUB"
        )

        assert gh_req.status == ApprovalStatus.PENDING
        assert gh_req.approver_email == "mike@company.internal"
        assert gh_email.recipient == "mike@company.internal"
        assert "app+" in str(gh_email.reply_to)

        # Inbound email reply from manager with explicit approval
        approval_reply = InboundEmail(
            sender="mike@company.internal",
            recipient=gh_email.reply_to,  # type: ignore[arg-type]
            subject="Re: Approval Required: INVITE_GITHUB for Sarah Chen",
            body="Looks great, approved! Go ahead and invite to GitHub.",
            message_id="reply-gh-01",
        )

        reply_result = await engine.handle_approval_reply(approval_reply)

        assert reply_result.status == "APPROVED"
        assert reply_result.approval_request is not None
        assert reply_result.approval_request.status == ApprovalStatus.APPROVED

        tasks = repo.get_tasks_for_case(case_id)
        gh_task = next(t for t in tasks if t.task_key == "INVITE_GITHUB")
        assert gh_task.status == TaskStatus.COMPLETED

        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "APPROVAL_GRANTED" in event_types

    asyncio.run(run())


def test_explicit_rejection_marks_task_and_downstream_skipped() -> None:
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
            subject="Onboard Sarah Chen",
            body=(
                "Name: Sarah Chen\n"
                "Personal Email: sarah.chen@gmail.com\n"
                "Role: Senior Backend Engineer\n"
                "Department: Engineering\n"
                "Manager: mike@company.internal\n"
                "Start Date: 2026-10-15\n"
            ),
            message_id="msg-2",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        await engine.execute_autonomous_tasks(case_id)
        pending_requests = engine.create_pending_approval_requests(case_id)

        laptop_req, laptop_email = next(
            (r, em)
            for r, em in pending_requests
            if repo.get_tasks_for_case(case_id)[
                [t.id for t in repo.get_tasks_for_case(case_id)].index(r.task_id)
            ].task_key
            == "ORDER_LAPTOP"
        )

        # Inbound rejection email
        reject_reply = InboundEmail(
            sender="mike@company.internal",
            recipient=laptop_email.reply_to,  # type: ignore[arg-type]
            subject="Re: Approval Required: ORDER_LAPTOP for Sarah Chen",
            body="Reject. We already have a spare laptop locally.",
            message_id="reply-laptop-01",
        )

        reply_result = await engine.handle_approval_reply(reject_reply)

        assert reply_result.status == "REJECTED"
        assert reply_result.approval_request is not None
        assert reply_result.approval_request.status == ApprovalStatus.REJECTED

        tasks = repo.get_tasks_for_case(case_id)
        laptop_task = next(t for t in tasks if t.task_key == "ORDER_LAPTOP")
        assert laptop_task.status == TaskStatus.SKIPPED

        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "APPROVAL_REJECTED" in event_types

    asyncio.run(run())


def test_ambiguous_reply_leaves_request_pending_and_drafts_clarification() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        engine = TaskExecutionEngine(repository=repo)

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
            message_id="msg-3",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        await engine.execute_autonomous_tasks(case_id)
        pending_requests = engine.create_pending_approval_requests(case_id)
        _laptop_req, laptop_email = pending_requests[0]

        # Ambiguous reply
        ambiguous_reply = InboundEmail(
            sender="mike@company.internal",
            recipient=laptop_email.reply_to,  # type: ignore[arg-type]
            subject="Re: Approval Required",
            body="Wait, what model laptop are we ordering? Not sure about this.",
            message_id="reply-ambiguous-01",
        )

        reply_result = await engine.handle_approval_reply(ambiguous_reply)

        # Still PENDING
        assert reply_result.status == "AMBIGUOUS"
        assert reply_result.approval_request is not None
        assert reply_result.approval_request.status == ApprovalStatus.PENDING

        # Clarification email drafted
        assert reply_result.outbound_email is not None
        assert reply_result.outbound_email.recipient == "mike@company.internal"
        assert "Clarification Needed" in reply_result.outbound_email.subject
        assert "Approve" in reply_result.outbound_email.body
        assert "Reject" in reply_result.outbound_email.body

        # Audit logged
        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "APPROVAL_AMBIGUITY_DETECTED" in event_types

    asyncio.run(run())


def test_unauthorized_spoofed_approval_reply_is_rejected() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        engine = TaskExecutionEngine(repository=repo)

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
            message_id="msg-4",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        await engine.execute_autonomous_tasks(case_id)
        pending_requests = engine.create_pending_approval_requests(case_id)
        _laptop_req, laptop_email = pending_requests[0]

        # Spoofed reply from attacker
        spoofed_reply = InboundEmail(
            sender="attacker@evil.com",
            recipient=laptop_email.reply_to,  # type: ignore[arg-type]
            subject="Re: Approval Required",
            body="Approved immediately.",
            message_id="reply-spoof-01",
        )

        reply_result = await engine.handle_approval_reply(spoofed_reply)

        assert reply_result.status == "UNAUTHORIZED"
        audit_events = repo.get_audit_events(case_id)
        event_types = [e.event_type for e in audit_events]
        assert "UNAUTHORIZED_APPROVAL_ATTEMPT" in event_types

    asyncio.run(run())
