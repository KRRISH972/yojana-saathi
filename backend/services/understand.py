"""The first of the two Gemini calls per chat turn: turn one raw user message into
structured data the rest of the app can act on, without Gemini ever deciding eligibility.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from backend.models.user_profile import UserProfile
from backend.services.llm import generate_structured

_SYSTEM_INSTRUCTION = """\
You are the understanding layer of Yojana Saathi, an assistant that helps Indian \
citizens find government schemes. You never decide whether someone is eligible for \
anything — a separate program does that. Your only job is to read the user's latest \
message (and, if given, the question they were just asked) and extract four things:

1. profile_updates: ONLY facts the user actually stated in THIS message. Never guess, \
infer, or assume a value they did not say, even if it seems likely. Leave every field \
you are not sure about as null (or, for exclusion answers, simply omit that key). If the \
message answers a yes/no question you were told was just asked, record that answer under \
the matching field. If the user gives a land area in acres, convert it to hectares \
(1 acre = 0.404686 hectares) and store only the converted number in \
landholding_hectares. If the user already gives it in hectares, store that number as-is. \
For any other local land unit — bigha, kanal, biswa, guntha, or anything else that is not \
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


def understand_message(message: str, last_question: str | None = None) -> UnderstandingResult:
    """Run the one structured Gemini call for a single user message.

    ``last_question`` should be the exact question text the assistant most recently asked
    (from EligibilityReport.questions), if any — it lets a short reply like "yes" or
    "2 hectares" be attributed to the right UserProfile field.
    """
    prompt = _PROMPT_TEMPLATE.format(last_question=last_question or "(none)", message=message)
    return generate_structured(prompt, response_model=UnderstandingResult, system_instruction=_SYSTEM_INSTRUCTION)
