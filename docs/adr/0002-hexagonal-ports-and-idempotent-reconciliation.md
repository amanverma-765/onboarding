# Hexagonal Ports & Idempotent SaaS Reconciliation

All third-party SaaS integrations (Google Workspace, Slack, GitHub, Hardware) are abstracted behind async Python Protocol ports returning a standardized `ProvisioningResult`. Retries use deterministic idempotency keys (`{case_id}:{task_key}`) with automatic reconciliation when resources already exist.

## Context
External provisioning APIs (Google Admin Directory, Slack Web API, GitHub REST/GraphQL) frequently experience transient network dropouts, rate limits (HTTP 429), and socket timeouts. In distributed systems, a timeout often occurs *after* the target platform has successfully created the resource. A naive retry would then receive an HTTP 409 Conflict or "User already exists" error, falsely marking the onboarding task as failed.

## Decision
1. **Ports-and-Adapters Pattern**: The core task orchestrator only interacts with abstract Python async Protocols (`GoogleWorkspacePort`, `SlackPort`, `GitHubPort`).
2. **Unified `ProvisioningResult` Object**: Methods never raise raw HTTP/vendor exceptions to the orchestrator; they return a typed `ProvisioningResult` capturing `success`, `external_id`, `error_message`, `retryable: bool`, and metadata.
3. **Idempotent Reconciliation**: Creation tasks pass deterministic idempotency keys. On "Already Exists" responses, the adapter inspects the existing user identity. If matching the candidate, it marks the task reconciled (`success=True, metadata={"reconciled": True}`).
4. **Transient vs Permanent Error Classification**: Rate limits and 5xx errors are classified as `retryable=True` (bounded backoff, max 3 retries). 4xx validation and auth errors are permanent (`retryable=False`).
5. **Stateful In-Memory Mocks with Fault Injection**: Automated test suites run against stateful in-memory adapters with configurable network/rate-limit fault injection.

## Consequences
- The task engine is 100% decoupled from third-party vendor SDKs.
- Automated tests run instantly in-memory without live cloud API credentials or rate limits.
- Onboarding workflows survive transient network dropouts without manual IT intervention.
