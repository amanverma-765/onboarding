"""Email-first employee onboarding workflow coordinator."""

from onboarding.application.service import InboundProcessingResult, OnboardingService
from onboarding.domain.extractor import (
    CandidateExtractor,
    EmailIntent,
    NluExtractionResult,
    RuleBasedCandidateExtractor,
)
from onboarding.domain.models import (
    AuditEvent,
    CandidateDraft,
    CaseStatus,
    InboundEmail,
    OnboardingCase,
    OutboundEmail,
)
from onboarding.domain.sanitizer import EmailSanitizer

__all__ = [
    "AuditEvent",
    "CandidateDraft",
    "CandidateExtractor",
    "CaseStatus",
    "EmailIntent",
    "EmailSanitizer",
    "InboundEmail",
    "InboundProcessingResult",
    "NluExtractionResult",
    "OnboardingCase",
    "OnboardingService",
    "OutboundEmail",
    "RuleBasedCandidateExtractor",
]
