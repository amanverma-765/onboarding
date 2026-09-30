# Three-Tier Approval Policy Matrix & Escalation Lifecycle

All agent actions are governed by a deterministic three-tier policy matrix. Approvals operate via a conservative intent gate that demands explicit confirmation, and rejections gracefully mark task graph branches as `SKIPPED` rather than failing the onboarding case.

## Context
In automated enterprise onboarding, giving an agent autonomous access to provision permissions and equipment introduces privilege escalation and financial risks. Conversely, gating every trivial action (like creating a basic Google account or Slack handle) causes severe approval fatigue. Furthermore, natural language email replies from managers can be ambiguous or conversational (e.g., "Wait, what department?"), where a false-positive approval could grant unauthorized access.

## Decision
1. **Three-Tier Policy Matrix**:
   - **Tier 1 (Automatic / Autonomous)**: Standard communication tools (Google Workspace, Slack team channels), welcome communications, checklist notifications, and internal case state transitions.
   - **Tier 2 (Manager / IT Email Approval Required)**: Intellectual property and financial spending (GitHub organization/repo access, laptop procurement, specialized paid SaaS seats). Gated by cryptographic VERP HMAC tokens.
   - **Tier 3 (Never Autonomous / Out-of-Band)**: Sensitive compliance and infrastructure root access (payroll bank details, production AWS/database admin, account deletion). Strictly rejected if requested via email.
2. **Conservative Ambiguity Gate**: The NLU engine only approves or rejects on explicit high-confidence tokens (`approve`, `approved`, `proceed`, `reject`, `deny`). Ambiguous, conditional, or conversational replies leave the request in `PENDING` and trigger an automated clarification email.
3. **Graceful Rejection via Task Skipping**: Rejections set `ApprovalRequest.status = REJECTED` and mark the task and its downstream-only dependents as `SKIPPED` (not `FAILED`). Independent tasks continue unhindered.
4. **Time-to-Start Relative Reminder Schedule**: Reminders fire at T+24h, T+48h (CC HR), and T+72h (escalate to IT/HR admin). At Start Date - 24h, unapproved tasks pause while the rest of the case proceeds to `READY_FOR_FIRST_DAY`.

## Consequences
- Zero risk of autonomous privilege escalation to production or financial systems.
- Ambiguous email chatter cannot accidentally trigger privileged provisioning.
- Managers rejecting unneeded access (e.g. GitHub for non-technical roles) does not abort the employee's onboarding.
- New hires can still start on day 1 with core email and communication tools even if specialized approvals are delayed.
