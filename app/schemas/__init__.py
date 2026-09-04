"""Public schemas for VictorIA's sales core."""

from app.schemas.booking import (
    AvailabilityPreference,
    BookingRequest,
    BookingResult,
    BookingSlot,
    BookingState,
    BookingStatus,
)
from app.schemas.conversation import (
    ConversationMessage,
    ConversationStage,
    MessageRole,
)
from app.schemas.objection import ObjectionType
from app.schemas.qualification import (
    EvidenceAssessment,
    EvidenceLevel,
    FitLevel,
    QualificationEvidence,
    QualificationResult,
)
from app.schemas.routing import RoutingSignals, ServiceRoute
from app.schemas.sales import (
    ConversationSession,
    NextAction,
    RequestScope,
    SalesAgentDraft,
    SalesAgentOutput,
)

__all__ = [
    "AvailabilityPreference",
    "BookingRequest",
    "BookingResult",
    "BookingSlot",
    "BookingState",
    "BookingStatus",
    "ConversationMessage",
    "ConversationSession",
    "ConversationStage",
    "EvidenceAssessment",
    "EvidenceLevel",
    "FitLevel",
    "MessageRole",
    "NextAction",
    "RequestScope",
    "ObjectionType",
    "QualificationEvidence",
    "QualificationResult",
    "RoutingSignals",
    "SalesAgentDraft",
    "SalesAgentOutput",
    "ServiceRoute",
]
