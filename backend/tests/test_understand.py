"""Tests for the single 'understand' Gemini call, with Gemini itself mocked out."""

from __future__ import annotations

import pytest

from backend.services import understand
from backend.services.understand import LanguageStyle, UnderstandingResult, understand_message


@pytest.fixture
def mock_generate_structured(monkeypatch: pytest.MonkeyPatch):
    """Patch understand.generate_structured to return a canned result and record its call."""
    calls: list[dict] = []

    def fake(prompt: str, response_model, system_instruction: str | None = None):
        calls.append({"prompt": prompt, "response_model": response_model, "system_instruction": system_instruction})
        return UnderstandingResult(
            search_query_en="pension for farmers", search_query_hi="किसानों के लिए पेंशन", language_style="hindi"
        )

    monkeypatch.setattr(understand, "generate_structured", fake)
    return calls


def test_understand_message_returns_the_gemini_result(mock_generate_structured: list[dict]) -> None:
    """understand_message is a thin wrapper: it returns exactly what Gemini produced."""
    result = understand_message("मुझे किसान पेंशन चाहिए")
    assert result.search_query_en == "pension for farmers"
    assert result.language_style is LanguageStyle.HINDI


def test_prompt_includes_the_message_and_response_model(mock_generate_structured: list[dict]) -> None:
    """The prompt sent to Gemini must contain the user's message and target the right schema."""
    understand_message("मुझे किसान पेंशन चाहिए")
    call = mock_generate_structured[0]
    assert "मुझे किसान पेंशन चाहिए" in call["prompt"]
    assert call["response_model"] is UnderstandingResult
    assert call["system_instruction"] is not None


def test_last_question_is_included_when_given(mock_generate_structured: list[dict]) -> None:
    """A previous question should be passed through so short answers can be attributed."""
    understand_message("yes", last_question="Do you own cultivable land?")
    assert "Do you own cultivable land?" in mock_generate_structured[0]["prompt"]


def test_no_last_question_is_marked_clearly(mock_generate_structured: list[dict]) -> None:
    """With no prior question, the prompt should say so rather than leaving it blank."""
    understand_message("hello")
    assert "(none)" in mock_generate_structured[0]["prompt"]
