"""The first of the two Gemini calls per chat turn: turn one raw user message into
structured data the rest of the app can act on, without Gemini ever deciding eligibility.
"""

from __future__ import annotations

import copy
import json
import logging
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.models.scheme import ExclusionCategory
from backend.models.user_profile import UserProfile, invalid_field_names
from backend.services.land import extract_land_hectares
from backend.services.llm import generate_structured

logger = logging.getLogger(__name__)
# Raw profile_updates exactly as Gemini returned them, at DEBUG level. Off unless a local
# debugging tool turns it on (scripts/chat_cli.py does, when YS_DEBUG_RAW=1).
raw_logger = logging.getLogger(f"{__name__}.raw")

_SYSTEM_INSTRUCTION = """\
You are the understanding layer of Yojana Saathi, an assistant that helps Indian \
citizens find government schemes. You never decide whether someone is eligible for \
anything — a separate program does that. Your only job is to read the user's latest \
message (and, if given, the question they were just asked) and extract four things:

1. profile_updates: ONLY facts the user actually stated in THIS message. Never guess, \
infer, or assume a value they did not say, even if it seems likely. Leave every field \
you are not sure about as null (or, for exclusion answers, simply omit that key). If the \
message answers a yes/no question you were told was just asked, record that answer under \
the matching field, as a JSON true/false. Exclusion answers go inside \
profile_updates.exclusions, keyed by the bare category name (for example \
{"income_tax_payer": false}), never with an "exclusion:" prefix. Whenever the user states how much land they own, ALWAYS fill \
landholding_hectares as well as owns_cultivable_land — for example, "I am a farmer with \
1 hectare of land" means owns_cultivable_land = true AND landholding_hectares = 1.0. If the \
user gives a land area in acres, convert it to hectares (1 acre = 0.404686 hectares) and \
store only the converted number in landholding_hectares. If the user already gives it in \
hectares, store that number as-is. For any other local land unit — bigha, kanal, biswa, guntha, or anything else that is not \
acres or hectares — do NOT convert it and do NOT guess a hectare figure: these units have \
different sizes in different states, so a wrong guess is worse than no answer. Leave \
landholding_hectares null in that case, even though the user did mention a land area.

2. search_query_en: a short, clear English phrase describing what government help the \
user is asking about. Use an empty string if this message is not about finding or asking \
about a scheme (for example, if it only answers a yes/no question).

3. search_query_hi: the same idea as search_query_en, written in simple, everyday Hindi \
(Devanagari script). Use an empty string under the same condition as search_query_en.

4. language_style: "hindi" if the user wrote in Devanagari script, "hinglish" if they \
wrote Hindi words using Roman/Latin letters, or "english" otherwise.
"""

_PROMPT_TEMPLATE = """\
Question the user was just asked (may be empty if this is the start of the conversation):
{last_question}
{answer_hint}
User's latest message:
{message}
"""

_EXCLUSION_FIELD_PREFIX = "exclusion:"
_EXCLUSION_KEYS = frozenset(category.value for category in ExclusionCategory)
BOOLEAN_PROFILE_FIELDS = frozenset({"owns_cultivable_land"})  # yes/no profile fields (not exclusions)
_TRUE_STRINGS = frozenset({"yes", "true"})
_FALSE_STRINGS = frozenset({"no", "false"})


class LanguageStyle(StrEnum):
    """The language style detected in the user's message."""

    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"


class UnderstandingResult(BaseModel):
    """Everything extracted from one user message by the single "understand" Gemini call."""

    model_config = ConfigDict(extra="forbid")

    profile_updates: UserProfile = Field(
        default_factory=UserProfile,
        description="Only facts stated in this message. Everything else stays null/empty.",
    )
    search_query_en: str = Field(default="", description="Clear English search query, or '' if not applicable.")
    search_query_hi: str = Field(default="", description="The same query in simple Hindi, or '' if not applicable.")
    language_style: LanguageStyle = Field(description="The language style to reply in.")


class _RawUnderstanding(BaseModel):
    """The same response as UnderstandingResult, but with profile_updates left unvalidated,
    so a bad profile can be dropped without losing the search queries and language style."""

    model_config = ConfigDict(extra="forbid")

    profile_updates: Any = None
    search_query_en: str = ""
    search_query_hi: str = ""
    language_style: LanguageStyle


def _replace_exclusive_bounds(node: Any) -> None:
    """Swap exclusiveMinimum/exclusiveMaximum for minimum/maximum, in place, everywhere.

    Gemini's structured output does not support the exclusive forms, and in real runs
    gemini-3.5-flash-lite never filled the one field that used one (landholding_hectares,
    which must be > 0). Python still enforces the strict bound when it validates.
    """
    if isinstance(node, dict):
        for exclusive, inclusive in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
            if exclusive in node:
                node[inclusive] = node.pop(exclusive)
        for value in node.values():
            _replace_exclusive_bounds(value)
    elif isinstance(node, list):
        for item in node:
            _replace_exclusive_bounds(item)


@lru_cache
def _gemini_response_schema() -> dict[str, Any]:
    """The JSON schema Gemini is asked to follow: UnderstandingResult's own schema, except
    that ``exclusions`` spells out one optional true/false property per ExclusionCategory.

    Pydantic describes the exclusions dict with ``propertyNames``, which does not show the
    model the exact keys to use (Flash-Lite answered with keys our model rejected), so
    every allowed key is listed explicitly instead.
    """
    schema = copy.deepcopy(UnderstandingResult.model_json_schema())
    _replace_exclusive_bounds(schema)
    exclusions = schema["$defs"]["UserProfile"]["properties"]["exclusions"]
    exclusions.pop("additionalProperties", None)
    exclusions.pop("propertyNames", None)
    exclusions["properties"] = {
        category.value: {"anyOf": [{"type": "boolean"}, {"type": "null"}]} for category in ExclusionCategory
    }
    return schema


def _answer_hint(last_question_field: str | None) -> str:
    """Tell Gemini exactly which key and value a short answer to the last question uses."""
    if not last_question_field:
        return ""
    if last_question_field.startswith(_EXCLUSION_FIELD_PREFIX):
        key = last_question_field.removeprefix(_EXCLUSION_FIELD_PREFIX)
        return (
            f"Field key for that question: {last_question_field}\n"
            f'If the message answers that question, record it as profile_updates.exclusions = {{"{key}": true}} '
            f'for yes, or {{"{key}": false}} for no. Use exactly the key "{key}" (without the '
            f'"{_EXCLUSION_FIELD_PREFIX}" prefix) and a JSON true/false, not a string.\n'
        )
    if last_question_field in BOOLEAN_PROFILE_FIELDS:
        return (
            f"Field key for that question: {last_question_field}\n"
            f"If the message answers that question, record the answer in profile_updates.{last_question_field} "
            f"(a yes/no answer is a JSON true/false, not a string).\n"
        )
    return (
        f"Field key for that question: {last_question_field}\n"
        f"If the message gives that value, record it in profile_updates.{last_question_field}. A bare "
        f'"yes" or "no" does not give this value: then leave it null, and do not change any other field '
        f"because of it.\n"
    )


def _to_bool(value: Any) -> bool | None:
    """A JSON true/false, or a "yes"/"no"/"true"/"false" string in any case; None otherwise."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
    return None


def _exclusion_key(key: Any) -> str | None:
    """The ExclusionCategory value a key names, accepting an "exclusion:" prefix; None if unknown."""
    if not isinstance(key, str):
        return None
    name = key.strip().lower().removeprefix(_EXCLUSION_FIELD_PREFIX).strip()
    return name if name in _EXCLUSION_KEYS else None


def _normalize_exclusions(raw_updates: dict[str, Any]) -> dict[str, bool]:
    """Collect exclusion answers leniently, one key at a time.

    Answers are read from ``exclusions`` and also from any top-level key that names an
    exclusion (e.g. "exclusion:income_tax_payer", the key shown in our question list). A
    null answer means "not answered". An unknown key or a value that is not yes/no is
    ignored on its own, never taking the other answers down with it.
    """
    nested = raw_updates.get("exclusions")
    pairs = list(nested.items()) if isinstance(nested, dict) else []
    pairs += [(key, value) for key, value in raw_updates.items() if key != "exclusions" and _exclusion_key(key)]
    ignored = 0 if isinstance(nested, dict) or nested is None else 1
    answers: dict[str, bool] = {}
    for key, value in pairs:
        if value is None:
            continue
        category, answer = _exclusion_key(key), _to_bool(value)
        if category is None or answer is None:
            ignored += 1
            continue
        answers[category] = answer
    if ignored:
        logger.warning("Understand call returned %d unusable exclusion answer(s); ignored only those.", ignored)
    return answers


def _normalize_profile_updates(raw_updates: dict[str, Any]) -> dict[str, Any]:
    """Drop null values (Gemini, especially Flash-Lite, writes "not mentioned" as an
    explicit null) and gather exclusion answers leniently (see _normalize_exclusions)."""
    cleaned = {
        field: value
        for field, value in raw_updates.items()
        if value is not None and field != "exclusions" and not _exclusion_key(field)
    }
    exclusions = _normalize_exclusions(raw_updates)
    if exclusions:
        cleaned["exclusions"] = exclusions
    return cleaned


def _validate_profile_updates(raw_updates: Any) -> UserProfile:
    """Validate Gemini's profile_updates, keeping every valid fact.

    Only the fields that fail validation are dropped, so one bad value (e.g. an unknown
    state name) never throws away the other facts from the same message. If the updates
    are still invalid after that (e.g. "no land" and a land size in the same message, a
    cross-field error that names no single field), the whole update is ignored this turn.
    Every drop is logged with field names only, never values.
    """
    if raw_updates is None:
        return UserProfile()
    if not isinstance(raw_updates, dict):
        logger.warning("Understand call returned profile_updates that is not an object; ignoring it this turn.")
        return UserProfile()
    cleaned = _normalize_profile_updates(raw_updates)
    try:
        return UserProfile.model_validate(cleaned)
    except ValidationError as exc:
        bad_fields = invalid_field_names(exc)
    logger.warning("Understand call returned invalid profile_updates on %s; dropping those fields.", ", ".join(bad_fields))
    # Keep only real fields that did not fail (bad_fields names made-up keys as "(unknown field)").
    kept = {f: v for f, v in cleaned.items() if f in UserProfile.model_fields and f not in bad_fields}
    try:
        return UserProfile.model_validate(kept)
    except ValidationError as exc:
        fields = ", ".join(invalid_field_names(exc))
        logger.warning("Understand call's profile_updates still invalid on %s; ignoring them this turn.", fields)
        return UserProfile()


def _with_stated_land_size(updates: UserProfile, message: str) -> UserProfile:
    """Set landholding_hectares from a clearly stated hectare/acre amount in the message
    (see backend/services/land.py), which is exact where Gemini is not always reliable.
    Skipped if the user said they own no land, so no contradiction is ever created."""
    hectares = extract_land_hectares(message)
    if hectares is None or updates.owns_cultivable_land is False:
        return updates
    return updates.model_copy(update={"landholding_hectares": hectares})


def understand_message(
    message: str, last_question: str | None = None, last_question_field: str | None = None
) -> UnderstandingResult:
    """Run the one structured Gemini call for a single user message.

    ``last_question`` should be the exact question text the assistant most recently asked
    and ``last_question_field`` its field key (both from EligibilityReport.questions), if
    any — they let a short reply like "yes" or "2 hectares" be recorded under the right
    UserProfile field.

    Gemini is asked for an explicit schema (see _gemini_response_schema), but the reply is
    parsed leniently: invalid profile fields are dropped one by one, and the search
    queries and language style are still used.
    """
    prompt = _PROMPT_TEMPLATE.format(
        last_question=last_question or "(none)", answer_hint=_answer_hint(last_question_field), message=message
    )
    raw = generate_structured(
        prompt,
        response_model=_RawUnderstanding,
        system_instruction=_SYSTEM_INSTRUCTION,
        schema=_gemini_response_schema(),
    )
    if raw_logger.isEnabledFor(logging.DEBUG):
        raw_logger.debug("raw profile_updates: %s", json.dumps(raw.profile_updates, ensure_ascii=False))
    return UnderstandingResult(
        profile_updates=_with_stated_land_size(_validate_profile_updates(raw.profile_updates), message),
        search_query_en=raw.search_query_en,
        search_query_hi=raw.search_query_hi,
        language_style=raw.language_style,
    )
