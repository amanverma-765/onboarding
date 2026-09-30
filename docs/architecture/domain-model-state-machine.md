# Domain Model & State Machine Design

This document specifies the core domain entities, relational PostgreSQL schema, and deterministic state transitions for the onboarding agent.

---

## 1. Domain Entities & Relational Schema

```text
┌─────────────────┐       1:N       ┌──────────────────┐
│  Employee       │ ◄───────────────┤  OnboardingCase  │
└─────────────────┘                 └─────────┬────────┘
                                              │ 1:N
                                              ├───► OnboardingTask (DAG)
                                              │        ▲  │ 1:1
                                              │        │  └──► ApprovalRequest
                                              │  task_dependencies
                                              │
                                              ├───► EmailThread (1:N)
                                              │
                                              └───► AuditEvent (1:N)
```

### Table: `employees`
Stores verified, permanent company personnel. Only created when an onboarding case moves to `PROVISIONING`.
- `id` (UUID, PK)
- `company_email` (VARCHAR, UNIQUE, NULLABLE until IT provisions Google Workspace)
- `personal_email` (VARCHAR, NOT NULL)
- `full_name` (VARCHAR, NOT NULL)
- `role_title` (VARCHAR, NOT NULL)
- `department` (VARCHAR, NOT NULL)
- `manager_id` (UUID, FK -> employees.id, NULLABLE)
- `start_date` (DATE, NOT NULL)
- `created_at` (TIMESTAMPTZ, NOT NULL)
- `updated_at` (TIMESTAMPTZ, NOT NULL)

### Table: `onboarding_cases`
Aggregate root for the onboarding process.
- `id` (UUID, PK)
- `case_number` (VARCHAR, UNIQUE, e.g. `ONB-2026-001`)
- `status` (VARCHAR, NOT NULL: `DRAFT`, `INFORMATION_GATHERING`, `PROVISIONING`, `READY_FOR_FIRST_DAY`, `ACTIVE_ONBOARDING`, `COMPLETED`, `CANCELLED`, `BLOCKED`)
- `employee_id` (UUID, FK -> employees.id, NULLABLE during draft/info gathering)
- `candidate_data` (JSONB, NOT NULL): Holds draft values for name, email, department, role, manager, start date
- `missing_fields` (TEXT[], NOT NULL): e.g. `["personal_email", "manager"]`
- `created_at` (TIMESTAMPTZ, NOT NULL)
- `updated_at` (TIMESTAMPTZ, NOT NULL)

### Table: `onboarding_tasks`
Discrete executable tasks in the onboarding workflow.
- `id` (UUID, PK)
- `case_id` (UUID, FK -> onboarding_cases.id, NOT NULL)
- `task_key` (VARCHAR, NOT NULL, e.g. `CREATE_GOOGLE_WORKSPACE`, `INVITE_GITHUB`, `ORDER_LAPTOP`)
- `owner_type` (VARCHAR, NOT NULL: `IT`, `HR`, `MANAGER`, `EMPLOYEE`)
- `status` (VARCHAR, NOT NULL: `WAITING_DEPENDENCY`, `WAITING_APPROVAL`, `READY`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `SKIPPED`)
- `requires_approval` (BOOLEAN, NOT NULL DEFAULT FALSE)
- `payload` (JSONB, NOT NULL DEFAULT '{}')
- `error_message` (TEXT, NULLABLE)
- `retry_count` (INTEGER, NOT NULL DEFAULT 0)
- `completed_at` (TIMESTAMPTZ, NULLABLE)
- `created_at` (TIMESTAMPTZ, NOT NULL)
- `updated_at` (TIMESTAMPTZ, NOT NULL)

### Table: `task_dependencies`
Self-referential DAG edges between tasks.
- `task_id` (UUID, FK -> onboarding_tasks.id, NOT NULL)
- `depends_on_task_id` (UUID, FK -> onboarding_tasks.id, NOT NULL)
- PRIMARY KEY (`task_id`, `depends_on_task_id`)

### Table: `approval_requests`
Explicit gates for privileged actions.
- `id` (UUID, PK)
- `task_id` (UUID, FK -> onboarding_tasks.id, NOT NULL, UNIQUE)
- `case_id` (UUID, FK -> onboarding_cases.id, NOT NULL)
- `approver_email` (VARCHAR, NOT NULL)
- `status` (VARCHAR, NOT NULL: `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`)
- `verp_token_hash` (VARCHAR, NOT NULL)
- `requested_at` (TIMESTAMPTZ, NOT NULL)
- `decided_at` (TIMESTAMPTZ, NULLABLE)
- `decision_reason` (TEXT, NULLABLE)

### Table: `email_threads`
Tracks email conversation context.
- `id` (UUID, PK)
- `case_id` (UUID, FK -> onboarding_cases.id, NOT NULL)
- `thread_key` (VARCHAR, NOT NULL)
- `subject` (VARCHAR, NOT NULL)
- `participant_emails` (TEXT[], NOT NULL)
- `last_message_id` (VARCHAR, NOT NULL)
- `created_at` (TIMESTAMPTZ, NOT NULL)
- `updated_at` (TIMESTAMPTZ, NOT NULL)

### Table: `audit_events`
Immutable audit log.
- `id` (UUID, PK)
- `case_id` (UUID, FK -> onboarding_cases.id, NOT NULL)
- `event_type` (VARCHAR, NOT NULL)
- `actor_type` (VARCHAR, NOT NULL: `SYSTEM`, `AGENT`, `USER`)
- `actor_id` (VARCHAR, NOT NULL)
- `payload` (JSONB, NOT NULL)
- `created_at` (TIMESTAMPTZ, NOT NULL)

---

## 2. Deterministic State Machines

### Case Lifecycle (`OnboardingCase.status`)

```text
    [Email Received]
           │
           ▼
        DRAFT
           │
           ├── Missing mandatory fields? ──► INFORMATION_GATHERING
           │                                          │
           │                                 All info provided
           │                                          │
           └──────── Complete fields? ◄───────────────┘
                       │
                       ▼
                 PROVISIONING ──(Create Employee Record)
                       │
             All pre-start tasks COMPLETED
                       │
                       ▼
              READY_FOR_FIRST_DAY
                       │
               Start date arrives
                       │
                       ▼
               ACTIVE_ONBOARDING
                       │
              First-week tasks COMPLETED
                       │
                       ▼
                   COMPLETED
```

*Cancellation & Failure*:
- Any state -> `CANCELLED`: Explicit email request from HR/Manager.
- Any state -> `BLOCKED`: Unrecoverable task failure exceeding retry threshold.

### Task Lifecycle (`OnboardingTask.status`)

```text
             [Task Generated]
                    │
                    ▼
           WAITING_DEPENDENCY
                    │
       All prerequisite tasks COMPLETED
                    │
          Requires approval?
          ├── YES ──► WAITING_APPROVAL
          │                 │
          │            Approval granted
          │                 │
          └─── NO ──────────┼───────────────┐
                            │               │
                            ▼               ▼
                          READY          SKIPPED (if rejected or inapplicable)
                            │
                            ▼
                       IN_PROGRESS
                            │
              Execution Result?
              ├── SUCCESS ──► COMPLETED ──(Triggers DAG check for dependent tasks)
              └── FAILURE ──► FAILED (Retries or alerts)
```
