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


def test_answer_only_turn_keeps_earlier_matched_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for a real bug: turn 1 finds both schemes and asks the income-tax
    question; turn 2 only answers "No", which Gemini correctly reports as empty search
    queries (a bare answer is not itself a new scheme search). That must NOT wipe out the
    schemes matched in turn 1 — they should still be checked with the updated profile."""
    monkeypatch.setattr(
        assistant, "understand_message",
        lambda message, last_question=None: UnderstandingResult(
            profile_updates=UserProfile(age=35, owns_cultivable_land=True, landholding_hectares=1.0),
            search_query_en="pension and income support for farmers",
            search_query_hi="किसानों के लिए पेंशन और आय सहायता",
            language_style=LanguageStyle.ENGLISH,
        ),
    )  # fmt: skip
    search_mock = _patch_search(monkeypatch, [
        SchemeMatch(scheme_id="pm-kmy", category="pension", score=0.80),
        SchemeMatch(scheme_id="pm-kisan", category="agriculture", score=0.66),
    ])  # fmt: skip

    turn_one = assistant.handle_message("I am a 35 year old farmer with 1 hectare of land")
    assert set(turn_one.matched_scheme_ids) == {"pm-kmy", "pm-kisan"}
    assert {r.scheme_id for r in turn_one.eligibility.possibly_eligible} == {"pm-kmy", "pm-kisan"}
    assert turn_one.next_question is not None
    assert "income tax" in turn_one.next_question.lower()
    assert search_mock.call_count == 2  # one call per non-empty query

    # Turn two only answers the income-tax question; the search queries come back empty.
    monkeypatch.setattr(
        assistant, "understand_message",
        lambda message, last_question=None: UnderstandingResult(
            profile_updates=UserProfile(exclusions={ExclusionCategory.INCOME_TAX_PAYER: False}),
            search_query_en="",
            search_query_hi="",
            language_style=LanguageStyle.ENGLISH,
        ),
    )  # fmt: skip

    turn_two = assistant.handle_message(
        "No",
        profile=turn_one.profile,
        last_question=turn_one.next_question,
        matched_scheme_ids=turn_one.matched_scheme_ids,
    )

    # The bug: this previously came back with matched_scheme_ids == [] and "no matching
    # scheme found", because a bare "No" doesn't match either scheme's search document.
    assert set(turn_two.matched_scheme_ids) == {"pm-kmy", "pm-kisan"}
    assert search_mock.call_count == 2  # unchanged: no new search call on an answer-only turn
    assert not turn_two.eligibility.not_eligible  # income tax = no rules out neither scheme
    assert turn_two.eligibility.eligible or turn_two.eligibility.possibly_eligible
    assert turn_two.next_question is not None
    assert turn_two.next_question != turn_one.next_question
    assert "income tax" not in turn_two.next_question.lower()


def _set_understanding(monkeypatch: pytest.MonkeyPatch, updates: UserProfile) -> None:
    """Make the next understand_message call return ``updates`` as an answer-only turn."""
    monkeypatch.setattr(
        assistant, "understand_message",
        lambda message, last_question=None: UnderstandingResult(
            profile_updates=updates, search_query_en="", search_query_hi="", language_style=LanguageStyle.ENGLISH
        ),
    )  # fmt: skip


def test_no_land_answer_clears_an_earlier_land_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for a real bug: "I have 2 hectares" (ownership still unknown), then
    "no" to the land-ownership question, used to crash the turn with a ValidationError
    (hectares set but owns_cultivable_land False). The newer "no" must win instead."""
    _patch_search(monkeypatch, [])
    _set_understanding(monkeypatch, UserProfile(landholding_hectares=2.0))
    turn_one = assistant.handle_message("I have 2 hectares", matched_scheme_ids=["pm-kisan"])
    assert turn_one.profile.landholding_hectares == 2.0
    assert turn_one.profile.owns_cultivable_land is None

    _set_understanding(monkeypatch, UserProfile(owns_cultivable_land=False))
    turn_two = assistant.handle_message(
        "no", profile=turn_one.profile, last_question=turn_one.next_question,
        matched_scheme_ids=turn_one.matched_scheme_ids,
    )  # fmt: skip

    assert turn_two.profile.owns_cultivable_land is False
    assert turn_two.profile.landholding_hectares is None  # contradicted older value cleared
    assert [r.scheme_id for r in turn_two.eligibility.not_eligible] == ["pm-kisan"]


def test_new_land_size_clears_an_earlier_no_land_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse contradiction: "I don't own land", then later "I have 3 hectares". The
    newer land size wins; the older "no" is cleared back to unknown rather than guessed."""
    _patch_search(monkeypatch, [])
    _set_understanding(monkeypatch, UserProfile(owns_cultivable_land=False))
    turn_one = assistant.handle_message("I don't own any land")

    _set_understanding(monkeypatch, UserProfile(landholding_hectares=3.0))
    turn_two = assistant.handle_message("Actually I have 3 hectares", profile=turn_one.profile)

    assert turn_two.profile.landholding_hectares == 3.0
    assert turn_two.profile.owns_cultivable_land is None


def test_invalid_merge_keeps_previous_profile_and_logs_no_user_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Safety net: if a merge still fails validation, the turn keeps the previous profile,
    logs a short warning without any user text, and the conversation carries on."""
    _patch_search(monkeypatch, [])
    previous = UserProfile(age=30, state="Bihar")
    _set_understanding(monkeypatch, UserProfile.model_construct(age=500))  # bypasses validation

    with caplog.at_level("WARNING", logger=assistant.__name__):
        result = assistant.handle_message("my-private-message-text", profile=previous)

    assert result.profile == previous
    assert result.reply_text == "Here is what I found."
    assert "keeping the previous profile" in caplog.text
    assert "age" in caplog.text
    assert "my-private-message-text" not in caplog.text
    assert "500" not in caplog.text
