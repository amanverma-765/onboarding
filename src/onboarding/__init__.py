"""Email-first employee onboarding workflow coordinator."""

from onboarding.application.service import (
    InboundProcessingResult,
    OnboardingService,
)
from onboarding.application.task_engine import (
    ApprovalReplyResult,
    TaskExecutionEngine,
)
from onboarding.domain.extractor import (
    CandidateExtractor,
    EmailIntent,
    NluExtractionResult,
    RuleBasedCandidateExtractor,
)
from onboarding.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
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
from onboarding.domain.verp import VerpTokenService
from onboarding.infrastructure.mock_adapters import (
    MockGitHubAdapter,
    MockGoogleWorkspaceAdapter,
    MockHardwareAdapter,
    MockSlackAdapter,
)

__all__ = [
    "ApprovalReplyResult",
    "ApprovalRequest",
    "ApprovalStatus",
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
    "MockHardwareAdapter",
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
    "VerpTokenService",
]
