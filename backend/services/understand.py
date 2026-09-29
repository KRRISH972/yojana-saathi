"""The first of the two Gemini calls per chat turn: turn one raw user message into
structured data the rest of the app can act on, without Gemini ever deciding eligibility.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.models.user_profile import UserProfile, invalid_field_names
from backend.services.llm import generate_structured

logger = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = """\
You are the understanding layer of Yojana Saathi, an assistant that helps Indian \
citizens find government schemes. You never decide whether someone is eligible for \
anything — a separate program does that. Your only job is to read the user's latest \
message (and, if given, the question they were just asked) and extract four things:

1. profile_updates: ONLY facts the user actually stated in THIS message. Never guess, \
infer, or assume a value they did not say, even if it seems likely. Leave every field \
you are not sure about as null (or, for exclusion answers, simply omit that key). If the \
message answers a yes/no question you were told was just asked, record that answer under \
the matching field. Whenever the user states how much land they own, ALWAYS fill \
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

User's latest message:
{message}
"""


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


def _drop_nulls(raw_updates: dict[str, Any]) -> dict[str, Any]:
    """Remove null values, including null exclusion answers. Gemini (especially Flash-Lite)
    often writes "not mentioned" as an explicit null, which means the same as leaving the
    key out; a null exclusion answer would otherwise fail validation as "not a bool"."""
    cleaned = {field: value for field, value in raw_updates.items() if value is not None}
    exclusions = cleaned.get("exclusions")
    if isinstance(exclusions, dict):
        cleaned["exclusions"] = {category: answer for category, answer in exclusions.items() if answer is not None}
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
    cleaned = _drop_nulls(raw_updates)
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


def understand_message(message: str, last_question: str | None = None) -> UnderstandingResult:
    """Run the one structured Gemini call for a single user message.

    ``last_question`` should be the exact question text the assistant most recently asked
    (from EligibilityReport.questions), if any — it lets a short reply like "yes" or
    "2 hectares" be attributed to the right UserProfile field.

    Gemini is asked for UnderstandingResult's exact schema, but the reply is parsed in two
    steps: if only profile_updates is invalid, this turn is treated as having no profile
    updates, and the search queries and language style are still used.
    """
    prompt = _PROMPT_TEMPLATE.format(last_question=last_question or "(none)", message=message)
    raw = generate_structured(
        prompt,
        response_model=_RawUnderstanding,
        system_instruction=_SYSTEM_INSTRUCTION,
        schema=UnderstandingResult.model_json_schema(),
    )
    return UnderstandingResult(
        profile_updates=_validate_profile_updates(raw.profile_updates),
        search_query_en=raw.search_query_en,
        search_query_hi=raw.search_query_hi,
        language_style=raw.language_style,
    )
