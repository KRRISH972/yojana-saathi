"""Tests for the Gemini wrapper's retry and error-handling logic, with the SDK client
mocked out entirely — no real network calls are made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services import llm


class _FakeAPIError(Exception):
    """Stands in for a Gemini SDK HTTP error: any exception with a status_code attribute."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"fake error, status {status_code}")
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _reset_client_and_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never sleep in tests, and always start with a clean (unset) cached client."""
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)


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
