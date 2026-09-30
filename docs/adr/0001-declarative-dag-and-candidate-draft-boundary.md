# Declarative Postgres DAG and CandidateDraft Separation

Transient candidate information is collected inside `OnboardingCase.candidate_data` during information gathering, and permanent `Employee` entities are only created upon transitioning to active provisioning. Task dependencies are modeled as a declarative directed acyclic graph in PostgreSQL join tables (`task_dependencies`) rather than an external workflow engine (Temporal) or hardcoded procedural code.

## Context
When HR or a hiring manager emails the agent to initiate onboarding, information is often incomplete (missing personal email, start date, or manager). Creating an `Employee` row immediately leads to nullable foreign keys and dirty records in the employee directory if an onboarding request is abandoned or invalid. Furthermore, provisioning tasks (Google Workspace, Slack, GitHub, laptop ordering) have interdependent execution prerequisites that must be unblocked deterministically over multiple days.

## Decision
1. **CandidateDraft Aggregate Boundary**: Candidate attributes remain stored as JSON / value objects in `OnboardingCase` until all mandatory validation rules pass. Transitioning to `PROVISIONING` creates the verified `Employee` record.
2. **Postgres-backed Declarative DAG**: Tasks and their prerequisites are represented as relational records (`onboarding_tasks` and `task_dependencies`). Task completion emits internal events that re-evaluate unblocked tasks via an atomic transaction.
3. **State Snapshot + Audit Trail**: State is maintained directly on mutable entities for simple querying, with an immutable append-only `audit_events` table for regulatory compliance and auditability.

## Consequences
- The employee directory remains pristine with zero partial or abandoned drafts.
- Task execution is resilient across restarts without running external Temporal or Redis infrastructure.
- Multi-day workflows are easily queried with plain SQL and inspected in standard database tooling.
