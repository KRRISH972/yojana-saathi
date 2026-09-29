"""FastAPI entrypoint: `uvicorn backend.app.main:app`.

Builds the app, registers the routes, and turns known failures (rate limits, Gemini
errors, invalid input) into small JSON errors with a stable ``error`` code the UI can
translate into Hindi or English.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.routes import router
from backend.models.chat import ErrorResponse
from backend.services.llm import GeminiError, GeminiRateLimitError
from backend.services.rate_limit import RateLimitExceeded
from backend.services.retriever import ensure_ready

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Load the embedding model and search index at startup, so the first visitor does not
    wait for them (and a missing index is built instead of failing every search)."""
    ensure_ready()
    yield


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    """A JSON error body in the ErrorResponse shape."""
    body = ErrorResponse(error=error, detail=detail)  # type: ignore[arg-type]
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _on_rate_limited(_: Request, exc: Exception) -> JSONResponse:
    """Our own per-visitor/global limit was hit."""
    return _error(429, "rate_limited", str(exc))


async def _on_gemini_rate_limited(_: Request, exc: Exception) -> JSONResponse:
    """Gemini's free quota was hit; its message already says whether to wait or try tomorrow."""
    return _error(429, "ai_quota_exhausted", str(exc))


async def _on_gemini_error(_: Request, exc: Exception) -> JSONResponse:
    """Any other Gemini failure. The internal message is logged, never shown to the user."""
    logger.warning("Gemini call failed: %s", type(exc).__name__)
    return _error(503, "ai_unavailable", "The AI service is not responding right now. Please try again in a minute.")


async def _on_invalid_request(_: Request, exc: Exception) -> JSONResponse:
    """Input that failed validation, e.g. an empty or too-long message."""
    fields = sorted({".".join(str(p) for p in err["loc"] if p != "body") for err in exc.errors()})  # type: ignore[attr-defined]
    return _error(422, "invalid_request", f"Invalid request: {', '.join(f for f in fields if f) or 'body'}.")


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="Yojana Saathi", version="0.5.0", lifespan=lifespan)
    app.include_router(router)
    app.add_exception_handler(RateLimitExceeded, _on_rate_limited)
    app.add_exception_handler(GeminiRateLimitError, _on_gemini_rate_limited)
    app.add_exception_handler(GeminiError, _on_gemini_error)
    app.add_exception_handler(RequestValidationError, _on_invalid_request)
    return app


app = create_app()
