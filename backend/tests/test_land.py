"""Tests for reading a stated land size from the message in plain Python."""

from __future__ import annotations

import pytest

from backend.models.user_profile import UserProfile
from backend.services import understand
from backend.services.land import HECTARES_PER_ACRE, extract_land_hectares


@pytest.mark.parametrize(
    ("message", "hectares"),
    [
        ("I am a 35 year old farmer with 1 hectare of land", 1.0),  # the exact e2e messages
        ("मैं 40 साल का किसान हूँ और मेरे पास 2 एकड़ ज़मीन है", 2 * HECTARES_PER_ACRE),
        ("Main 30 saal ka kisan hoon, mere paas 1 hectare zameen hai", 1.0),
        ("I own 2.5 hectares", 2.5),
        ("I have 3 acres", 3 * HECTARES_PER_ACRE),
        ("mere paas 2 acre khet hai", 2 * HECTARES_PER_ACRE),
        ("mere paas 4 ekad zameen hai", 4 * HECTARES_PER_ACRE),
        ("मेरे पास २ हेक्टेयर ज़मीन है", 2.0),  # Devanagari digits
        ("I have 1.5hectares", 1.5),  # no space
        ("मेरे पास 2 एकड ज़मीन है", 2 * HECTARES_PER_ACRE),  # ड without the nukta
    ],
)
def test_reads_hectares_and_acres_in_english_hindi_and_hinglish(message: str, hectares: float) -> None:
    """A number with a clear hectare/acre unit is read and acres are converted exactly."""
    assert extract_land_hectares(message) == pytest.approx(hectares, abs=1e-6)


@pytest.mark.parametrize(
    "message",
    [
        "I have 5 bigha of land",  # local unit: never converted
        "mere paas 10 kanal zameen hai",
        "I am 35 years old",  # a number, but no land unit
        "I have land",
        "I have 2 acres and my brother has 3 acres",  # ambiguous: two amounts
        "I have 0 hectares",
        "",
    ],
)
def test_leaves_local_units_ambiguous_and_missing_sizes_to_gemini(message: str) -> None:
    """Anything that is not exactly one clear hectare/acre amount returns None."""
    assert extract_land_hectares(message) is None


def _understand_with(monkeypatch: pytest.MonkeyPatch, message: str, profile_updates: dict) -> UserProfile:
    """Run understand_message against a mocked Gemini reply with these profile_updates."""
    reply = {"language_style": "english", "profile_updates": profile_updates}
    monkeypatch.setattr(understand, "generate_structured", lambda prompt, response_model, **_: response_model.model_validate(reply))
    return understand.understand_message(message).profile_updates


def test_fills_the_land_size_gemini_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: the exact real Flash-Lite reply (no landholding_hectares) now ends
    up with the land size the user stated."""
    flash_lite = {"age": 35, "occupation": "farmer", "owns_cultivable_land": True, "state": None}
    profile = _understand_with(monkeypatch, "I am a 35 year old farmer with 1 hectare of land", flash_lite)
    assert profile == UserProfile(age=35, occupation="farmer", owns_cultivable_land=True, landholding_hectares=1.0)


def test_exact_python_conversion_replaces_a_rounded_gemini_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python's exact acre conversion wins over Gemini's own arithmetic."""
    profile = _understand_with(monkeypatch, "I have 2 acres", {"owns_cultivable_land": True, "landholding_hectares": 0.8})
    assert profile.landholding_hectares == pytest.approx(2 * HECTARES_PER_ACRE)


def test_no_land_size_is_added_when_the_user_owns_no_land(monkeypatch: pytest.MonkeyPatch) -> None:
    """"I don't own the 2 acres I farm" must not create a contradiction."""
    profile = _understand_with(monkeypatch, "I don't own the 2 acres I farm", {"owns_cultivable_land": False})
    assert profile == UserProfile(owns_cultivable_land=False)
