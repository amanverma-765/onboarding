"""Email-first employee onboarding workflow coordinator."""

from onboarding.application.service import (
    InboundProcessingResult,
    OnboardingService,
)
from onboarding.application.task_engine import TaskExecutionEngine
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
from onboarding.domain.ports import (
    GitHubPort,
    GoogleWorkspacePort,
    HardwarePort,
    ProvisioningResult,
    SlackPort,
)
from onboarding.domain.sanitizer import EmailSanitizer
from onboarding.infrastructure.mock_adapters import (
    MockGitHubAdapter,
    MockGoogleWorkspaceAdapter,
    MockSlackAdapter,
)

__all__ = [
    "AuditEvent",
    "CandidateDraft",
    "CandidateExtractor",
    "CaseStatus",
    "EmailIntent",
    "EmailSanitizer",
    "Employee",
    "GitHubPort",
    "GoogleWorkspacePort",
    "HardwarePort",
    "InboundEmail",
    "InboundProcessingResult",
    "MockGitHubAdapter",
    "MockGoogleWorkspaceAdapter",
    "MockSlackAdapter",
    "NluExtractionResult",
    "OnboardingCase",
    "OnboardingService",
    "OnboardingTask",
    "OutboundEmail",
    "ProvisioningResult",
    "RuleBasedCandidateExtractor",
    "SlackPort",
    "TaskDependency",
    "TaskExecutionEngine",
    "TaskStatus",
]
