"""Conversation-state definitions."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ConversationStage(str, Enum):
    """Observable stages in VictorIA's commercial conversation."""

    OPENING = "OPENING"
    DISCOVERY = "DISCOVERY"
    QUALIFICATION = "QUALIFICATION"
    OBJECTION = "OBJECTION"
    BOOKING = "BOOKING"
    BOOKED = "BOOKED"
    NO_FIT = "NO_FIT"
    CLOSED = "CLOSED"


class MessageRole(str, Enum):
    """Roles retained in provider-neutral conversation history."""

    USER = "user"
    ASSISTANT = "assistant"


class ConversationMessage(BaseModel):
    """One normalized conversational message."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: MessageRole
    content: str = Field(min_length=1)
