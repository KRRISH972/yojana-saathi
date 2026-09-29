"""Orchestrates one chat turn: understand the message, merge the profile, search for
matching schemes, run the (Python, rule-based) eligibility engine, then ask Gemini to
write the reply. Gemini never decides eligibility — it only explains what Python decided.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from backend.models.scheme import Scheme
from backend.models.user_profile import UserProfile
from backend.services.eligibility import EligibilityReport, EligibilityStatus, SchemeResult, check_eligibility
from backend.services.llm import generate_text
from backend.services.retriever import SchemeMatch, search_schemes
from backend.services.understand import understand_message

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMES_JSON = PROJECT_ROOT / "data" / "schemes.json"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "backend" / "prompts" / "system_prompt.md"

logger = logging.getLogger(__name__)

SEARCH_TOP_K = 5
SEARCH_SCORE_THRESHOLD = 0.35  # below this, a match is treated as "not actually relevant" (see Step 3)

_STATUS_LABELS: dict[EligibilityStatus, str] = {
    EligibilityStatus.ELIGIBLE: "ELIGIBLE",
    EligibilityStatus.POSSIBLY_ELIGIBLE: "POSSIBLY ELIGIBLE (more information needed)",
    EligibilityStatus.NOT_ELIGIBLE: "NOT ELIGIBLE",
}


class ChatTurnResult(BaseModel):
    """Everything one call to handle_message produces."""

    reply_text: str
    profile: UserProfile
    eligibility: EligibilityReport
    matched_scheme_ids: list[str]
    next_question: str | None = None


@lru_cache
def _load_system_prompt() -> str:
    """Read backend/prompts/system_prompt.md, cached after the first read."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache
def _load_all_schemes() -> tuple[Scheme, ...]:
    """Load every scheme from data/schemes.json, cached after the first read."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    return tuple(Scheme.model_validate(entry) for entry in entries)


def _clear_contradicted_fields(merged: dict[str, object], updated_fields: set[str]) -> None:
    """Clear any older value that contradicts a newer answer, in place: the newer answer wins.

    ``updated_fields`` are the fields stated this turn. ``updates`` is itself a valid
    UserProfile, so the two sides of a contradiction can never both be new — one side is
    always an older value, and that is the one cleared (back to "unknown", never guessed).
    Add a rule here for every new cross-field check added to UserProfile.
    """
    if merged["owns_cultivable_land"] is False and merged["landholding_hectares"] is not None:
        if "owns_cultivable_land" in updated_fields:
            merged["landholding_hectares"] = None  # "I own no land" now overrides an older land size
        else:
            merged["owns_cultivable_land"] = None  # a new land size overrides an older "no land"


def _merge_profile(base: UserProfile, updates: UserProfile) -> UserProfile:
    """Copy every fact stated in ``updates`` onto ``base``.

    A field left ``None`` in ``updates`` (or an exclusion category with no answer) means
    "not mentioned this turn" and must never overwrite something already known. If a new
    answer contradicts an older one, the newer answer wins and the older value is cleared.
    As a last-resort safety net, if the merged profile still fails validation, the previous
    profile is kept and a warning is logged, so one bad turn never crashes the conversation.
    """
    merged = base.model_dump()
    updated_fields: set[str] = set()
    for field, value in updates.model_dump().items():
        if field == "exclusions":
            new_answers = {category: answer for category, answer in value.items() if answer is not None}
            merged["exclusions"] = {**merged.get("exclusions", {}), **new_answers}
        elif value is not None:
            merged[field] = value
            updated_fields.add(field)
    _clear_contradicted_fields(merged, updated_fields)
    try:
        return UserProfile.model_validate(merged)
    except ValidationError as exc:
        # Log only field names, never values: they come from what the user typed.
        fields = sorted({".".join(str(part) for part in err["loc"]) or "(profile)" for err in exc.errors()})
        logger.warning("Profile merge failed validation on %s; keeping the previous profile.", ", ".join(fields))
        return base


def _search_both_queries(query_en: str, query_hi: str, top_k: int = SEARCH_TOP_K) -> list[SchemeMatch]:
    """Search with the English and Hindi queries, keep each scheme's best score across
    both, drop anything below SEARCH_SCORE_THRESHOLD, and sort best-match first."""
    best_by_scheme: dict[str, SchemeMatch] = {}
    for query in (query_en, query_hi):
        if not query.strip():
            continue
        for match in search_schemes(query, top_k=top_k):
            if match.score < SEARCH_SCORE_THRESHOLD:
                continue
            current_best = best_by_scheme.get(match.scheme_id)
            if current_best is None or match.score > current_best.score:
                best_by_scheme[match.scheme_id] = match
    return sorted(best_by_scheme.values(), key=lambda m: m.score, reverse=True)


def _resolve_matched_scheme_ids(query_en: str, query_hi: str, previous_matched_scheme_ids: list[str]) -> list[str]:
    """Decide which schemes are "in play" this turn, combining this turn's search with
    whatever was already matched in earlier turns of the same conversation.

    If both queries are empty, this message is not itself a new scheme search — Gemini
    marks it that way when a message only answers a previous question (e.g. "no"). In
    that case we must not search at all (a bare "no" matches nothing and would otherwise
    wipe out everything found so far); we simply re-check the schemes already on the
    table. Otherwise, this turn's search results are combined with (not replacing) the
    earlier matches, so a scheme found in an earlier turn is never silently dropped just
    because a later message's search phrase does not happen to mention it again.
    """
    if not query_en.strip() and not query_hi.strip():
        return list(previous_matched_scheme_ids)

    new_matches = _search_both_queries(query_en, query_hi)
    combined_ids = [m.scheme_id for m in new_matches]
    for scheme_id in previous_matched_scheme_ids:
        if scheme_id not in combined_ids:
            combined_ids.append(scheme_id)
    return combined_ids


def _format_scheme_result(result: SchemeResult, scheme: Scheme | None) -> str:
    """One scheme's status, reasons and (if relevant) official link, as plain text for the
    reply-writing prompt."""
    lines = [f"- {result.scheme_name} ({result.scheme_id}): {_STATUS_LABELS[result.status]}"]
    for reason in result.reasons:
        lines.append(f"    reason: {reason}")
    if scheme is not None and result.status is not EligibilityStatus.NOT_ELIGIBLE:
        lines.append(f"    official link: {scheme.official_url}")
        lines.append(f"    benefits: {scheme.benefits}")
    return "\n".join(lines)


def _build_reply_prompt(
    message: str, language_style: str, report: EligibilityReport, schemes_by_id: dict[str, Scheme]
) -> tuple[str, str | None]:
    """Build the input text for the reply-writing Gemini call, and pick the single
    follow-up question (if any) it is allowed to ask."""
    all_results = report.eligible + report.possibly_eligible + report.not_eligible
    if not all_results:
        scheme_section = "(No matching scheme was found for this question.)"
    else:
        scheme_section = "\n".join(_format_scheme_result(r, schemes_by_id.get(r.scheme_id)) for r in all_results)

    next_question = report.questions[0].question if report.questions else None
    question_section = next_question or "(none - do not ask a question this turn)"

    prompt = (
        f"User's language style: {language_style}\n\n"
        f"User's message: {message}\n\n"
        f"Scheme results (already decided by Python, do not change these):\n{scheme_section}\n\n"
        f"Follow-up question you may ask (at most this one, or none): {question_section}\n"
    )
    return prompt, next_question


def handle_message(
    message: str,
    profile: UserProfile | None = None,
    last_question: str | None = None,
    matched_scheme_ids: list[str] | None = None,
) -> ChatTurnResult:
    """Run one full chat turn and return the reply plus the updated state.

    ``profile`` is the profile accumulated so far (an empty UserProfile for a new
    conversation); ``last_question`` is the exact question text most recently asked, used
    to interpret a short answer like "yes" correctly; ``matched_scheme_ids`` are the
    scheme ids already in play from earlier turns (empty/None for a new conversation).
    The caller (e.g. scripts/chat_cli.py) is expected to keep passing back
    ``result.matched_scheme_ids`` on the next call, the same way it already does for
    ``profile`` and ``last_question``.
    """
    profile = profile if profile is not None else UserProfile()

    understanding = understand_message(message, last_question=last_question)
    merged_profile = _merge_profile(profile, understanding.profile_updates)

    all_matched_ids = _resolve_matched_scheme_ids(
        understanding.search_query_en, understanding.search_query_hi, matched_scheme_ids or []
    )
    all_schemes_by_id = {scheme.id: scheme for scheme in _load_all_schemes()}
    matched_schemes = [all_schemes_by_id[scheme_id] for scheme_id in all_matched_ids if scheme_id in all_schemes_by_id]

    report = check_eligibility(merged_profile, matched_schemes)

    reply_prompt, next_question = _build_reply_prompt(
        message, understanding.language_style.value, report, all_schemes_by_id
    )
    reply_text = generate_text(reply_prompt, system_instruction=_load_system_prompt())

    return ChatTurnResult(
        reply_text=reply_text,
        profile=merged_profile,
        eligibility=report,
        matched_scheme_ids=[s.id for s in matched_schemes],
        next_question=next_question,
    )
