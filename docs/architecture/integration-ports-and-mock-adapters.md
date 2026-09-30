# Integration Tool Contracts & Mock Adapters

This document specifies the Hexagonal Architecture ports, standardized result contracts, error handling, idempotent reconciliation, and mock adapters for external SaaS provisioning.

---

## 1. Standardized Result Contract

Every integration port returns a unified `ProvisioningResult` Pydantic model:

```python
class ProvisioningResult(BaseModel):
    success: bool
    external_id: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
```

The orchestrator inspects `success` and `retryable` to drive task state transitions without vendor-specific branching.

---

## 2. Port Interfaces (Async Protocols)

### `GoogleWorkspacePort`
Responsible for core company identity creation:
```python
class GoogleWorkspacePort(Protocol):
    async def create_user(
        self,
        full_name: str,
        primary_email: str,
        personal_recovery_email: str,
        department: str,
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Provisions Google Workspace account and recovery email."""
        ...

    async def add_to_group(
        self,
        user_email: str,
        group_email: str,
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Adds provisioned user to departmental distribution lists."""
        ...
```

### `SlackPort`
Responsible for workspace collaboration access:
```python
class SlackPort(Protocol):
    async def invite_user(
        self,
        email: str,
        full_name: str,
        channels: list[str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Sends workspace invite and assigns initial departmental channels."""
        ...
```

### `GitHubPort`
Responsible for developer organization and repository access:
```python
class GitHubPort(Protocol):
    async def invite_org_member(
        self,
        email: str,
        teams: list[str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Sends GitHub org invitation and adds to default team slugs."""
        ...
```

### `HardwarePort`
Responsible for physical equipment provisioning:
```python
class HardwarePort(Protocol):
    async def order_laptop(
        self,
        employee_id: str,
        role_title: str,
        shipping_address: dict[str, str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Submits equipment requisition to IT procurement queue."""
        ...
```

---

## 3. Idempotent Reconciliation

Every provisioning call generates an idempotency key:
```text
idempotency_key = f"{case_id}:{task_key}"
```

When an adapter encounters an `HTTP 409 Conflict` or `"user_already_exists"` error from a SaaS provider:
1. The adapter executes a lookup for the existing user using the candidate's email.
2. If the user exists and matches the candidate identity:
   - The adapter returns:
     ```python
     ProvisioningResult(
         success=True,
         external_id=existing_user.id,
         metadata={"reconciled": True, "notice": "Existing account matched"}
     )
     ```
3. If the user exists but belongs to an unrelated identity (collision):
   - The adapter returns:
     ```python
     ProvisioningResult(
         success=False,
         error_message="Identity collision: account already assigned to another user",
         retryable=False
     )
     ```

---

## 4. Error Classification & Retry Policy

External errors are mapped into two categories:

| Error Type | Status Codes / Exceptions | `retryable` | Action |
| :--- | :--- | :--- | :--- |
| **Transient** | `HTTP 429` (Rate Limit), `HTTP 500/502/503/504`, TCP connection timeout, DNS failure | `True` | Bounded exponential backoff + jitter |
| **Permanent** | `HTTP 400` (Bad Request), `HTTP 401` (Unauthorized), `HTTP 403` (Forbidden/Scope), Invalid domain | `False` | Fail immediately; alert case owner |

### Backoff Schedule:
- **Attempt 1**: Immediate execution.
- **Attempt 2**: Wait $2^1 + \text{jitter}$ (approx. 2-3s).
- **Attempt 3**: Wait $2^2 + \text{jitter}$ (approx. 4-6s).
- **Attempt 4**: Wait $2^3 + \text{jitter}$ (approx. 8-12s).
- **Max Retries Exceeded**: Mark task as `FAILED`, write `AuditEvent`, and send escalation email to IT admin.

---

## 5. Stateful In-Memory Mock Adapters

For testing and local development, mock adapters maintain state in memory:
- `MockGoogleWorkspaceAdapter`: Stores users in `dict[str, GoogleUser]`.
- `MockSlackAdapter`: Stores users and channel memberships.
- `MockGitHubAdapter`: Stores org invitations.

### Configurable Fault Injection:
Mock adapters accept fault simulation flags:
```python
mock_google = MockGoogleWorkspaceAdapter(
    simulate_rate_limit=False,
    simulate_network_failure=False,
    simulate_auth_error=False,
)
```
This enables deterministic automated tests for retry exhaustion, reconciliation on conflict, and task DAG propagation.
