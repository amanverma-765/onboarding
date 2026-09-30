from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from uuid import UUID


class VerpTokenService:
    """Generates and verifies cryptographic HMAC-SHA256 VERP tokens."""

    _VERP_EMAIL_REGEX = re.compile(
        r"app\+([a-zA-Z0-9_-]+)@([a-zA-Z0-9_.-]+)", re.IGNORECASE
    )

    def __init__(self, secret_key: str = "onboarding-verp-secret-key") -> None:
        self.secret_key = secret_key.encode("utf-8")

    def generate_token(self, case_id: UUID, task_id: UUID, approver_email: str) -> str:
        """Create a cryptographic token binding case, task, and approver."""
        nonce = secrets.token_hex(4)
        payload = f"{case_id}:{task_id}:{approver_email.lower()}:{nonce}".encode()
        sig = hmac.new(self.secret_key, payload, hashlib.sha256).hexdigest()[:16]
        return f"{nonce}-{sig}"

    @classmethod
    def hash_token(cls, token: str) -> str:
        """Compute SHA-256 hash of token for database storage and indexing."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def extract_token_from_email(cls, email_address: str) -> str | None:
        """Extract token from an app+<token>@domain address."""
        match = cls._VERP_EMAIL_REGEX.search(email_address)
        if match:
            return match.group(1)
        return None

    def format_reply_to(self, token: str, domain: str = "company.internal") -> str:
        """Construct full VERP return email address."""
        return f"app+{token}@{domain}"
