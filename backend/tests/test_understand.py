"""Tests for the single 'understand' Gemini call, with Gemini itself mocked out."""

from __future__ import annotations

from typing import Any

import pytest

from backend.models.user_profile import UserProfile
from backend.services import understand
from backend.services.understand import LanguageStyle, UnderstandingResult, understand_message

_GOOD_RESPONSE: dict[str, Any] = {
    "search_query_en": "pension for farmers",
    "search_query_hi": "किसानों के लिए पेंशन",
    "language_style": "hindi",
}


def _patch_gemini(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> list[dict]:
    """Patch understand.generate_structured to parse ``response`` with the requested model,
    as the real function would parse Gemini's JSON, and record each call."""
    calls: list[dict] = []

    def fake(prompt: str, response_model, system_instruction: str | None = None, schema=None):
        calls.append({"prompt": prompt, "system_instruction": system_instruction, "schema": schema})
        return response_model.model_validate(response)

    monkeypatch.setattr(understand, "generate_structured", fake)
    return calls


@pytest.fixture
def mock_generate_structured(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Patch Gemini to return a canned, valid response and record its call."""
    return _patch_gemini(monkeypatch, _GOOD_RESPONSE)


def test_understand_message_returns_the_gemini_result(mock_generate_structured: list[dict]) -> None:
    """understand_message is a thin wrapper: it returns exactly what Gemini produced."""
    result = understand_message("मुझे किसान पेंशन चाहिए")
    assert result.search_query_en == "pension for farmers"
    assert result.language_style is LanguageStyle.HINDI


def test_prompt_includes_the_message_and_response_schema(mock_generate_structured: list[dict]) -> None:
    """The prompt sent to Gemini must contain the user's message, and Gemini must be asked
    for UnderstandingResult's exact (strict) schema, even though parsing is lenient."""
    understand_message("मुझे किसान पेंशन चाहिए")
    call = mock_generate_structured[0]
    assert "मुझे किसान पेंशन चाहिए" in call["prompt"]
    assert call["schema"] == UnderstandingResult.model_json_schema()
    assert call["system_instruction"] is not None


def test_last_question_is_included_when_given(mock_generate_structured: list[dict]) -> None:
    """A previous question should be passed through so short answers can be attributed."""
    understand_message("yes", last_question="Do you own cultivable land?")
    assert "Do you own cultivable land?" in mock_generate_structured[0]["prompt"]


def test_no_last_question_is_marked_clearly(mock_generate_structured: list[dict]) -> None:
    """With no prior question, the prompt should say so rather than leaving it blank."""
    understand_message("hello")
    assert "(none)" in mock_generate_structured[0]["prompt"]


def test_system_instruction_only_allows_converting_acres_and_hectares(mock_generate_structured: list[dict]) -> None:
    """Local land units (bigha, kanal, biswa, ...) differ by state, so the instruction
    given to Gemini must forbid guessing a hectare conversion for them, and only allow
    acres (converted) and hectares (as-is)."""
    understand_message("I have 5 bigha of land")
    instruction = mock_generate_structured[0]["system_instruction"]

    assert "acres" in instruction
    assert "hectares" in instruction
    for local_unit in ("bigha", "kanal", "biswa"):
        assert local_unit in instruction
    assert "do NOT convert" in instruction
    assert "leave landholding_hectares null" in instruction.lower()


def test_valid_profile_updates_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid profile_updates object comes through as a real UserProfile."""
    _patch_gemini(monkeypatch, {**_GOOD_RESPONSE, "profile_updates": {"age": 35, "owns_cultivable_land": True}})
    result = understand_message("मेरी उम्र 35 है और मेरे पास ज़मीन है")
    assert result.profile_updates == UserProfile(age=35, owns_cultivable_land=True)


def test_contradictory_profile_updates_are_dropped_but_queries_kept(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression test: Gemini returning "no land" and a land size in one response used
    to crash the whole turn with a ValidationError. Now the profile updates are ignored
    for this turn, while the search queries and language style are still used."""
    _patch_gemini(
        monkeypatch,
        {**_GOOD_RESPONSE, "profile_updates": {"owns_cultivable_land": False, "landholding_hectares": 2.0}},
    )

    with caplog.at_level("WARNING", logger=understand.__name__):
        result = understand_message("my-private-message-text")

    assert result.profile_updates == UserProfile()
    assert result.search_query_en == "pension for farmers"
    assert result.search_query_hi == "किसानों के लिए पेंशन"
    assert result.language_style is LanguageStyle.HINDI
    assert "ignoring them this turn" in caplog.text
    assert "my-private-message-text" not in caplog.text


def test_invalid_profile_log_names_fields_but_never_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning names the real field that failed, but never its value, and never a
    made-up key (which could have been copied from what the user typed)."""
    _patch_gemini(
        monkeypatch, {**_GOOD_RESPONSE, "profile_updates": {"age": 500, "ramesh_from_sitapur": "x"}}
    )

    with caplog.at_level("WARNING", logger=understand.__name__):
        result = understand_message("hello")

    assert result.profile_updates == UserProfile()
    assert "age" in caplog.text
    assert "(unknown field)" in caplog.text
    assert "500" not in caplog.text
    assert "ramesh_from_sitapur" not in caplog.text


# The exact profile_updates shapes gemini-3.5-flash-lite returned in a real run (2026-09-29)
# for "I am a 35 year old farmer with 1 hectare of land" and then "No" to the income-tax
# question. Note the explicit "state": null padding in the first one.
FLASH_LITE_TURN_ONE = {"age": 35, "occupation": "farmer", "owns_cultivable_land": True, "state": None}
FLASH_LITE_TURN_TWO = {"exclusions": {"income_tax_payer": False}}


def test_flash_lite_real_shapes_are_kept(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Regression test: both real Flash-Lite shapes must come through with every fact kept
    and no warning, including the explicit null padding."""
    with caplog.at_level("WARNING", logger=understand.__name__):
        _patch_gemini(monkeypatch, {**_GOOD_RESPONSE, "profile_updates": FLASH_LITE_TURN_ONE})
        turn_one = understand_message("I am a 35 year old farmer with 1 hectare of land")
        _patch_gemini(monkeypatch, {**_GOOD_RESPONSE, "profile_updates": FLASH_LITE_TURN_TWO})
        turn_two = understand_message("No")

    assert turn_one.profile_updates == UserProfile(age=35, occupation="farmer", owns_cultivable_land=True)
    assert turn_two.profile_updates == UserProfile(exclusions={"income_tax_payer": False})
    assert caplog.text == ""


def test_null_exclusion_answers_mean_not_answered(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Flash-Lite pads with nulls; a null exclusion answer means "not asked", and must not
    make the real "No" to the income-tax question be thrown away."""
    padded = {"age": None, "exclusions": {"income_tax_payer": False, "government_employee": None}}
    _patch_gemini(monkeypatch, {**_GOOD_RESPONSE, "profile_updates": padded})

    with caplog.at_level("WARNING", logger=understand.__name__):
        result = understand_message("No")

    assert result.profile_updates == UserProfile(exclusions={"income_tax_payer": False})
    assert caplog.text == ""


def test_one_bad_field_does_not_drop_the_other_facts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Root-cause regression: an invalid value in one field used to throw away every fact
    from the message. Now only that field is dropped (and logged by name, not value)."""
    updates = {"age": 35, "state": "India", "owns_cultivable_land": True, "landholding_hectares": 1.0}
    _patch_gemini(monkeypatch, {**_GOOD_RESPONSE, "profile_updates": updates})

    with caplog.at_level("WARNING", logger=understand.__name__):
        result = understand_message("I am a 35 year old farmer with 1 hectare of land")

    assert result.profile_updates == UserProfile(age=35, owns_cultivable_land=True, landholding_hectares=1.0)
    assert "state" in caplog.text
    assert "India" not in caplog.text


def test_system_instruction_insists_on_saving_the_land_size(mock_generate_structured: list[dict]) -> None:
    """Flash-Lite dropped "1 hectare" in a real run, so the instruction must say explicitly
    that a stated land size always fills landholding_hectares."""
    understand_message("I am a 35 year old farmer with 1 hectare of land")
    instruction = mock_generate_structured[0]["system_instruction"]
    assert "ALWAYS fill" in instruction
    assert "landholding_hectares = 1.0" in instruction
