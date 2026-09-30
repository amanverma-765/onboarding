# Technical Architecture & System Specification
## Email-First Employee Onboarding Coordinator Agent

**Status**: Approved & Baselined  
**Target Runtime**: Python 3.14  
**Primary Interface**: Inbound & Outbound Email (RFC 5322)  
**Parent Wayfinder Map**: [amanverma-765/onboarding#1](https://github.com/amanverma-765/onboarding/issues/1)

---

## 1. Executive Summary & Core Product Concept

The **Email-First Onboarding Coordinator Agent** is an event-driven workflow engine designed to orchestrate enterprise employee onboarding entirely through conversational email. Managers and HR coordinators interact using natural language (e.g., *"Please onboard Sarah Chen as a backend engineer starting Oct 15"*), while the agent maintains persistent, deterministic relational state and coordinates IT and HR provisioning across SaaS platforms.

### Architectural Tenet: The Sandwich Model
```text
Email (Untrusted Interface)
       │
       ▼
LLM NLU Layer (Pydantic Schema Extraction)
       │
       ▼
Deterministic Core (PostgreSQL State Machine & Policy Gate)
       │
       ▼
Tools & Integrations (Hexagonal Ports-and-Adapters)
       │
       ▼
LLM NLG Layer (Template & Natural Language Drafting)
       │
       ▼
Email (Response to Humans)
```

The LLM is strictly confined to translation (NLU extraction and NLG drafting). It has **zero authority** to execute tools, bypass approval policies, or mutate workflow state directly.

---

## 2. High-Level Component Architecture

```text
               ┌────────────────────────────────────────────────────────┐
               │              Inbound Email Ingestion                   │
               │  Postmark Webhook (Dev) / AWS SES + S3 + SQS (Prod)   │
               └──────────────────────────┬─────────────────────────────┘
                                          │
                                          ▼
               ┌────────────────────────────────────────────────────────┐
               │           Security & Threading Pipeline                │
               │ • DKIM/DMARC SPF Check   • VERP HMAC Token Resolver    │
               │ • Delimiter Defanging    • Heuristic Pre-Scanner       │
               └──────────────────────────┬─────────────────────────────┘
                                          │
                                          ▼
               ┌────────────────────────────────────────────────────────┐
               │             NLU Intent & Entity Extraction             │
               │ Pydantic v2 Schemas: EmailIntent, CandidateExtractedData│
               └──────────────────────────┬─────────────────────────────┘
                                          │
                                          ▼
               ┌────────────────────────────────────────────────────────┐
               │              Deterministic Policy Gate                 │
               │ • Missing Fields Evaluator  • Three-Tier Policy Matrix │
               │ • VERP Token Authenticator  • Ambiguity Gate           │
               └──────────────────────────┬─────────────────────────────┘
                                          │
                                          ▼
               ┌────────────────────────────────────────────────────────┐
               │           PostgreSQL State & Task DAG Engine           │
               │ • OnboardingCase Aggregate  • OnboardingTask DAG       │
               │ • Append-Only AuditEvents   • ApprovalRequests         │
               └──────────────────────────┬─────────────────────────────┘
                                          │
                       ┌──────────────────┴──────────────────┐
                       ▼                                     ▼
      ┌─────────────────────────────────┐   ┌─────────────────────────────────┐
      │     Outbound Email Worker       │   │    SaaS Provisioning Adapters   │
      │ • Question Email (Missing Info) │   │ • Google Workspace Port         │
      │ • Approval Request (VERP Token) │   │ • Slack Port                    │
      │ • Clarification & Day 1 Welcome │   │ • GitHub Port                   │
      │ • Escalation Reminders          │   │ • Hardware Procurement Port     │
      └─────────────────────────────────┘   └─────────────────────────────────┘
```

---

## 3. Domain Model & PostgreSQL Relational Schema

### 3.1 Entity Relationship Diagram
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

### 3.2 Relational Schema Definition

#### `employees`
Stores verified, permanent company personnel. Only materialized when a case transitions to `PROVISIONING` (ADR-0001).
- `id`: UUID (Primary Key)
- `company_email`: VARCHAR (Unique, Nullable until Google Workspace provisioning)
- `personal_email`: VARCHAR (Not Null)
- `full_name`: VARCHAR (Not Null)
- `role_title`: VARCHAR (Not Null)
- `department`: VARCHAR (Not Null)
- `manager_id`: UUID (Foreign Key -> `employees.id`, Nullable)
- `start_date`: DATE (Not Null)
- `created_at`: TIMESTAMPTZ (Not Null)
- `updated_at`: TIMESTAMPTZ (Not Null)

#### `onboarding_cases`
Aggregate root tracking an individual hire's onboarding lifecycle.
- `id`: UUID (Primary Key)
- `case_number`: VARCHAR (Unique, e.g. `ONB-2026-001`)
- `status`: VARCHAR (Not Null: `DRAFT`, `INFORMATION_GATHERING`, `PROVISIONING`, `READY_FOR_FIRST_DAY`, `ACTIVE_ONBOARDING`, `COMPLETED`, `CANCELLED`, `BLOCKED`)
- `employee_id`: UUID (Foreign Key -> `employees.id`, Nullable during draft)
- `candidate_data`: JSONB (Not Null, stores draft extracted attributes)
- `missing_fields`: TEXT[] (Not Null, e.g. `["personal_email", "manager_email"]`)
- `created_at`: TIMESTAMPTZ (Not Null)
- `updated_at`: TIMESTAMPTZ (Not Null)

#### `onboarding_tasks`
Discrete tasks within the onboarding DAG.
- `id`: UUID (Primary Key)
- `case_id`: UUID (Foreign Key -> `onboarding_cases.id`, Not Null)
- `task_key`: VARCHAR (Not Null, e.g. `CREATE_GOOGLE_WORKSPACE`, `INVITE_GITHUB`)
- `owner_type`: VARCHAR (Not Null: `IT`, `HR`, `MANAGER`, `EMPLOYEE`)
- `status`: VARCHAR (Not Null: `WAITING_DEPENDENCY`, `WAITING_APPROVAL`, `READY`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `SKIPPED`)
- `requires_approval`: BOOLEAN (Not Null, Default `FALSE`)
- `payload`: JSONB (Not Null, Default `{}`)
- `error_message`: TEXT (Nullable)
- `retry_count`: INTEGER (Not Null, Default `0`)
- `completed_at`: TIMESTAMPTZ (Nullable)
- `created_at`: TIMESTAMPTZ (Not Null)
- `updated_at`: TIMESTAMPTZ (Not Null)

#### `task_dependencies`
Declarative DAG join table.
- `task_id`: UUID (Foreign Key -> `onboarding_tasks.id`, Not Null)
- `depends_on_task_id`: UUID (Foreign Key -> `onboarding_tasks.id`, Not Null)
- PRIMARY KEY (`task_id`, `depends_on_task_id`)

#### `approval_requests`
Explicit authorization gates for Tier 2 tasks.
- `id`: UUID (Primary Key)
- `task_id`: UUID (Foreign Key -> `onboarding_tasks.id`, Not Null, Unique)
- `case_id`: UUID (Foreign Key -> `onboarding_cases.id`, Not Null)
- `approver_email`: VARCHAR (Not Null)
- `status`: VARCHAR (Not Null: `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `ESCALATED`)
- `verp_token_hash`: VARCHAR (Not Null, SHA-256 hash of VERP token)
- `requested_at`: TIMESTAMPTZ (Not Null)
- `decided_at`: TIMESTAMPTZ (Nullable)
- `decision_reason`: TEXT (Nullable)

#### `email_threads`
Email conversation context and header indexing.
- `id`: UUID (Primary Key)
- `case_id`: UUID (Foreign Key -> `onboarding_cases.id`, Not Null)
- `thread_key`: VARCHAR (Not Null)
- `subject`: VARCHAR (Not Null)
- `participant_emails`: TEXT[] (Not Null)
- `last_message_id`: VARCHAR (Not Null)
- `created_at`: TIMESTAMPTZ (Not Null)
- `updated_at`: TIMESTAMPTZ (Not Null)

#### `audit_events`
Immutable compliance audit log.
- `id`: UUID (Primary Key)
- `case_id`: UUID (Foreign Key -> `onboarding_cases.id`, Not Null)
- `event_type`: VARCHAR (Not Null)
- `actor_type`: VARCHAR (Not Null: `SYSTEM`, `AGENT`, `USER`)
- `actor_id`: VARCHAR (Not Null)
- `payload`: JSONB (Not Null)
- `created_at`: TIMESTAMPTZ (Not Null)

---

## 4. Two-Tier Lifecycle State Machines

### 4.1 Case Lifecycle (`OnboardingCase.status`)

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
                 PROVISIONING ──(Create Permanent Employee Record)
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

### 4.2 Task Lifecycle (`OnboardingTask.status`)

```text
             [Task Activated in Graph]
                         │
                         ▼
                WAITING_DEPENDENCY
                         │
           All prerequisites COMPLETED
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
                  ├── SUCCESS ──► COMPLETED ──(Evaluates DAG for dependent tasks)
                  └── FAILURE ──► FAILED (Retries or alerts)
```

---

## 5. Inbound Email Ingestion & Anti-Spoofing Architecture

### 5.1 Dual-Adapter Ingestion Strategy
- **Development / Staging**: Postmark Inbound Webhook adapter (instant setup, pre-parsed JSON, native stripped reply text).
- **Production**: AWS SES → Amazon S3 → SQS queue (cost-efficient, preserves raw RFC 5322 MIME, parsed with Python 3.14 `email.message.EmailMessage` using `email.policy.default`).
- *IMAP IDLE is prohibited* due to NAT socket dropouts and authentication deprecation.

### 5.2 Three-Tier Thread Correlation Hierarchy
1. **Tier 1 (VERP Mailbox Token)**: Matches `Reply-To` address containing cryptographic token: `app+<token>@domain.com`.
2. **Tier 2 (RFC 5322 Headers)**: Matches incoming `In-Reply-To` and `References` against outbound message IDs indexed in PostgreSQL.
3. **Tier 3 (Subject Regex Token)**: Fallback scan for `\[(?:Case\s*#?|Ref:\s*)(ONB-[A-Z0-9]+)\]`.

### 5.3 Four-Layer Anti-Spoofing Defense
1. **Protocol Verification**: SPF, DKIM, and DMARC alignment verified via RFC 8601 `Authentication-Results`.
2. **Cryptographic VERP Tokens**: 41-character HMAC-SHA256 token encoding `case_id`, `action_id`, `manager_email`, `timestamp`, and `nonce`:
   ```text
   app+O849-A02-68e8f0-8f1a-e4b27a90f182c3d5@company.internal
   ```
3. **Atomic State Transitions**: Single-use atomic updates (`UPDATE approval_requests SET status='APPROVED' WHERE status='PENDING'`) prevent replay attacks.
4. **Risk-Tiered Step-Up**: Tier 3 actions (production credentials, banking) mandate authenticated enterprise SSO magic links.

---

## 6. NLU Extraction Schemas & Prompt Guardrails

### 6.1 Pydantic v2 Extraction Contracts
```python
class EmailIntent(StrEnum):
    START_ONBOARDING = "START_ONBOARDING"
    PROVIDE_INFO = "PROVIDE_INFO"
    APPROVE_ACTION = "APPROVE_ACTION"
    REJECT_ACTION = "REJECT_ACTION"
    CANCEL_ONBOARDING = "CANCEL_ONBOARDING"
    QUERY_STATUS = "QUERY_STATUS"
    UNKNOWN = "UNKNOWN"

class CandidateExtractedData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = None
    personal_email: EmailStr | None = None
    role_title: str | None = None
    department: str | None = None
    manager_name: str | None = None
    manager_email: EmailStr | None = None
    start_date: date | None = None

class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REJECTED"] | None = None
    reason: str | None = None
    detected_action_ref: str | None = None

class NluExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: EmailIntent
    confidence: float = Field(ge=0.0, le=1.0)
    candidate_data: CandidateExtractedData = Field(default_factory=CandidateExtractedData)
    missing_fields: list[str] = Field(default_factory=list)
    approval_decision: ApprovalDecision | None = None
    query_summary: str | None = None
    security_flags: list[str] = Field(default_factory=list)
```

### 6.2 Prompt Injection Containment Protocol
- **Delimiter Defanging**: Raw email text has all `<` and `>` replaced with `&lt;` and `&gt;` and is enclosed in `<untrusted_email_body>` tags.
- **Role-Bound System Prompt**: The LLM is instructed that text within `<untrusted_email_body>` is passive data only and to ignore any instructions inside it.
- **Deterministic Policy Verification**: Approvals are completely rejected unless verified against the VERP HMAC token and SPF/DKIM validation.

---

## 7. Integration Tool Contracts & SaaS Reconciliation

### 7.1 Unified `ProvisioningResult`
```python
class ProvisioningResult(BaseModel):
    success: bool
    external_id: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
```

### 7.2 Port Interfaces (Protocols)
- **`GoogleWorkspacePort`**: `create_user()`, `add_to_group()`
- **`SlackPort`**: `invite_user()`
- **`GitHubPort`**: `invite_org_member()`
- **`HardwarePort`**: `order_laptop()`

### 7.3 Idempotent SaaS Reconciliation (ADR-0002)
- All provisioning tasks pass `idempotency_key = f"{case_id}:{task_key}"`.
- SaaS `HTTP 409 Conflict` ("already exists") triggers an automatic lookup. If the existing resource matches the candidate, the task reconciles as successful (`metadata={"reconciled": True}`).

### 7.4 Retry Schedule
- **Transient (HTTP 429, 5xx, timeouts)**: Bounded exponential backoff with jitter (max 3 retries over ~30s).
- **Permanent (HTTP 400, 401, 403)**: Immediate failure, alert logged, case flagged.

---

## 8. Three-Tier Approval Policy & Escalation

### 8.1 Policy Matrix (ADR-0003)
- **Tier 1 (Autonomous)**: Google Workspace account, core Slack channels, welcome emails, calendar invites.
- **Tier 2 (Manager Email Approval Required)**: GitHub org access, laptop procurement, paid SaaS licenses.
- **Tier 3 (Never Autonomous / Out-of-Band)**: Payroll bank details, production AWS/database credentials, account deletion.

### 8.2 Ambiguity Clarification Gate
- **High-confidence approval** (`approve`, `approved`, `go ahead`) $\rightarrow$ Execute.
- **High-confidence rejection** (`reject`, `deny`, `hold off`) $\rightarrow$ Mark `SKIPPED`.
- **Ambiguous chatter** $\rightarrow$ Request remains `PENDING`; automated clarification email sent to approver.

### 8.3 Escalation Schedule
- **T + 24h**: First gentle reminder.
- **T + 48h**: Second urgent reminder (CC HR coordinator).
- **T + 72h**: Escalation notice sent to IT/HR admin (status `ESCALATED`).
- **Start Date - 24h**: Unapproved tasks pause; case advances to `READY_FOR_FIRST_DAY` with Tier 1 tools active to guarantee day 1 readiness.

---

## 9. MVP Implementation Roadmap

```text
┌────────────────────────────────────────────────────────┐
│  Phase 1: Domain Core & Mock Integrations (Week 1)     │
│  • Pydantic v2 domain schemas & Postgres SQLModel/DDL  │
│  • Task DAG evaluator & state machine transitions      │
│  • Stateful in-memory mock adapters & test suite       │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│  Phase 2: Inbound Email Ingestion & Security (Week 2)  │
│  • Postmark webhook endpoint + email MIME parser       │
│  • 3-tier thread resolver & VERP HMAC generator/parser │
│  • Untrusted body defanging & heuristic pre-scanner    │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│  Phase 3: LLM Extraction & Safety Sandwich (Week 3)    │
│  • Claude / Instructor structured extraction pipeline  │
│  • Deterministic policy gate & ambiguity handler       │
│  • Outbound email drafting templates                   │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│  Phase 4: Live Adapters, Cadence & Hardening (Week 4)  │
│  • Google Workspace, Slack, GitHub live adapters      │
│  • Idempotent reconciliation & retry backoff engine    │
│  • Background escalation reminder polling worker       │
│  • End-to-end integration & fault injection tests      │
└────────────────────────────────────────────────────────┘
```

---

## 10. Architectural Decision Records Index

- **[ADR-0001: Declarative Postgres DAG and CandidateDraft Separation](docs/adr/0001-declarative-dag-and-candidate-draft-boundary.md)**
- **[ADR-0002: Hexagonal Ports & Idempotent SaaS Reconciliation](docs/adr/0002-hexagonal-ports-and-idempotent-reconciliation.md)**
- **[ADR-0003: Three-Tier Approval Policy Matrix & Escalation Lifecycle](docs/adr/0003-approval-policy-matrix-and-escalation.md)**
