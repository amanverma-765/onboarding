# Approval Policy Engine & Escalation Timeouts

This document specifies the deterministic policy matrix, approval reply parsing semantics, escalation timeouts, and rejection handling for the onboarding agent.

---

## 1. Three-Tier Policy Matrix

The agent deterministically inspects every generated task against the policy matrix before execution.

```text
                            Task Generated
                                  │
                                  ▼
                        Determine Policy Tier
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
      Tier 1                   Tier 2                   Tier 3
   [Autonomous]          [Approval Required]       [Out-of-Band Only]
         │                        │                        │
         ▼                        ▼                        ▼
  Execute immediately      Generate VERP Token      Reject immediately
  via Ports-and-Adapters   & Email Approver         & Alert Security / HR
                           (Wait in PENDING)        (Never autonomous)
```

| Tier | Category | Actions | Execution Mode | Approver |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1** | Standard Identity & Comms | • Create Google Workspace user (`firstname.lastname@company.internal`)<br>• Add to core Slack channels (`#general`, `#announcements`, `#team-<dept>`)<br>• Send welcome emails & checklist reminders<br>• Schedule day-1 calendar invites | **Automatic** | None (Autonomous) |
| **Tier 2** | Resource Spending & IP Access | • GitHub organization invitation & team assignments<br>• Hardware procurement order (MacBook / Dell workstation)<br>• Specialized paid SaaS seats (Figma, Jira, Salesforce) | **Approval Gated** | Reporting Manager or IT Lead via VERP Email Reply |
| **Tier 3** | Compliance & Root Infrastructure | • Payroll / Direct Deposit bank details modification<br>• Production AWS / Cloud console root or IAM admin<br>• Production database access (SSH / VPN / Bastion)<br>• Terminating or deleting employee accounts | **Out-of-Band** | Prohibited via email; requires manual HR/IT intervention |

---

## 2. Approval Parsing Semantics & Clarification Flow

Approver email replies are evaluated through a strict classification pipeline:

```text
                  Incoming Email Reply Received
                                │
                                ▼
               Extract Intent & Sentiment via LLM
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
  High-Confidence        High-Confidence         Ambiguous /
   Approval Token         Rejection Token         Conversational
  (conf >= 0.85)         (conf >= 0.85)         (conf < 0.85)
         │                      │                      │
         ▼                      ▼                      ▼
  Validate VERP HMAC     Validate VERP HMAC     Leave Request PENDING
  & SPF/DKIM Domain      & SPF/DKIM Domain             │
         │                      │                      ▼
         ▼                      ▼               Send Clarification
  Mark APPROVED          Mark REJECTED          Email to Approver
  & Execute Task         & Skip Task
```

### Approved Tokens:
`approve`, `approved`, `looks good`, `go ahead`, `yes`, `confirmed`, `proceed`, `lgtm`.

### Rejected Tokens:
`reject`, `rejected`, `do not grant`, `deny`, `no`, `cancel`, `hold off`.

### Ambiguous Replies:
If a manager replies with questions or chatter (e.g. *"Wait, what team is this for?"*, *"Let me check with IT first"*):
- Approval request remains in `PENDING`.
- The agent drafts an automated clarification email:
  > **Subject:** Clarification needed: GitHub Access for Sarah Chen [Case #ONB-001]
  >
  > *Hi Mike,*
  >
  > *I received your note regarding GitHub access for Sarah Chen, but couldn't confirm an approval or rejection.*
  >
  > *To proceed with provisioning, please reply **"Approved"** or **"Rejected"**.*

---

## 3. Reminder Cadence & Time-to-Start Escalation Schedule

Approval requests are monitored by a scheduled polling worker relative to creation time and employee start date:

```text
T = Approval Request Created
│
├── T + 24 Hours:  1st Gentle Reminder sent to approver
│
├── T + 48 Hours:  2nd Urgent Reminder sent to approver (CC HR Coordinator)
│
├── T + 72 Hours:  Escalation Alert sent to IT / HR Admin (Status -> ESCALATED)
│
└── Start Date - 24 Hours:
                   Unapproved Tier 2 tasks paused
                   Case advances to READY_FOR_FIRST_DAY with core Tier 1 access active
```

*Guaranteed Day 1 Readiness*: Stalled hardware or repository approvals will not prevent the employee from receiving their Google Workspace and Slack credentials on their first day. Blocked items are flagged for human HR resolution.

---

## 4. Rejection Handling & Task Graph Pruning

When an approver explicitly rejects a task:
1. `ApprovalRequest.status` transitions to `REJECTED`.
2. The target `OnboardingTask.status` transitions to `SKIPPED`.
3. The deterministic task orchestrator inspects the DAG:
   - Any downstream tasks that strictly depend *only* on the skipped task are also marked `SKIPPED`.
   - All independent tasks (e.g. Slack provisioning when GitHub is rejected) continue unhindered.
4. An `AuditEvent` is recorded: `TASK_SKIPPED_BY_APPROVER`.
5. A confirmation summary is sent to the manager and HR coordinator.
