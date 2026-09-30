from __future__ import annotations

from typing import Any
from uuid import uuid4

from onboarding.domain.ports import (
    GitHubPort,
    GoogleWorkspacePort,
    HardwarePort,
    ProvisioningResult,
    SlackPort,
)


class MockGoogleWorkspaceAdapter(GoogleWorkspacePort):
    """Stateful in-memory Google Workspace adapter with fault injection."""

    def __init__(
        self,
        simulate_rate_limit: int = 0,
        simulate_network_failure: bool = False,
        simulate_auth_error: bool = False,
    ) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, set[str]] = {}
        self.seen_idempotency_keys: dict[str, ProvisioningResult] = {}
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_network_failure = simulate_network_failure
        self.simulate_auth_error = simulate_auth_error

    async def create_user(
        self,
        full_name: str,
        primary_email: str,
        personal_recovery_email: str,
        department: str,
        idempotency_key: str,
    ) -> ProvisioningResult:
        # 1. Fault injection
        if self.simulate_auth_error:
            return ProvisioningResult(
                success=False,
                error_message="HTTP 401 Unauthorized: Invalid service credentials",
                retryable=False,
            )

        if self.simulate_network_failure:
            return ProvisioningResult(
                success=False,
                error_message="TCP connection timeout: google-admin.googleapis.com",
                retryable=True,
            )

        if self.simulate_rate_limit > 0:
            self.simulate_rate_limit -= 1
            return ProvisioningResult(
                success=False,
                error_message="HTTP 429 Too Many Requests: Rate quota exceeded",
                retryable=True,
            )

        # 2. Idempotency check
        if idempotency_key in self.seen_idempotency_keys:
            return self.seen_idempotency_keys[idempotency_key]

        # 3. Conflict / Reconciliation check (HTTP 409)
        if primary_email in self.users:
            existing = self.users[primary_email]
            if (
                existing["full_name"] == full_name
                and existing["personal_recovery_email"] == personal_recovery_email
            ):
                result = ProvisioningResult(
                    success=True,
                    external_id=existing["id"],
                    metadata={
                        "reconciled": True,
                        "notice": "Existing account matched",
                    },
                )
                self.seen_idempotency_keys[idempotency_key] = result
                return result

            # Identity collision
            return ProvisioningResult(
                success=False,
                error_message=(
                    "Identity collision: account already assigned to another user"
                ),
                retryable=False,
            )

        # 4. Standard creation
        user_id = f"gw_usr_{uuid4().hex[:12]}"
        self.users[primary_email] = {
            "id": user_id,
            "full_name": full_name,
            "personal_recovery_email": personal_recovery_email,
            "department": department,
        }

        result = ProvisioningResult(
            success=True,
            external_id=user_id,
            metadata={"primary_email": primary_email, "reconciled": False},
        )
        self.seen_idempotency_keys[idempotency_key] = result
        return result

    async def add_to_group(
        self,
        user_email: str,
        group_email: str,
        idempotency_key: str,
    ) -> ProvisioningResult:
        if idempotency_key in self.seen_idempotency_keys:
            return self.seen_idempotency_keys[idempotency_key]

        members = self.groups.setdefault(group_email, set())
        reconciled = user_email in members
        members.add(user_email)

        result = ProvisioningResult(
            success=True,
            external_id=f"grp_{group_email}",
            metadata={"reconciled": reconciled, "group": group_email},
        )
        self.seen_idempotency_keys[idempotency_key] = result
        return result


class MockSlackAdapter(SlackPort):
    """Stateful in-memory Slack adapter with fault injection."""

    def __init__(
        self,
        simulate_rate_limit: int = 0,
        simulate_network_failure: bool = False,
    ) -> None:
        self.invites: dict[str, dict[str, Any]] = {}
        self.seen_idempotency_keys: dict[str, ProvisioningResult] = {}
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_network_failure = simulate_network_failure

    async def invite_user(
        self,
        email: str,
        full_name: str,
        channels: list[str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        if self.simulate_network_failure:
            return ProvisioningResult(
                success=False,
                error_message="HTTP 503 Service Unavailable: Slack API timeout",
                retryable=True,
            )

        if self.simulate_rate_limit > 0:
            self.simulate_rate_limit -= 1
            return ProvisioningResult(
                success=False,
                error_message="HTTP 429 Too Many Requests: Slack rate limit",
                retryable=True,
            )

        if idempotency_key in self.seen_idempotency_keys:
            return self.seen_idempotency_keys[idempotency_key]

        if email in self.invites:
            result = ProvisioningResult(
                success=True,
                external_id=self.invites[email]["id"],
                metadata={
                    "reconciled": True,
                    "notice": "User already invited to Slack",
                },
            )
            self.seen_idempotency_keys[idempotency_key] = result
            return result

        invite_id = f"slk_inv_{uuid4().hex[:12]}"
        self.invites[email] = {
            "id": invite_id,
            "full_name": full_name,
            "channels": channels,
        }

        result = ProvisioningResult(
            success=True,
            external_id=invite_id,
            metadata={"channels": channels, "reconciled": False},
        )
        self.seen_idempotency_keys[idempotency_key] = result
        return result


class MockGitHubAdapter(GitHubPort):
    """Stateful in-memory GitHub adapter for org invitations."""

    def __init__(self) -> None:
        self.invitations: dict[str, dict[str, Any]] = {}
        self.seen_idempotency_keys: dict[str, ProvisioningResult] = {}

    async def invite_org_member(
        self,
        email: str,
        teams: list[str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        if idempotency_key in self.seen_idempotency_keys:
            return self.seen_idempotency_keys[idempotency_key]

        if email in self.invitations:
            return ProvisioningResult(
                success=True,
                external_id=self.invitations[email]["id"],
                metadata={"reconciled": True},
            )

        inv_id = f"gh_inv_{uuid4().hex[:12]}"
        self.invitations[email] = {"id": inv_id, "teams": teams}
        result = ProvisioningResult(
            success=True,
            external_id=inv_id,
            metadata={"teams": teams, "reconciled": False},
        )
        self.seen_idempotency_keys[idempotency_key] = result
        return result


class MockHardwareAdapter(HardwarePort):
    """Stateful in-memory adapter for hardware requisitions."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.seen_idempotency_keys: dict[str, ProvisioningResult] = {}

    async def order_laptop(
        self,
        employee_id: str,
        role_title: str,
        shipping_address: dict[str, str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        if idempotency_key in self.seen_idempotency_keys:
            return self.seen_idempotency_keys[idempotency_key]

        if employee_id in self.orders:
            return ProvisioningResult(
                success=True,
                external_id=self.orders[employee_id]["id"],
                metadata={"reconciled": True},
            )

        order_id = f"hw_ord_{uuid4().hex[:12]}"
        self.orders[employee_id] = {
            "id": order_id,
            "role_title": role_title,
            "shipping_address": shipping_address,
        }
        result = ProvisioningResult(
            success=True,
            external_id=order_id,
            metadata={"order_id": order_id, "reconciled": False},
        )
        self.seen_idempotency_keys[idempotency_key] = result
        return result
