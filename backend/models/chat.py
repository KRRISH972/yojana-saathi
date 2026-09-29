"""Request/response models for the chat API (POST /api/chat).

The server is stateless: the browser keeps ConversationState and sends it back with every
message. Nothing about a citizen is stored on the server, and a restart (common on free
hosting) never loses a conversation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.user_profile import UserProfile
from backend.services.eligibility import EligibilityStatus
from backend.services.understand import LanguageStyle

MAX_MESSAGE_CHARS = 1000


class ConversationState(BaseModel):
    """Everything carried from one turn to the next, kept by the browser."""

    model_config = ConfigDict(extra="forbid")

    profile: UserProfile = Field(default_factory=UserProfile)
    last_question: str | None = Field(default=None, max_length=500)
    last_question_field: str | None = Field(default=None, max_length=100)
    matched_scheme_ids: list[str] = Field(default_factory=list, max_length=50)


class ChatRequest(BaseModel):
    """One user message plus the state returned by the previous response (empty to start)."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    state: ConversationState = Field(default_factory=ConversationState)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, message: str) -> str:
        """Strip surrounding whitespace and reject a message that is only whitespace."""
        stripped = message.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class SchemeCard(BaseModel):
    """One matched scheme, ready to show as a card in the UI."""

    scheme_id: str
    name_en: str
    name_hi: str
    status: EligibilityStatus
    reasons: list[str]
    benefits: str
    official_url: str


class ChatResponse(BaseModel):
    """The assistant's reply, the scheme cards, and the state to send with the next message."""

    reply: str
    language_style: LanguageStyle = Field(description="Also tells the UI which voice to speak the reply in.")
    schemes: list[SchemeCard] = Field(description="Eligible first, then possibly eligible, then not eligible.")
    next_question: str | None = None
    understood_by: Literal["quick_answer", "gemini"]
    state: ConversationState


class ErrorResponse(BaseModel):
    """Body of every error response; ``error`` is a stable code the UI can translate."""

    error: Literal["rate_limited", "ai_quota_exhausted", "ai_unavailable", "invalid_request"]
    detail: str
