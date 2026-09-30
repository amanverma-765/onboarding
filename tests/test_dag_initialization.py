from __future__ import annotations

from datetime import date

from onboarding.application.service import OnboardingService
from onboarding.domain.models import (
    CaseStatus,
    InboundEmail,
    TaskStatus,
)
from onboarding.infrastructure.repository import InMemoryCaseRepository


def test_follow_up_email_resolves_missing_fields_and_initializes_dag() -> None:
    repo = InMemoryCaseRepository()
    service = OnboardingService(repository=repo)

    # 1. Initial incomplete email
    initial_email = InboundEmail(
        sender="hr@company.internal",
        recipient="onboarding@company.internal",
        subject="Onboard Sarah Chen",
        body=(
            "Please onboard Sarah Chen as a Senior Backend Engineer "
            "in Engineering starting 2026-10-15."
        ),
        message_id="msg-init-01",
    )
    res1 = service.process_inbound_email(initial_email)
    case_num = res1.case.case_number

    assert res1.case.status == CaseStatus.INFORMATION_GATHERING
    assert "personal_email" in res1.case.missing_fields
    assert "manager_email" in res1.case.missing_fields
    assert res1.case.employee_id is None
    assert len(repo.get_tasks_for_case(res1.case.id)) == 0

    # 2. Follow-up email providing the remaining missing details
    follow_up = InboundEmail(
        sender="hr@company.internal",
        recipient="onboarding@company.internal",
        subject=f"Re: Clarification needed: Onboarding Sarah Chen [{case_num}]",
        body=(
            "Here are the remaining details for Sarah:\n"
            "Personal Email: sarah.chen@gmail.com\n"
            "Manager Email: mike@company.internal\n"
        ),
        message_id="msg-followup-02",
    )
    res2 = service.process_inbound_email(follow_up)

    # State & Employee Verification
    assert res2.case.status == CaseStatus.PROVISIONING
    assert len(res2.case.missing_fields) == 0
    assert res2.case.employee_id is not None
    assert res2.employee is not None
    assert res2.employee.id == res2.case.employee_id
    assert res2.employee.full_name == "Sarah Chen"
    assert res2.employee.personal_email == "sarah.chen@gmail.com"
    assert res2.employee.role_title == "Senior Backend Engineer"
    assert res2.employee.department == "Engineering"
    assert res2.employee.start_date == date(2026, 10, 15)

    # Repository Persistence Verification
    persisted_case = repo.get_case(res2.case.id)
    assert persisted_case is not None
    assert persisted_case.status == CaseStatus.PROVISIONING

    persisted_emp = repo.get_employee(res2.employee.id)
    assert persisted_emp is not None
    assert persisted_emp.full_name == "Sarah Chen"

    # Task DAG Verification
    tasks = repo.get_tasks_for_case(res2.case.id)
    task_keys = {t.task_key for t in tasks}
    assert task_keys == {
        "CREATE_GOOGLE_WORKSPACE",
        "INVITE_SLACK",
        "INVITE_GITHUB",
        "ORDER_LAPTOP",
    }

    gw_task = next(t for t in tasks if t.task_key == "CREATE_GOOGLE_WORKSPACE")
    slack_task = next(t for t in tasks if t.task_key == "INVITE_SLACK")
    gh_task = next(t for t in tasks if t.task_key == "INVITE_GITHUB")
    laptop_task = next(t for t in tasks if t.task_key == "ORDER_LAPTOP")

    # Tier & Approval settings
    assert gw_task.status == TaskStatus.READY
    assert not gw_task.requires_approval

    assert slack_task.status == TaskStatus.WAITING_DEPENDENCY
    assert not slack_task.requires_approval

    assert gh_task.status == TaskStatus.WAITING_DEPENDENCY
    assert gh_task.requires_approval

    assert laptop_task.status == TaskStatus.WAITING_APPROVAL
    assert laptop_task.requires_approval

    # Dependency Edge Verification
    deps = repo.get_task_dependencies_for_case(res2.case.id)
    assert len(deps) == 2

    slack_deps = [d for d in deps if d.task_id == slack_task.id]
    assert len(slack_deps) == 1
    assert slack_deps[0].depends_on_task_id == gw_task.id

    gh_deps = [d for d in deps if d.task_id == gh_task.id]
    assert len(gh_deps) == 1
    assert gh_deps[0].depends_on_task_id == gw_task.id

    # Audit Trail Verification
    audit_events = repo.get_audit_events(res2.case.id)
    event_types = [e.event_type for e in audit_events]
    assert "CANDIDATE_DATA_UPDATED" in event_types
    assert "EMPLOYEE_MATERIALIZED" in event_types
    assert "STATUS_TRANSITION" in event_types
    assert "TASK_DAG_INITIALIZED" in event_types
    assert "PROVISIONING_INITIATED" in event_types


def test_follow_up_with_partial_fields_keeps_case_in_information_gathering() -> None:
    repo = InMemoryCaseRepository()
    service = OnboardingService(repository=repo)

    # Initial intake
    initial_email = InboundEmail(
        sender="hr@company.internal",
        recipient="onboarding@company.internal",
        subject="Onboard Alex",
        body="Please onboard Alex in Marketing starting 2026-11-01.",
        message_id="msg-p-01",
    )
    res1 = service.process_inbound_email(initial_email)
    case_num = res1.case.case_number

    # Partial follow-up providing only personal email
    follow_up = InboundEmail(
        sender="hr@company.internal",
        recipient="onboarding@company.internal",
        subject=f"Details for [{case_num}]",
        body="Personal Email: alex@gmail.com",
        message_id="msg-p-02",
    )
    res2 = service.process_inbound_email(follow_up)

    assert res2.case.status == CaseStatus.INFORMATION_GATHERING
    assert res2.case.candidate_data.personal_email == "alex@gmail.com"
    # Role title and manager email still missing
    assert "role_title" in res2.case.missing_fields
    assert "manager_email" in res2.case.missing_fields
    assert res2.employee is None
    assert len(res2.tasks) == 0

    assert res2.outbound_email is not None
    assert "Clarification needed" in res2.outbound_email.subject
    assert "- role_title" in res2.outbound_email.body
    assert "- manager_email" in res2.outbound_email.body
