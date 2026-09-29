"""Tests for the Python-only yes/no shortcut (no Gemini involved at all)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.models.scheme import ExclusionCategory
from backend.models.user_profile import UserProfile
from backend.services import assistant
from backend.services.quick_answer import parse_yes_no, quick_answer
from backend.services.retriever import SchemeMatch
from backend.services.understand import LanguageStyle

_INCOME_TAX = "exclusion:income_tax_payer"


@pytest.mark.parametrize(
    ("message", "answer", "style"),
    [
        ("yes", True, LanguageStyle.ENGLISH),
        ("Yes.", True, LanguageStyle.ENGLISH),
        ("  NO!  ", False, LanguageStyle.ENGLISH),
        ("no", False, LanguageStyle.ENGLISH),
        ("haan", True, LanguageStyle.HINGLISH),
        ("Ha", True, LanguageStyle.HINGLISH),
        ("haan ji", True, LanguageStyle.HINGLISH),
        ("nahi", False, LanguageStyle.HINGLISH),
        ("nhi", False, LanguageStyle.HINGLISH),
        ("na", False, LanguageStyle.HINGLISH),
        ("Nahin.", False, LanguageStyle.HINGLISH),
        ("हाँ", True, LanguageStyle.HINDI),
        ("हां", True, LanguageStyle.HINDI),
        ("नहीं", False, LanguageStyle.HINDI),
        ("नहीं।", False, LanguageStyle.HINDI),
        ("ना", False, LanguageStyle.HINDI),
    ],
)
def test_parse_yes_no_in_english_hinglish_and_hindi(message: str, answer: bool, style: LanguageStyle) -> None:
    """Every supported spelling is recognised, along with the language it was written in."""
    assert parse_yes_no(message) == (answer, style)


@pytest.mark.parametrize("message", ["no, but my wife pays tax", "I am 35", "maybe", "not sure", "", "nahi pata"])
def test_anything_more_than_a_bare_yes_no_is_not_parsed(message: str) -> None:
    """Longer or unclear messages must go to Gemini, never be guessed here."""
    assert parse_yes_no(message) is None


def test_no_to_an_exclusion_question_is_saved_as_false() -> None:
    """The main case: "No" to the income-tax question."""
    result = quick_answer("No", _INCOME_TAX)
    assert result is not None
    assert result.profile_updates == UserProfile(exclusions={ExclusionCategory.INCOME_TAX_PAYER: False})
    assert result.search_query_en == result.search_query_hi == ""  # an answer, not a new search


def test_haan_to_land_ownership_is_saved_as_true() -> None:
    """A plain boolean field takes the answer directly."""
    result = quick_answer("haan", "owns_cultivable_land")
    assert result is not None
    assert result.profile_updates == UserProfile(owns_cultivable_land=True)
    assert result.language_style is LanguageStyle.HINGLISH


def test_no_to_the_pension_question_means_zero() -> None:
    """The pension question says "Enter 0 if none", so a bare "no" means 0."""
    result = quick_answer("नहीं", "monthly_pension")
    assert result is not None
    assert result.profile_updates == UserProfile(monthly_pension=0)


@pytest.mark.parametrize(
    ("message", "field"),
    [
        ("yes", "monthly_pension"),  # needs an amount
        ("no", "age"),  # a number field
        ("yes", "state"),
        ("no", None),  # no question was asked
        ("no", "exclusion:not_a_real_category"),
    ],
)
def test_cases_a_yes_no_cannot_answer_go_to_gemini(message: str, field: str | None) -> None:
    """Return None so the normal Gemini path handles it."""
    assert quick_answer(message, field) is None


def test_handle_message_skips_the_gemini_understand_call_for_a_bare_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the question's field known, "No" costs no understand call, and is still saved."""
    understand_mock = MagicMock(side_effect=AssertionError("Gemini understand must not be called"))
    monkeypatch.setattr(assistant, "understand_message", understand_mock)
    monkeypatch.setattr(assistant, "generate_text", MagicMock(return_value="ok"))
    monkeypatch.setattr(assistant, "search_schemes", MagicMock(return_value=[]))

    profile = UserProfile(age=35, owns_cultivable_land=True, landholding_hectares=1.0)
    result = assistant.handle_message(
        "No", profile=profile, last_question="Did you pay income tax?", matched_scheme_ids=["pm-kisan"],
        last_question_field=_INCOME_TAX,
    )  # fmt: skip

    assert result.understood_by == "quick_answer"
    assert result.profile.exclusion_answer(ExclusionCategory.INCOME_TAX_PAYER) is False
    assert result.matched_scheme_ids == ["pm-kisan"]  # earlier match kept, no new search
    assert result.language_style is LanguageStyle.ENGLISH


def test_handle_message_uses_gemini_when_the_field_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a known question field, even "no" goes through Gemini as before."""
    from backend.services.understand import UnderstandingResult

    understand_mock = MagicMock(return_value=UnderstandingResult(language_style=LanguageStyle.ENGLISH))
    monkeypatch.setattr(assistant, "understand_message", understand_mock)
    monkeypatch.setattr(assistant, "generate_text", MagicMock(return_value="ok"))
    monkeypatch.setattr(assistant, "search_schemes", MagicMock(return_value=[SchemeMatch(scheme_id="pm-kisan", category="agriculture", score=0.5)]))

    result = assistant.handle_message("No")

    assert understand_mock.call_count == 1
    assert result.understood_by == "gemini"
