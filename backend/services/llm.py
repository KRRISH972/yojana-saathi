"""Thin wrapper around the Gemini API (the google-genai SDK's Interactions API).

Every other module that needs the LLM calls through here — nothing else imports
``google.genai`` directly — so the model name, thinking level, timeout, and retry
behaviour all live in exactly one place.

This was written against google-genai 2.25.0 and the live Interactions API as of
2026-09-27. The Interactions API had a breaking change in May 2026 that dropped support
for SDK versions before 2.0.0, so ``requirements.txt`` pins ``google-genai>=2.0,<3.0``.
Per the current docs (https://ai.google.dev/gemini-api/docs/latest-model), calls use
``client.interactions.create(...)`` with a top-level ``system_instruction`` string, a
``generation_config={"thinking_level": ...}`` (replacing the deprecated
``thinking_budget``), and no deprecated sampling parameters (``temperature``, ``top_p``,
``top_k``, ``candidate_count`` are all unsupported on Gemini 3+ and are never passed here).

Error handling note: the SDK's HTTP error classes (RateLimitError, APITimeoutError, ...)
live in a private module (``google.genai._gaos...``) that could be renamed or moved
without notice between SDK versions, so this wrapper never imports them. Instead it reads
the ``status_code`` attribute off whatever exception comes back — confirmed present on
every HTTP-status error raised by this SDK version — and retries or raises based on that.

Retry note: the SDK has its OWN internal retry loop (google.genai._gaos.utils.retries),
enabled by default, sitting underneath every call. Left alone, it made a single 429 turn
into a real, observed multi-minute hang — our outer retry loop (below) would retry a call
that was itself silently retrying inside the SDK, compounding delays. We disable it
entirely via ``HttpRetryOptions(attempts=0)`` on the client's ``http_options``, so this
module's own MAX_ATTEMPTS/RETRY_DELAYS_SECONDS are the only retry logic that ever runs.
"""

from __future__ import annotations

import time
from typing import TypeVar

from google import genai
from google.genai.types import HttpOptions, HttpRetryOptions
from pydantic import BaseModel

from backend.app.config import get_settings

T = TypeVar("T", bound=BaseModel)

THINKING_LEVEL = "low"
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1.0, 3.0)  # delay before the 2nd and 3rd attempt
# Worst case, every attempt times out: 3 * 8s + 1s + 3s = 28s — comfortably under the
# ~30s the whole call (including all retries) must give up within.

_RATE_LIMIT_STATUS = 429
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

_client: genai.Client | None = None


class GeminiRateLimitError(RuntimeError):
    """Gemini is rate-limiting us, and retries did not help. Show the user a friendly message."""


class GeminiError(RuntimeError):
    """Any other Gemini call failure that survived retries."""


def _get_client() -> genai.Client:
    """Return the shared Gemini client, creating it only on first use.

    ``HttpRetryOptions(attempts=0)`` disables the SDK's own internal retry loop (see the
    module docstring) — without it, one 429 can silently turn into minutes of hidden
    retries before our own retry logic even gets a chance to run.
    """
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=get_settings().gemini_api_key,
            http_options=HttpOptions(retry_options=HttpRetryOptions(attempts=0)),
        )
    return _client


def _status_code(exc: Exception) -> int | None:
    """Best-effort extraction of an HTTP status code from a Gemini SDK exception."""
    return getattr(exc, "status_code", None)


def _rate_limit_detail_text(exc: Exception) -> str:
    """Best-effort extraction of the server's own rate-limit message text.

    The SDK's error classes expose a parsed ``.body`` (the JSON error payload) when
    available; fall back to the exception's string form if that shape isn't there.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return str(exc)


def _friendly_rate_limit_message(exc: Exception) -> str:
    """A friendly rate-limit message, naming a daily quota specifically when the server's
    own error text says so, since "try again in a minute" would be misleading advice for
    a quota that only resets tomorrow."""
    detail = _rate_limit_detail_text(exc).lower()
    if "per day" in detail or "daily" in detail:
        return "Gemini's free daily quota has been used up. Please try again tomorrow."
    if "per minute" in detail or "rpm" in detail:
        return "Gemini is getting a lot of requests right now. Please try again in a minute."
    return "The free AI limit may be used up. Try again in a minute; if it keeps happening, try again tomorrow."


def _call_gemini(**kwargs: object) -> object:
    """Call the Interactions API with a timeout, retrying transient failures.

    A definite rate limit (HTTP 429) that survives every retry raises GeminiRateLimitError
    with a friendly message. Any other unrecoverable failure raises GeminiError. A
    non-retryable error (e.g. a bad request) is raised immediately, without retrying.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _get_client().interactions.create(timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        except Exception as exc:  # noqa: BLE001 - see the error-handling note in the module docstring
            status = _status_code(exc)
            is_last_attempt = attempt == MAX_ATTEMPTS
            if status is not None and status not in _RETRYABLE_STATUS_CODES:
                raise GeminiError(f"Gemini request failed: {exc}") from exc
            if status == _RATE_LIMIT_STATUS and is_last_attempt:
                raise GeminiRateLimitError(_friendly_rate_limit_message(exc)) from exc
            if is_last_attempt:
                raise GeminiError(f"Gemini request failed after {MAX_ATTEMPTS} attempts: {exc}") from exc
            last_exc = exc
            time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
    raise GeminiError(f"Gemini request failed: {last_exc}")  # unreachable, satisfies type checkers


def generate_text(prompt: str, system_instruction: str | None = None) -> str:
    """Send one prompt to Gemini and return its plain text reply."""
    kwargs: dict[str, object] = {
        "model": get_settings().gemini_model,
        "input": prompt,
        "generation_config": {"thinking_level": THINKING_LEVEL},
    }
    if system_instruction is not None:
        kwargs["system_instruction"] = system_instruction
    interaction = _call_gemini(**kwargs)
    return interaction.output_text  # type: ignore[attr-defined]


def generate_structured(
    prompt: str,
    response_model: type[T],
    system_instruction: str | None = None,
    schema: dict[str, object] | None = None,
) -> T:
    """Send one prompt to Gemini and parse its JSON reply into ``response_model``.

    ``schema`` is the JSON schema Gemini is asked to follow; it defaults to
    ``response_model``'s own. Pass a stricter one to parse leniently but still ask Gemini
    for the exact shape (see backend/services/understand.py).
    """
    kwargs: dict[str, object] = {
        "model": get_settings().gemini_model,
        "input": prompt,
        "generation_config": {"thinking_level": THINKING_LEVEL},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": schema if schema is not None else response_model.model_json_schema(),
        },
    }
    if system_instruction is not None:
        kwargs["system_instruction"] = system_instruction
    interaction = _call_gemini(**kwargs)
    return response_model.model_validate_json(interaction.output_text)  # type: ignore[attr-defined]
