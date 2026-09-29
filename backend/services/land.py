"""Read a stated land size (in hectares or acres) straight from the user's message.

gemini-3.5-flash-lite reliably extracts age, occupation and land ownership, but in real
runs it often left out the land size, in English, Hindi and Hinglish alike. A number
followed by a clear unit is easy and exact to read in Python, and converting acres to
hectares is arithmetic an LLM should not be trusted with, so we do it here.

Only hectares and acres are read. Local units (bigha, kanal, biswa, guntha, ...) differ
from state to state, so they are never converted — the same rule the Gemini prompt uses.
If a message mentions more than one land size, it is ambiguous and left to Gemini.
"""

from __future__ import annotations

import re
import unicodedata

HECTARES_PER_ACRE = 0.404686

_HECTARE_UNITS = ("hectares", "hectare", "hectre", "hektare", "hekteyar", "हेक्टेयर", "हैक्टेयर", "हेक्टर")
_ACRE_UNITS = ("acres", "acre", "ekad", "ekar", "ekarh", "एकड़", "एकड")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _normalize(text: str) -> str:
    """NFC-normalize (so both spellings of ड़ compare equal), lowercase, and turn
    Devanagari digits into ASCII digits."""
    return unicodedata.normalize("NFC", text).lower().translate(_DEVANAGARI_DIGITS)


def _unit_pattern(units: tuple[str, ...]) -> str:
    """A regex alternation of unit words, longest first so 'hectares' beats 'hectare'."""
    normalized = sorted((_normalize(u) for u in units), key=len, reverse=True)
    return "|".join(re.escape(u) for u in normalized)


_LAND_SIZE = re.compile(
    rf"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:(?P<ha>{_unit_pattern(_HECTARE_UNITS)})|(?P<acre>{_unit_pattern(_ACRE_UNITS)}))"
    r"(?![a-z])"
)


def extract_land_hectares(message: str) -> float | None:
    """Return the land size stated in the message, in hectares, or None.

    None when no hectare/acre amount is stated, when more than one is (ambiguous), or
    when the amount is zero.
    """
    matches = list(_LAND_SIZE.finditer(_normalize(message)))
    if len(matches) != 1:
        return None
    match = matches[0]
    amount = float(match.group(1))
    hectares = amount if match.group("ha") else amount * HECTARES_PER_ACRE
    return round(hectares, 6) if hectares > 0 else None
