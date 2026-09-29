"""Answer a short yes/no reply in plain Python, without the Gemini "understand" call.

Most turns in a conversation are just "no" or "haan" to the question we asked last. When
we know that question's field key, the answer is fully determined, so spending one of the
few free Gemini requests per day on it would be waste. Anything longer or less clear
("no, but my wife pays tax") still goes to Gemini.
"""

from __future__ import annotations

import re

from backend.models.scheme import ExclusionCategory
from backend.models.user_profile import UserProfile
from backend.services.understand import BOOLEAN_PROFILE_FIELDS, LanguageStyle, UnderstandingResult

_EXCLUSION_FIELD_PREFIX = "exclusion:"
_PENSION_FIELD = "monthly_pension"

# word -> (answer, language style it was written in)
_YES_NO_WORDS: dict[str, tuple[bool, LanguageStyle]] = {
    **{word: (True, LanguageStyle.ENGLISH) for word in ("yes", "y", "yeah", "yep")},
    **{word: (False, LanguageStyle.ENGLISH) for word in ("no", "n", "nope")},
    **{word: (True, LanguageStyle.HINGLISH) for word in ("haan", "han", "haa", "ha", "haan ji", "ha ji", "ji haan")},
    **{word: (False, LanguageStyle.HINGLISH) for word in ("nahi", "nahin", "nhi", "nai", "na", "nahi ji", "ji nahi")},
    **{word: (True, LanguageStyle.HINDI) for word in ("हाँ", "हां", "हा", "हाँ जी", "जी हाँ", "जी हां")},
    **{word: (False, LanguageStyle.HINDI) for word in ("नहीं", "नही", "ना", "न", "जी नहीं", "नहीं जी")},
}

_EDGE_PUNCTUATION = " \t\n.,!?।'\""


def parse_yes_no(message: str) -> tuple[bool, LanguageStyle] | None:
    """Return (answer, language style) if the whole message is just a yes or a no."""
    text = re.sub(r"\s+", " ", message.strip(_EDGE_PUNCTUATION).lower())
    return _YES_NO_WORDS.get(text)


def _updates_for(field: str, answer: bool) -> UserProfile | None:
    """The profile update a yes/no answer means for this question field, or None if a
    yes/no alone cannot answer it (e.g. "yes" to "what is your pension amount?")."""
    if field.startswith(_EXCLUSION_FIELD_PREFIX):
        category = field.removeprefix(_EXCLUSION_FIELD_PREFIX)
        if category in {c.value for c in ExclusionCategory}:
            return UserProfile(exclusions={category: answer})
        return None
    if field in BOOLEAN_PROFILE_FIELDS:
        return UserProfile.model_validate({field: answer})
    if field == _PENSION_FIELD and answer is False:
        return UserProfile(monthly_pension=0)  # the question itself says "Enter 0 if none"
    return None


def quick_answer(message: str, last_question_field: str | None) -> UnderstandingResult | None:
    """Understand a bare yes/no reply to the last question without calling Gemini.

    Returns None (use the Gemini path) unless the last question's field is known, the
    message is only a yes or a no, and that alone answers the question. The result has
    empty search queries, which marks the turn as an answer rather than a new search.
    """
    if not last_question_field:
        return None
    parsed = parse_yes_no(message)
    if parsed is None:
        return None
    answer, language_style = parsed
    updates = _updates_for(last_question_field, answer)
    if updates is None:
        return None
    return UnderstandingResult(profile_updates=updates, language_style=language_style)
