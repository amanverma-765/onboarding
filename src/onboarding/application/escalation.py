from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from onboarding.domain.models import (
    ApprovalStatus,
    AuditEvent,
    CaseStatus,
    OutboundEmail,
    TaskStatus,
)
from onboarding.infrastructure.repository import CaseRepository


class EscalationCoordinator:
    """Coordinates reminder schedules and start-date safety overrides."""

    def __init__(self, repository: CaseRepository) -> None:
        self.repository = repository

    def evaluate_pending_approvals(
        self,
        current_time: datetime,
        hr_coordinator_email: str = "hr@company.internal",
        admin_email: str = "it-admin@company.internal",
    ) -> list[OutboundEmail]:
        """Dispatch T+24h, T+48h, and T+72h approval notifications."""
        pending_requests = self.repository.get_all_pending_approval_requests()
        dispatched_emails: list[OutboundEmail] = []

        for req in pending_requests:
            case = self.repository.get_case(req.case_id)
            if case is None:
                continue

            tasks = self.repository.get_tasks_for_case(case.id)
            task = next((t for t in tasks if t.id == req.task_id), None)
            if task is None:
                continue

            elapsed = current_time - req.requested_at
            candidate_label = case.candidate_data.full_name or "New Hire"

            # T + 72h: Escalation to Admin
            if (
                elapsed >= timedelta(hours=72)
                and req.status != ApprovalStatus.ESCALATED
            ):
                req.status = ApprovalStatus.ESCALATED
                self.repository.save_approval_request(req)

                audit_event = AuditEvent(
                    case_id=case.id,
                    event_type="APPROVAL_ESCALATED",
                    actor_type="SYSTEM",
                    actor_id="escalation_engine",
                    payload={
                        "task_key": task.task_key,
                        "approver_email": str(req.approver_email),
                        "elapsed_hours": elapsed.total_seconds() / 3600,
                    },
                )
                self.repository.save_audit_event(audit_event)

                escalation_email = OutboundEmail(
                    recipient=admin_email,
                    subject=(
                        f"[ESCALATION] Approval overdue: {task.task_key} for "
                        f"{candidate_label} [{case.case_number}]"
                    ),
                    body=(
                        f"Attention IT/HR Admin,\n\n"
                        f"Approval for {task.task_key} for {candidate_label} "
                        f"[{case.case_number}] has been pending for over 72 hours "
                        f"without a response from {req.approver_email}.\n\n"
                        f"Please take administrative action to review or reassign.\n\n"
                        f"Best regards,\n"
                        f"Onboarding Coordination Agent"
                    ),
                )
                dispatched_emails.append(escalation_email)

            # T + 48h: Urgent Reminder with HR CC'd
            elif (
                elapsed >= timedelta(hours=48)
                and req.reminder_count < 2
                and req.status == ApprovalStatus.PENDING
            ):
                req.reminder_count = 2
                self.repository.save_approval_request(req)

                audit_event = AuditEvent(
                    case_id=case.id,
                    event_type="APPROVAL_REMINDER_SENT",
                    actor_type="AGENT",
                    actor_id="escalation_engine",
                    payload={
                        "task_key": task.task_key,
                        "tier": 2,
                        "hours": 48,
                        "cc": hr_coordinator_email,
                    },
                )
                self.repository.save_audit_event(audit_event)

                urgent_email = OutboundEmail(
                    recipient=req.approver_email,
                    cc=[hr_coordinator_email],
                    subject=(
                        f"[URGENT REMINDER] Approval required: {task.task_key} for "
                        f"{candidate_label} [{case.case_number}]"
                    ),
                    body=(
                        f"Hello,\n\n"
                        f"This is an urgent reminder that approval for {task.task_key} "
                        f"for {candidate_label} [{case.case_number}] is pending.\n"
                        f"Please reply 'Approve' or 'Reject' as soon as possible.\n\n"
                        f"Best regards,\n"
                        f"Onboarding Coordination Agent"
                    ),
                )
                dispatched_emails.append(urgent_email)

            # T + 24h: Gentle Reminder to Approver
            elif (
                elapsed >= timedelta(hours=24)
                and req.reminder_count < 1
                and req.status == ApprovalStatus.PENDING
            ):
                req.reminder_count = 1
                self.repository.save_approval_request(req)

                audit_event = AuditEvent(
                    case_id=case.id,
                    event_type="APPROVAL_REMINDER_SENT",
                    actor_type="AGENT",
                    actor_id="escalation_engine",
                    payload={
                        "task_key": task.task_key,
                        "tier": 1,
                        "hours": 24,
                    },
                )
                self.repository.save_audit_event(audit_event)

                gentle_email = OutboundEmail(
                    recipient=req.approver_email,
                    subject=(
                        f"[REMINDER] Approval required: {task.task_key} for "
                        f"{candidate_label} [{case.case_number}]"
                    ),
                    body=(
                        f"Hello,\n\n"
                        f"A gentle reminder: approval is pending for "
                        f"{task.task_key} for {candidate_label} "
                        f"[{case.case_number}].\n"
                        f"Please reply 'Approve' or 'Reject' when convenient.\n\n"
                        f"Best regards,\n"
                        f"Onboarding Coordination Agent"
                    ),
                )
                dispatched_emails.append(gentle_email)

        return dispatched_emails

    def evaluate_start_date_safety_override(
        self, case_id: UUID, current_time: datetime
    ) -> bool:
        """Pause approvals at Start Date - 24h and advance to READY_FOR_FIRST_DAY."""
        case = self.repository.get_case(case_id)
        if case is None or case.status != CaseStatus.PROVISIONING:
            return False

        if case.employee_id is None:
            return False

        employee = self.repository.get_employee(case.employee_id)
        if employee is None:
            return False

        start_datetime = datetime.combine(employee.start_date, time.min, tzinfo=UTC)
        time_to_start = start_datetime - current_time

        if time_to_start > timedelta(hours=24):
            return False

        tasks = self.repository.get_tasks_for_case(case_id)
        tier1_completed = all(
            t.status == TaskStatus.COMPLETED for t in tasks if not t.requires_approval
        )

        if not tier1_completed:
            return False

        # Pause/Skip remaining pending Tier 2 approvals
        audit_events: list[AuditEvent] = []
        for task in tasks:
            if task.requires_approval and task.status in (
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.WAITING_DEPENDENCY,
                TaskStatus.READY,
            ):
                task.status = TaskStatus.SKIPPED
                task.updated_at = current_time
                audit_events.append(
                    AuditEvent(
                        case_id=case.id,
                        event_type="TASK_SKIPPED",
                        actor_type="SYSTEM",
                        actor_id="safety_override",
                        payload={
                            "task_key": task.task_key,
                            "reason": "Paused by Start Date - 24h safety override",
                        },
                    )
                )

        case.status = CaseStatus.READY_FOR_FIRST_DAY
        case.updated_at = current_time

        audit_events.append(
            AuditEvent(
                case_id=case.id,
                event_type="START_DATE_SAFETY_OVERRIDE_TRIGGERED",
                actor_type="SYSTEM",
                actor_id="safety_override",
                payload={
                    "case_number": case.case_number,
                    "time_to_start_hours": time_to_start.total_seconds() / 3600,
                },
            )
        )
        audit_events.append(
            AuditEvent(
                case_id=case.id,
                event_type="STATUS_TRANSITION",
                actor_type="SYSTEM",
                actor_id="safety_override",
                payload={
                    "from_status": CaseStatus.PROVISIONING.value,
                    "to_status": CaseStatus.READY_FOR_FIRST_DAY.value,
                },
            )
        )

        self.repository.save_case(case)
        self.repository.save_tasks(tasks)
        for event in audit_events:
            self.repository.save_audit_event(event)

        return True
