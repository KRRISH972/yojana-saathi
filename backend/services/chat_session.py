"""Runs one chat turn for the API: unpack the browser's state, call the assistant, and
pack the reply, scheme cards, and next state into a ChatResponse."""

from __future__ import annotations

from backend.models.chat import ChatRequest, ChatResponse, ConversationState, SchemeCard
from backend.services.assistant import ChatTurnResult, handle_message
from backend.services.eligibility import SchemeResult
from backend.services.scheme_store import schemes_by_id


def _scheme_cards(result: ChatTurnResult) -> list[SchemeCard]:
    """One card per matched scheme: eligible first, then possibly eligible, then not eligible."""
    report = result.eligibility
    ordered: list[SchemeResult] = report.eligible + report.possibly_eligible + report.not_eligible
    known = schemes_by_id()
    return [
        SchemeCard(
            scheme_id=r.scheme_id,
            name_en=known[r.scheme_id].name_en,
            name_hi=known[r.scheme_id].name_hi,
            status=r.status,
            reasons=r.reasons,
            benefits=known[r.scheme_id].benefits,
            official_url=str(known[r.scheme_id].official_url),
        )
        for r in ordered
        if r.scheme_id in known
    ]


def run_chat_turn(request: ChatRequest) -> ChatResponse:
    """Handle one API chat message and return everything the browser needs."""
    state = request.state
    result = handle_message(
        request.message,
        profile=state.profile,
        last_question=state.last_question,
        matched_scheme_ids=state.matched_scheme_ids,
        last_question_field=state.last_question_field,
    )
    return ChatResponse(
        reply=result.reply_text,
        language_style=result.language_style,
        schemes=_scheme_cards(result),
        next_question=result.next_question,
        understood_by=result.understood_by,
        state=ConversationState(
            profile=result.profile,
            last_question=result.next_question,
            last_question_field=result.next_question_field,
            matched_scheme_ids=result.matched_scheme_ids,
        ),
    )
