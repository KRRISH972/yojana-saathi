"""HTTP routes. Each one only validates input, calls a service, and returns a model."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.models.chat import ChatRequest, ChatResponse, ErrorResponse
from backend.services.chat_session import run_chat_turn
from backend.services.rate_limit import check_chat_rate_limit

router = APIRouter(prefix="/api")

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorResponse, "description": "Invalid request (e.g. an empty message)."},
    429: {"model": ErrorResponse, "description": "Rate limited, or the free AI quota is used up."},
    503: {"model": ErrorResponse, "description": "The AI service did not respond."},
}


def visitor_key(request: Request) -> str:
    """Identify a visitor for rate limiting: the first X-Forwarded-For address when behind
    a proxy (as on Hugging Face Spaces), otherwise the direct client address."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_chat_rate_limit(request: Request) -> None:
    """Dependency: reject the request if this visitor, or everyone together, is over the limit."""
    check_chat_rate_limit(visitor_key(request))


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check for the hosting platform."""
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse, responses=_ERROR_RESPONSES)
def chat(body: ChatRequest, _: None = Depends(enforce_chat_rate_limit)) -> ChatResponse:
    """One chat turn: send a message and the previous state, get the reply and new state."""
    return run_chat_turn(body)
