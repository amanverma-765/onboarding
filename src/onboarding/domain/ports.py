from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ProvisioningResult(BaseModel):
    """Standardized response contract for all SaaS integration ports."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    external_id: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoogleWorkspacePort(Protocol):
    """Port for company email and Google Workspace provisioning."""

    async def create_user(
        self,
        full_name: str,
        primary_email: str,
        personal_recovery_email: str,
        department: str,
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Create new Google Workspace account with recovery email."""
        ...

    async def add_to_group(
        self,
        user_email: str,
        group_email: str,
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Add user to departmental Google distribution group."""
        ...


class SlackPort(Protocol):
    """Port for Slack enterprise communication provisioning."""

    async def invite_user(
        self,
        email: str,
        full_name: str,
        channels: list[str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Send Slack workspace invite and pre-assign channels."""
        ...


class GitHubPort(Protocol):
    """Port for GitHub organization and repository access."""

    async def invite_org_member(
        self,
        email: str,
        teams: list[str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Invite user to organization and teams."""
        ...


class HardwarePort(Protocol):
    """Port for hardware and laptop procurement."""

    async def order_laptop(
        self,
        employee_id: str,
        role_title: str,
        shipping_address: dict[str, str],
        idempotency_key: str,
    ) -> ProvisioningResult:
        """Submit equipment requisition order."""
        ...
