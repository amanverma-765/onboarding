# Onboarding

An event-driven, email-native workflow coordinator for automated employee onboarding and IT/HR provisioning.

## Language

**OnboardingCase**:
The lifecycle aggregate tracking an individual new hire's onboarding process from initial email inquiry to post-start verification.
_Avoid_: Ticket, OnboardingRequest, Flow

**CandidateDraft**:
The transient, unverified collection of new hire attributes collected during information gathering before a formal Employee record is created.
_Avoid_: Prospect, Applicant, Lead

**Employee**:
The permanent, verified company employee entity created once all mandatory onboarding attributes are validated.
_Avoid_: User, Staff, Worker

**OnboardingTask**:
A discrete, deterministic operational unit of work (such as account provisioning, hardware order, or checklist item) assigned to an owner (IT, HR, or Manager).
_Avoid_: Step, Action, Job

**TaskDependency**:
A directed prerequisite relationship declaring that one OnboardingTask cannot start until another OnboardingTask completes.
_Avoid_: Blocker, Precondition

**ApprovalRequest**:
An explicit authorization gate requiring human sign-off (via email reply or step-up authentication) before a privileged task executes.
_Avoid_: SignOff, PermissionGrant

**AuditEvent**:
An immutable chronological record of external inputs, agent decisions, state transitions, and tool actions.
_Avoid_: Log, HistoryRecord
