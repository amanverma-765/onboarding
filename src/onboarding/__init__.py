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
    Employee,
    InboundEmail,
    OnboardingCase,
    OnboardingTask,
    OutboundEmail,
    TaskDependency,
    TaskStatus,
)
from onboarding.domain.sanitizer import EmailSanitizer

__all__ = [
    "AuditEvent",
    "CandidateDraft",
    "CandidateExtractor",
    "CaseStatus",
    "EmailIntent",
    "EmailSanitizer",
    "Employee",
    "InboundEmail",
    "InboundProcessingResult",
    "NluExtractionResult",
    "OnboardingCase",
    "OnboardingService",
    "OnboardingTask",
    "OutboundEmail",
    "RuleBasedCandidateExtractor",
    "TaskDependency",
    "TaskStatus",
]
