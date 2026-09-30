from __future__ import annotations

import contextlib
import re
from datetime import date
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from onboarding.domain.models import (
    MANDATORY_CANDIDATE_FIELDS,
    CandidateDraft,
    InboundEmail,
)
from onboarding.domain.sanitizer import EmailSanitizer


class EmailIntent(StrEnum):
    START_ONBOARDING = "START_ONBOARDING"
    PROVIDE_INFO = "PROVIDE_INFO"
    APPROVE_ACTION = "APPROVE_ACTION"
    REJECT_ACTION = "REJECT_ACTION"
    CANCEL_ONBOARDING = "CANCEL_ONBOARDING"
    QUERY_STATUS = "QUERY_STATUS"
    UNKNOWN = "UNKNOWN"


class NluExtractionResult(BaseModel):
    """Result of intent and entity extraction."""

    model_config = ConfigDict(extra="forbid")

    intent: EmailIntent
    confidence: float = Field(ge=0.0, le=1.0)
    candidate_data: CandidateDraft = Field(default_factory=CandidateDraft)
    missing_fields: list[str] = Field(default_factory=list)
    security_flags: list[str] = Field(default_factory=list)
    query_summary: str | None = None


def compute_missing_fields(candidate: CandidateDraft) -> list[str]:
    """Deterministically calculate which mandatory fields are missing."""
    missing: list[str] = []
    for field_name in MANDATORY_CANDIDATE_FIELDS:
        val = getattr(candidate, field_name, None)
        if val is None or val == "":
            missing.append(field_name)
    return missing


class CandidateExtractor(Protocol):
    """Port interface for candidate data extraction."""

    def extract(self, email: InboundEmail) -> NluExtractionResult: ...


class RuleBasedCandidateExtractor:
    """Deterministic extractor parsing structured or natural language intake emails."""

    _DATE_PATTERN = re.compile(
        r"(?:starting\s+(?:on\s+)?|start\s*date\s*[:=]\s*)?(\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )
    _EMAIL_PATTERN = re.compile(
        r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    )

    def extract(self, email: InboundEmail) -> NluExtractionResult:
        _wrapped, flags = EmailSanitizer.sanitize(email.body)
        subject_lower = email.subject.lower()
        body_lower = email.body.lower()

        # Classify intent with specific patterns taking precedence
        if "cancel" in subject_lower or "cancel" in body_lower:
            intent = EmailIntent.CANCEL_ONBOARDING
            confidence = 0.90
        elif "status" in subject_lower or "readiness" in body_lower:
            intent = EmailIntent.QUERY_STATUS
            confidence = 0.85
        elif (
            "onboard" in subject_lower
            or "onboard" in body_lower
            or "new hire" in subject_lower
        ):
            intent = EmailIntent.START_ONBOARDING
            confidence = 0.95
        else:
            intent = EmailIntent.UNKNOWN
            confidence = 0.30

        if intent != EmailIntent.START_ONBOARDING:
            return NluExtractionResult(
                intent=intent,
                confidence=confidence,
                candidate_data=CandidateDraft(),
                missing_fields=[],
                security_flags=flags,
            )

        extracted = self._parse_candidate_attributes(email)
        missing = compute_missing_fields(extracted)

        return NluExtractionResult(
            intent=intent,
            confidence=confidence,
            candidate_data=extracted,
            missing_fields=missing,
            security_flags=flags,
        )

    def _parse_candidate_attributes(self, email: InboundEmail) -> CandidateDraft:
        body = email.body
        full_name: str | None = None
        personal_email: str | None = None
        role_title: str | None = None
        department: str | None = None
        manager_name: str | None = None
        manager_email: str | None = None
        start_date: date | None = None

        # 1. Check for key-value lines
        for line in body.splitlines():
            line_stripped = line.strip()
            if not line_stripped or ":" not in line_stripped:
                continue
            key, _, val = line_stripped.partition(":")
            k = key.strip().lower()
            v = val.strip()
            if not v:
                continue

            if k in ("name", "full name", "candidate", "candidate name"):
                full_name = v
            elif k in ("personal email", "email", "candidate email"):
                personal_email = v.rstrip(".,;:!?)>")
            elif k in ("role", "role title", "title", "position", "job title"):
                role_title = v
            elif k in ("department", "dept", "team"):
                department = v
            elif k in ("manager name", "reporting manager name"):
                manager_name = v
            elif k in ("manager", "manager email", "reporting manager", "reports to"):
                if "@" in v:
                    manager_email = v.rstrip(".,;:!?)>")
                else:
                    manager_name = v
            elif k in ("start date", "start", "starting"):
                date_match = self._DATE_PATTERN.search(v)
                if date_match:
                    with contextlib.suppress(ValueError):
                        start_date = date.fromisoformat(date_match.group(1))

        # 2. Natural language fallback heuristics if key-values were not present
        if not full_name:
            for text in (email.subject, body):
                name_match = re.search(
                    r"(?:onboard|hire)\s+(?:new\s+hire\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
                    text,
                )
                if name_match:
                    full_name = name_match.group(1).strip()
                    break
                single_match = re.search(
                    r"(?:onboard|hire)\s+(?:new\s+hire\s+)?([A-Z][a-z]+)",
                    text,
                )
                if single_match:
                    full_name = single_match.group(1).strip()
                    break

        # Parse emails in body, trimming trailing punctuation
        raw_emails = [em.rstrip(".,;:!?)>") for em in self._EMAIL_PATTERN.findall(body)]

        if not personal_email:
            for em in raw_emails:
                is_internal = "company" in em.lower() or "corp" in em.lower()
                if em != email.sender and not is_internal:
                    personal_email = em
                    break
            if not personal_email and raw_emails:
                personal_email = raw_emails[0]

        if not manager_email:
            mgr_match = re.search(
                r"(?:reporting\s+to|manager\s*[:=]?)\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
                body,
                re.IGNORECASE,
            )
            if mgr_match:
                manager_email = mgr_match.group(1).rstrip(".,;:!?)>")

        if not role_title:
            role_match = re.search(
                r"(?:as\s+(?:a|an)\s+)([A-Z][a-zA-Z0-9\s]+?)"
                r"(?:\s+(?:in|starting|reporting|team)|[.,\n]|$)",
                body,
            )
            if role_match:
                role_title = role_match.group(1).strip()

        if not department:
            dept_match = re.search(
                r"(?:in\s+(?:the\s+)?|for\s+(?:the\s+)?|department\s*[:=]\s*)"
                r"([A-Z][a-zA-Z]+)(?:\s+team|\s+department|[.,\n]|$)",
                body,
            )
            if dept_match:
                department = dept_match.group(1).strip()

        if not start_date:
            date_match = self._DATE_PATTERN.search(body)
            if date_match:
                with contextlib.suppress(ValueError):
                    start_date = date.fromisoformat(date_match.group(1))

        return CandidateDraft(
            full_name=full_name,
            personal_email=personal_email,
            role_title=role_title,
            department=department,
            manager_name=manager_name,
            manager_email=manager_email,
            start_date=start_date,
        )
