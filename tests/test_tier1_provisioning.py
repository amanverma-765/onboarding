from __future__ import annotations

import asyncio

from onboarding.application.service import OnboardingService
from onboarding.application.task_engine import TaskExecutionEngine
from onboarding.domain.models import (
    CaseStatus,
    InboundEmail,
    TaskStatus,
)
from onboarding.infrastructure.mock_adapters import (
    MockGoogleWorkspaceAdapter,
    MockSlackAdapter,
)
from onboarding.infrastructure.repository import InMemoryCaseRepository


def test_autonomous_tier1_provisioning_and_dag_propagation() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        google_mock = MockGoogleWorkspaceAdapter()
        slack_mock = MockSlackAdapter()
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=google_mock,
            slack_port=slack_mock,
        )

        # 1. Complete onboarding intake directly transitioning to PROVISIONING
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

        # Initial state of tasks before execution
        tasks_initial = repo.get_tasks_for_case(case_id)
        gw_init = next(
            t for t in tasks_initial if t.task_key == "CREATE_GOOGLE_WORKSPACE"
        )
        slack_init = next(t for t in tasks_initial if t.task_key == "INVITE_SLACK")
        gh_init = next(t for t in tasks_initial if t.task_key == "INVITE_GITHUB")
        laptop_init = next(t for t in tasks_initial if t.task_key == "ORDER_LAPTOP")

        assert gw_init.status == TaskStatus.READY
        assert slack_init.status == TaskStatus.WAITING_DEPENDENCY
        assert gh_init.status == TaskStatus.WAITING_DEPENDENCY
        assert laptop_init.status == TaskStatus.WAITING_APPROVAL

        # 2. Execute autonomous tasks
        executed = await engine.execute_autonomous_tasks(case_id)
        assert len(executed) == 2

        # Check updated tasks
        tasks_after = repo.get_tasks_for_case(case_id)
        gw_after = next(
            t for t in tasks_after if t.task_key == "CREATE_GOOGLE_WORKSPACE"
        )
        slack_after = next(t for t in tasks_after if t.task_key == "INVITE_SLACK")
        gh_after = next(t for t in tasks_after if t.task_key == "INVITE_GITHUB")
        laptop_after = next(t for t in tasks_after if t.task_key == "ORDER_LAPTOP")

        # Tier 1 tasks completed
        assert gw_after.status == TaskStatus.COMPLETED
        assert gw_after.completed_at is not None
        assert slack_after.status == TaskStatus.COMPLETED
        assert slack_after.completed_at is not None

        # Employee record updated with corporate email
        employee = repo.get_employee(result.case.employee_id)  # type: ignore[arg-type]
        assert employee is not None
        assert employee.company_email == "sarah.chen@company.internal"

        # Downstream Tier 2 task unblocked into WAITING_APPROVAL
        assert gh_after.status == TaskStatus.WAITING_APPROVAL
        assert laptop_after.status == TaskStatus.WAITING_APPROVAL

        # Audit events verified
        audit_events = repo.get_audit_events(case_id)
        completed_events = [e for e in audit_events if e.event_type == "TASK_COMPLETED"]
        assert len(completed_events) == 2
        gw_audit = next(
            e
            for e in completed_events
            if e.payload["task_key"] == "CREATE_GOOGLE_WORKSPACE"
        )
        assert gw_audit.payload["company_email"] == "sarah.chen@company.internal"

    asyncio.run(run())


def test_idempotent_reconciliation_on_existing_account_conflict() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        google_mock = MockGoogleWorkspaceAdapter()
        slack_mock = MockSlackAdapter()
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=google_mock,
            slack_port=slack_mock,
        )

        # Pre-populate existing user in Google Workspace (e.g. prior provisioned run)
        await google_mock.create_user(
            full_name="Sarah Chen",
            primary_email="sarah.chen@company.internal",
            personal_recovery_email="sarah.chen@gmail.com",
            department="Engineering",
            idempotency_key="pre-existing-key",
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

        # Execute tasks -> Google adapter will encounter conflict and reconcile
        await engine.execute_autonomous_tasks(case_id)

        tasks = repo.get_tasks_for_case(case_id)
        gw_task = next(t for t in tasks if t.task_key == "CREATE_GOOGLE_WORKSPACE")
        assert gw_task.status == TaskStatus.COMPLETED
        assert gw_task.payload.get("reconciled") is True

        audit_events = repo.get_audit_events(case_id)
        gw_audit = next(
            e
            for e in audit_events
            if e.event_type == "TASK_COMPLETED"
            and e.payload["task_key"] == "CREATE_GOOGLE_WORKSPACE"
        )
        assert gw_audit.payload["reconciled"] is True

    asyncio.run(run())


def test_transient_rate_limit_fault_and_retry_recovery() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        # Simulate 1 transient rate-limit hit before succeeding
        google_mock = MockGoogleWorkspaceAdapter(simulate_rate_limit=1)
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=google_mock,
            max_retries=2,
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
            message_id="msg-3",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        # First execution hits rate limit and schedules retry
        await engine._execute_single_task(
            result.case,
            repo.get_employee(result.case.employee_id),  # type: ignore[arg-type]
            repo.get_tasks_for_case(case_id)[0],
        )

        tasks_retry = repo.get_tasks_for_case(case_id)
        gw_task = next(
            t for t in tasks_retry if t.task_key == "CREATE_GOOGLE_WORKSPACE"
        )
        assert gw_task.status == TaskStatus.READY
        assert gw_task.retry_count == 1

        # Second execution succeeds
        await engine.execute_autonomous_tasks(case_id)
        gw_final = next(
            t
            for t in repo.get_tasks_for_case(case_id)
            if t.task_key == "CREATE_GOOGLE_WORKSPACE"
        )
        assert gw_final.status == TaskStatus.COMPLETED

    asyncio.run(run())


def test_permanent_auth_failure_marks_task_failed() -> None:
    async def run() -> None:
        repo = InMemoryCaseRepository()
        service = OnboardingService(repository=repo)
        google_mock = MockGoogleWorkspaceAdapter(simulate_auth_error=True)
        engine = TaskExecutionEngine(
            repository=repo,
            google_port=google_mock,
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
            message_id="msg-4",
        )
        result = service.process_inbound_email(email)
        case_id = result.case.id

        await engine.execute_autonomous_tasks(case_id)

        tasks = repo.get_tasks_for_case(case_id)
        gw_task = next(t for t in tasks if t.task_key == "CREATE_GOOGLE_WORKSPACE")
        assert gw_task.status == TaskStatus.FAILED
        assert "401" in (gw_task.error_message or "")

        audit_events = repo.get_audit_events(case_id)
        fail_events = [e for e in audit_events if e.event_type == "TASK_FAILED"]
        assert len(fail_events) == 1
        assert fail_events[0].payload["retryable"] is False

    asyncio.run(run())
