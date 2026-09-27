"""Integration-style tests for the chat orchestrator.

Gemini (understanding + reply writing) and the vector search are all mocked; the
eligibility engine and the real data/schemes.json are used as-is, so these tests check
that the pieces are wired together correctly, not that Gemini or Chroma work.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.models.scheme import ExclusionCategory
from backend.models.user_profile import UserProfile
from backend.services import assistant
from backend.services.eligibility import EligibilityStatus
from backend.services.retriever import SchemeMatch
from backend.services.understand import LanguageStyle, UnderstandingResult

_ALL_CLEAR = {c: False for c in ExclusionCategory if c is not ExclusionCategory.INSTITUTIONAL_LAND_HOLDER}


def _fake_understanding(**profile_kwargs: object) -> UnderstandingResult:
    """Build a canned UnderstandingResult with the given UserProfile fields set."""
    return UnderstandingResult(
        profile_updates=UserProfile(**profile_kwargs),
        search_query_en="pension for farmers",
        search_query_hi="किसानों के लिए पेंशन",
        language_style=LanguageStyle.ENGLISH,
    )


@pytest.fixture(autouse=True)
def mock_generate_text(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch the reply-writing Gemini call so no real network call is made."""
    mock = MagicMock(return_value="Here is what I found.")
    monkeypatch.setattr(assistant, "generate_text", mock)
    return mock


def _patch_search(monkeypatch: pytest.MonkeyPatch, matches: list[SchemeMatch]) -> MagicMock:
    """Patch search_schemes to return the same fixed list for every query."""
    mock = MagicMock(return_value=matches)
    monkeypatch.setattr(assistant, "search_schemes", mock)
    return mock


def test_full_turn_eligible_farmer(monkeypatch: pytest.MonkeyPatch, mock_generate_text: MagicMock) -> None:
    """A healthy young farmer with a clean record should come back eligible for both
    schemes, and the reply prompt should carry both official links."""
    monkeypatch.setattr(
        assistant, "understand_message",
        lambda message, last_question=None: _fake_understanding(
            age=30, owns_cultivable_land=True, landholding_hectares=1.5, monthly_pension=0, exclusions=_ALL_CLEAR
        ),
    )  # fmt: skip
    _patch_search(monkeypatch, [
        SchemeMatch(scheme_id="pm-kmy", category="pension", score=0.80),
        SchemeMatch(scheme_id="pm-kisan", category="agriculture", score=0.66),
    ])  # fmt: skip

    result = assistant.handle_message("I need pension help")

    assert set(result.matched_scheme_ids) == {"pm-kmy", "pm-kisan"}
    assert result.profile.age == 30
    kisan = next(r for r in result.eligibility.eligible if r.scheme_id == "pm-kisan")
    kmy = next(r for r in result.eligibility.eligible if r.scheme_id == "pm-kmy")
    assert kisan.status is EligibilityStatus.ELIGIBLE
    assert kmy.status is EligibilityStatus.ELIGIBLE

    reply_prompt = mock_generate_text.call_args[0][0]
    assert "pmkisan.gov.in" in reply_prompt
    assert result.reply_text == "Here is what I found."


def test_profile_merges_across_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fact learned in turn one must still be known in turn two, even though the second
    message only mentions something else."""
    monkeypatch.setattr(
        assistant, "understand_message",
        lambda message, last_question=None: _fake_understanding(age=30),
    )  # fmt: skip
    _patch_search(monkeypatch, [])

    turn_one = assistant.handle_message("I am 30 years old")
    assert turn_one.profile.age == 30
    assert turn_one.profile.owns_cultivable_land is None

    monkeypatch.setattr(
        assistant, "understand_message",
        lambda message, last_question=None: _fake_understanding(owns_cultivable_land=True),
    )  # fmt: skip
    turn_two = assistant.handle_message("Yes, I own land", profile=turn_one.profile)

    assert turn_two.profile.age == 30  # still remembered
    assert turn_two.profile.owns_cultivable_land is True  # newly learned


def test_matches_below_threshold_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A weak search match (below 0.35) must not reach the eligibility engine at all."""
    monkeypatch.setattr(assistant, "understand_message", lambda message, last_question=None: _fake_understanding())
    _patch_search(monkeypatch, [SchemeMatch(scheme_id="pm-kisan", category="agriculture", score=0.20)])

    result = assistant.handle_message("something unrelated")

    assert result.matched_scheme_ids == []
    assert result.eligibility.eligible == []
    assert result.eligibility.possibly_eligible == []
    assert result.eligibility.not_eligible == []


def test_best_score_wins_when_both_queries_match_the_same_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the English and Hindi queries both find the same scheme, keep the higher score."""
    monkeypatch.setattr(assistant, "understand_message", lambda message, last_question=None: _fake_understanding())

    def fake_search(query: str, top_k: int = 5, **_: object) -> list[SchemeMatch]:
        score = 0.9 if "पेंशन" in query else 0.5  # different score per language
        return [SchemeMatch(scheme_id="pm-kmy", category="pension", score=score)]

    monkeypatch.setattr(assistant, "search_schemes", fake_search)

    result = assistant.handle_message("pension please")
    assert result.matched_scheme_ids == ["pm-kmy"]


def test_no_match_still_produces_a_reply(monkeypatch: pytest.MonkeyPatch, mock_generate_text: MagicMock) -> None:
    """When nothing matches, the assistant still calls Gemini, saying honestly there is no match."""
    monkeypatch.setattr(assistant, "understand_message", lambda message, last_question=None: _fake_understanding())
    _patch_search(monkeypatch, [])

    result = assistant.handle_message("scholarship for students")

    assert result.matched_scheme_ids == []
    reply_prompt = mock_generate_text.call_args[0][0]
    assert "No matching scheme was found" in reply_prompt


def test_next_question_is_the_single_most_useful_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """With an empty profile, the assistant should surface exactly one follow-up question."""
    monkeypatch.setattr(assistant, "understand_message", lambda message, last_question=None: _fake_understanding())
    _patch_search(monkeypatch, [
        SchemeMatch(scheme_id="pm-kmy", category="pension", score=0.8),
        SchemeMatch(scheme_id="pm-kisan", category="agriculture", score=0.7),
    ])  # fmt: skip

    result = assistant.handle_message("what schemes can I get")

    assert result.next_question is not None
    assert result.next_question == result.eligibility.questions[0].question
