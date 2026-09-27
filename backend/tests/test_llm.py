"""Tests for the Gemini wrapper's retry and error-handling logic, with the SDK client
mocked out entirely — no real network calls are made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services import llm


class _FakeAPIError(Exception):
    """Stands in for a Gemini SDK HTTP error: any exception with a status_code attribute
    and, optionally, a parsed ``.body`` (the shape the real SDK's error classes expose)."""

    def __init__(self, status_code: int, body: dict | None = None) -> None:
        super().__init__(f"fake error, status {status_code}")
        self.status_code = status_code
        if body is not None:
            self.body = body


@pytest.fixture(autouse=True)
def _reset_client_and_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Never really sleep in tests (but record calls so we can check retries stay fast),
    and always start with a clean (unset) cached client."""
    monkeypatch.setattr(llm, "_client", None)
    sleep_spy = MagicMock()
    monkeypatch.setattr(llm.time, "sleep", sleep_spy)
    return sleep_spy


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch _get_client() to return a MagicMock standing in for genai.Client()."""
    client = MagicMock()
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return client


def test_successful_call_returns_output_text(fake_client: MagicMock) -> None:
    """A normal, successful call just returns the interaction's output_text."""
    fake_client.interactions.create.return_value = SimpleNamespace(output_text="hello")
    assert llm.generate_text("hi there") == "hello"
    assert fake_client.interactions.create.call_count == 1


def test_call_uses_configured_model_and_low_thinking_level(fake_client: MagicMock) -> None:
    """generate_text must use GEMINI_MODEL and thinking_level 'low', with no deprecated params."""
    fake_client.interactions.create.return_value = SimpleNamespace(output_text="ok")
    llm.generate_text("hi", system_instruction="be nice")

    _, kwargs = fake_client.interactions.create.call_args
    assert kwargs["model"] == llm.get_settings().gemini_model
    assert kwargs["generation_config"] == {"thinking_level": "low"}
    assert kwargs["system_instruction"] == "be nice"
    for deprecated in ("temperature", "top_p", "top_k", "candidate_count", "thinking_budget"):
        assert deprecated not in kwargs
        assert deprecated not in kwargs.get("generation_config", {})


def test_transient_error_is_retried_then_succeeds(fake_client: MagicMock) -> None:
    """A 503 on the first attempt should be retried, and a later success is returned."""
    fake_client.interactions.create.side_effect = [
        _FakeAPIError(503),
        SimpleNamespace(output_text="recovered"),
    ]
    assert llm.generate_text("hi") == "recovered"
    assert fake_client.interactions.create.call_count == 2


def test_rate_limit_exhausted_raises_friendly_error(fake_client: MagicMock) -> None:
    """A 429 on every attempt raises GeminiRateLimitError, not a raw SDK exception."""
    fake_client.interactions.create.side_effect = _FakeAPIError(429)
    with pytest.raises(llm.GeminiRateLimitError):
        llm.generate_text("hi")
    assert fake_client.interactions.create.call_count == llm.MAX_ATTEMPTS


def test_client_disables_the_sdks_own_internal_retry_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: the SDK has its own internal retry loop that, left enabled, once
    turned a single 429 into a multi-minute hang before our own retry logic ever ran. The
    client must be built with attempts=0 so llm.py's retry loop is the only one that runs."""
    captured_kwargs: dict = {}

    def fake_client_constructor(**kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(llm.genai, "Client", fake_client_constructor)
    llm._get_client()

    http_options = captured_kwargs["http_options"]
    assert http_options.retry_options.attempts == 0


def test_repeated_rate_limit_fails_fast_without_relying_on_sdk_backoff(
    fake_client: MagicMock, _reset_client_and_sleep: MagicMock
) -> None:
    """Repeated 429s must exhaust our own (small, fixed) retry budget quickly, not hang
    for minutes waiting on some other backoff schedule."""
    fake_client.interactions.create.side_effect = _FakeAPIError(429)

    with pytest.raises(llm.GeminiRateLimitError):
        llm.generate_text("hi")

    assert fake_client.interactions.create.call_count == llm.MAX_ATTEMPTS
    # Only our own short, fixed delays were ever requested — nothing bigger, nothing
    # sourced from an SDK-side backoff strategy.
    slept_durations = [call.args[0] for call in _reset_client_and_sleep.call_args_list]
    assert slept_durations == list(llm.RETRY_DELAYS_SECONDS)
    assert sum(slept_durations) < 10  # comfortably under the ~30s ceiling


def test_daily_quota_message_names_the_daily_limit(fake_client: MagicMock) -> None:
    """When the server's own error text says the limit is per-day, the friendly message
    must say so too, rather than the misleading 'try again in a minute'."""
    fake_client.interactions.create.side_effect = _FakeAPIError(
        429,
        body={
            "error": {
                "message": (
                    "Rate limit exceeded for model gemini-3.8-flash "
                    "(limit: 20 requests per day on Free Tier). Please retry in 29s."
                )
            }
        },
    )
    with pytest.raises(llm.GeminiRateLimitError, match="tomorrow"):
        llm.generate_text("hi")


def test_per_minute_quota_message_says_try_again_in_a_minute(fake_client: MagicMock) -> None:
    """A per-minute limit should still get the "try again in a minute" message."""
    fake_client.interactions.create.side_effect = _FakeAPIError(
        429, body={"error": {"message": "Rate limit exceeded: 15 requests per minute."}}
    )
    with pytest.raises(llm.GeminiRateLimitError, match="minute"):
        llm.generate_text("hi")


def test_non_retryable_error_fails_immediately(fake_client: MagicMock) -> None:
    """A 400 (bad request) is a client mistake, not a transient failure, so no retries."""
    fake_client.interactions.create.side_effect = _FakeAPIError(400)
    with pytest.raises(llm.GeminiError):
        llm.generate_text("hi")
    assert fake_client.interactions.create.call_count == 1


def test_generate_structured_parses_json_into_model(fake_client: MagicMock) -> None:
    """generate_structured should validate the returned JSON text into the given model."""
    from pydantic import BaseModel

    class Animal(BaseModel):
        name: str
        legs: int

    fake_client.interactions.create.return_value = SimpleNamespace(output_text='{"name": "dog", "legs": 4}')
    result = llm.generate_structured("describe a dog", response_model=Animal)

    assert result == Animal(name="dog", legs=4)
    _, kwargs = fake_client.interactions.create.call_args
    assert kwargs["response_format"]["mime_type"] == "application/json"
    assert kwargs["response_format"]["schema"] == Animal.model_json_schema()
