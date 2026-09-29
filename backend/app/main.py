"""FastAPI entrypoint: `uvicorn backend.app.main:app`.

Builds the app, registers the routes, and turns known failures (rate limits, Gemini
errors, invalid input) into small JSON errors with a stable ``error`` code the UI can
translate into Hindi or English.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.routes import router
from backend.models.chat import ErrorResponse
from backend.services.llm import GeminiError, GeminiRateLimitError
from backend.services.rate_limit import RateLimitExceeded
from backend.services.retriever import ensure_ready

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'self' https://huggingface.co"  # Hugging Face Spaces shows the app in an iframe
)


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


async def _security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Add browser security headers. The web UI loads only its own files, so a strict
    Content-Security-Policy is possible everywhere except FastAPI's /docs pages (which
    load Swagger UI from a CDN)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    if not request.url.path.startswith(("/docs", "/redoc")):
        response.headers["Content-Security-Policy"] = _CSP
    return response


def create_app() -> FastAPI:
    """Build the FastAPI application: API routes first, then the static web UI at "/"."""
    app = FastAPI(title="Yojana Saathi", version="0.6.0", lifespan=lifespan)
    app.include_router(router)
    app.add_exception_handler(RateLimitExceeded, _on_rate_limited)
    app.add_exception_handler(GeminiRateLimitError, _on_gemini_rate_limited)
    app.add_exception_handler(GeminiError, _on_gemini_error)
    app.add_exception_handler(RequestValidationError, _on_invalid_request)
    app.middleware("http")(_security_headers)
    app.add_middleware(GZipMiddleware, minimum_size=500)  # much smaller pages on slow mobile networks
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
