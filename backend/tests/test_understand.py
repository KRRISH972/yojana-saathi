"""Tests for the single 'understand' Gemini call, with Gemini itself mocked out."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.models.scheme import ExclusionCategory
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
    for the explicit Gemini schema (see _gemini_response_schema), even though parsing is lenient."""
    understand_message("मुझे किसान पेंशन चाहिए")
    call = mock_generate_structured[0]
    assert "मुझे किसान पेंशन चाहिए" in call["prompt"]
    assert call["schema"] == understand._gemini_response_schema()
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

    # landholding_hectares comes from the message itself (backend/services/land.py), not Gemini
    assert turn_one.profile_updates == UserProfile(
        age=35, occupation="farmer", owns_cultivable_land=True, landholding_hectares=1.0
    )
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


# --- Exclusion answers: explicit schema, answer hint, lenient normalization ------------

_INCOME_TAX_QUESTION = "Did you, or anyone in your immediate family, pay income tax in the last assessment year?"


def _profile_from(monkeypatch: pytest.MonkeyPatch, profile_updates: Any) -> UserProfile:
    """Run understand_message against a mocked Gemini reply with these profile_updates."""
    _patch_gemini(monkeypatch, {**_GOOD_RESPONSE, "profile_updates": profile_updates})
    return understand_message("No", last_question=_INCOME_TAX_QUESTION, last_question_field="exclusion:income_tax_payer").profile_updates


def test_schema_lists_every_exclusion_key_explicitly(mock_generate_structured: list[dict]) -> None:
    """Gemini must see the exact allowed exclusion keys, each an optional true/false,
    instead of pydantic's propertyNames/additionalProperties form."""
    understand_message("hello")
    exclusions = mock_generate_structured[0]["schema"]["$defs"]["UserProfile"]["properties"]["exclusions"]

    assert "propertyNames" not in exclusions
    assert "additionalProperties" not in exclusions
    assert set(exclusions["properties"]) == {category.value for category in ExclusionCategory}
    for prop in exclusions["properties"].values():
        assert prop == {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
    assert UnderstandingResult.model_json_schema()["$defs"]["UserProfile"]["properties"]["exclusions"].get(
        "propertyNames"
    ), "the pydantic model's own schema must not be modified"


def test_prompt_gives_the_exact_key_and_value_for_an_exclusion_answer(mock_generate_structured: list[dict]) -> None:
    """With a last question, the prompt names its field key and exactly how to record yes/no."""
    understand_message("No", last_question=_INCOME_TAX_QUESTION, last_question_field="exclusion:income_tax_payer")
    prompt = mock_generate_structured[0]["prompt"]

    assert "Field key for that question: exclusion:income_tax_payer" in prompt
    assert 'profile_updates.exclusions = {"income_tax_payer": true} for yes' in prompt
    assert '{"income_tax_payer": false} for no' in prompt


def test_prompt_gives_the_field_for_a_plain_profile_answer(mock_generate_structured: list[dict]) -> None:
    """A non-exclusion question points at the plain profile field."""
    understand_message("yes", last_question="Do you own land?", last_question_field="owns_cultivable_land")
    assert "profile_updates.owns_cultivable_land" in mock_generate_structured[0]["prompt"]


def test_prompt_has_no_answer_hint_without_a_field(mock_generate_structured: list[dict]) -> None:
    """With no last question field, no key hint is added."""
    understand_message("hello")
    assert "Field key" not in mock_generate_structured[0]["prompt"]


@pytest.mark.parametrize(
    "profile_updates",
    [
        {"exclusions": {"income_tax_payer": False}},  # the correct shape
        {"exclusions": {"exclusion:income_tax_payer": False}},  # field key from our question list
        {"exclusion:income_tax_payer": False},  # prefixed key at the top level
        {"income_tax_payer": False},  # bare key at the top level
        {"exclusions": {"income_tax_payer": "no"}},  # yes/no string
        {"exclusions": {"income_tax_payer": "No"}},
        {"exclusions": {"income_tax_payer": "false"}},
        {"exclusions": {"Exclusion:Income_Tax_Payer": "FALSE"}},  # odd casing
    ],
)
def test_no_to_income_tax_is_saved_from_every_shape(monkeypatch: pytest.MonkeyPatch, profile_updates: dict) -> None:
    """Regression test: every shape Gemini might use for "No" to the income-tax question
    must be saved as income_tax_payer = False."""
    assert _profile_from(monkeypatch, profile_updates) == UserProfile(exclusions={"income_tax_payer": False})


@pytest.mark.parametrize("value", [True, "yes", "Yes", "true", "TRUE"])
def test_yes_strings_are_saved_as_true(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    """A "yes"/"true" answer in any form is saved as True."""
    profile = _profile_from(monkeypatch, {"exclusions": {"income_tax_payer": value}})
    assert profile.exclusion_answer(ExclusionCategory.INCOME_TAX_PAYER) is True


def test_unknown_exclusion_keys_are_ignored_one_by_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unknown key or an unusable value is ignored on its own; the valid answer and the
    other profile facts survive, and the warning shows neither the bad key nor the value."""
    updates = {
        "age": 35,
        "exclusions": {"income_tax_payer": False, "tax_payer_sitapur": False, "government_employee": "maybe"},
    }
    with caplog.at_level("WARNING", logger=understand.__name__):
        profile = _profile_from(monkeypatch, updates)

    assert profile == UserProfile(age=35, exclusions={"income_tax_payer": False})
    assert "2 unusable exclusion answer(s)" in caplog.text
    assert "tax_payer_sitapur" not in caplog.text
    assert "maybe" not in caplog.text


def test_exclusions_that_is_not_an_object_does_not_drop_other_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a completely wrong exclusions value (a list) only loses the exclusions."""
    assert _profile_from(monkeypatch, {"age": 35, "exclusions": ["income_tax_payer"]}) == UserProfile(age=35)


def test_raw_profile_updates_are_logged_only_when_debug_raw_is_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The raw JSON is logged at DEBUG on a separate logger, so it never shows by default."""
    raw = {"exclusions": {"exclusion:income_tax_payer": "no"}}

    with caplog.at_level("WARNING", logger=understand.__name__):
        _profile_from(monkeypatch, raw)
    assert "raw profile_updates" not in caplog.text

    caplog.clear()
    with caplog.at_level("DEBUG", logger=understand.raw_logger.name):
        _profile_from(monkeypatch, raw)
    assert 'raw profile_updates: {"exclusions": {"exclusion:income_tax_payer": "no"}}' in caplog.text


def test_schema_has_no_exclusive_bounds(mock_generate_structured: list[dict]) -> None:
    """Regression test: Gemini does not support exclusiveMinimum, and Flash-Lite never
    filled landholding_hectares (the one field using it). The schema sent must use
    minimum instead, while Python still rejects 0 hectares."""
    understand_message("I am a farmer with 1 hectare of land")
    schema_text = json.dumps(mock_generate_structured[0]["schema"])
    hectares = mock_generate_structured[0]["schema"]["$defs"]["UserProfile"]["properties"]["landholding_hectares"]

    assert "exclusiveMinimum" not in schema_text
    assert "exclusiveMaximum" not in schema_text
    assert {"minimum": 0, "type": "number"} in hectares["anyOf"]
    with pytest.raises(ValueError):
        UserProfile(landholding_hectares=0)


def test_numeric_question_hint_keeps_a_bare_no_from_changing_other_fields(mock_generate_structured: list[dict]) -> None:
    """Regression test: Flash-Lite turned "No" to the hectares question into "owns no
    land". For a number question the hint must say a bare yes/no gives no value."""
    understand_message(
        "No", last_question="How many hectares of cultivable land do you own?", last_question_field="landholding_hectares"
    )
    prompt = mock_generate_structured[0]["prompt"]
    assert "profile_updates.landholding_hectares" in prompt
    assert "do not change any other field" in prompt
