from __future__ import annotations

import re


class EmailSanitizer:
    """Isolates untrusted email text to contain prompt injection attempts."""

    UNTRUSTED_OPEN = "<untrusted_email_body>"
    UNTRUSTED_CLOSE = "</untrusted_email_body>"

    INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
        re.compile(r"system\s+prompt", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
        re.compile(r"override\s+policy", re.IGNORECASE),
        re.compile(r"grant\s+(admin|production|root)\s+access", re.IGNORECASE),
        re.compile(r"do\s+not\s+require\s+approval", re.IGNORECASE),
    )

    @classmethod
    def defang_delimiters(cls, text: str) -> str:
        """Escape angle brackets so attacker text cannot break XML framing."""
        return text.replace("<", "&lt;").replace(">", "&gt;")

    @classmethod
    def scan_injection_patterns(cls, text: str) -> list[str]:
        """Scan text for common adversarial prompt injection indicators."""
        flags: list[str] = []
        for pattern in cls.INJECTION_PATTERNS:
            if pattern.search(text):
                flags.append(f"SUSPICIOUS_PATTERN: {pattern.pattern}")
        return flags

    @classmethod
    def sanitize(cls, raw_body: str) -> tuple[str, list[str]]:
        """Defang delimiters, wrap in boundary tags, and scan for security flags."""
        flags = cls.scan_injection_patterns(raw_body)
        defanged = cls.defang_delimiters(raw_body)
        wrapped = f"{cls.UNTRUSTED_OPEN}\n{defanged}\n{cls.UNTRUSTED_CLOSE}"
        return wrapped, flags
