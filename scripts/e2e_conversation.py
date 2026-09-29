"""CLI: a scripted end-to-end conversation that checks the saved profile after every turn.

Usage (from the project root):
    venv\\Scripts\\python scripts/e2e_conversation.py              # live: real Gemini calls
    venv\\Scripts\\python scripts/e2e_conversation.py --replay     # offline: recorded answers
    venv\\Scripts\\python scripts/e2e_conversation.py --with-replies   # live, reply calls too

The conversation:
  1. "I am a 35 year old farmer with 1 hectare of land"  -> age and land saved
  2. "No" to every follow-up question                    -> each answer saved, the next
                                                            question changes, and in the
                                                            end both schemes are eligible
  3. one Hindi and one Hinglish opening message           -> age and land saved

Live mode makes real Gemini "understand" calls (3 per run: the bare "No" turns are
answered in Python and cost nothing). The reply-writing call is stubbed unless
--with-replies is given, because the checks are on the profile, not the wording. Every
real call goes through a local daily ledger (.cache/gemini_calls.json, gitignored) that
refuses to go past DAILY_CALL_LIMIT and keeps calls MIN_SECONDS_BETWEEN_CALLS apart.

Live mode also records Gemini's raw answers to RECORDING_PATH, so --replay (and the
pytest test backend/tests/test_e2e_replay.py) can re-run the exact same conversation
offline, for free, whenever the pipeline has not changed.

Exit code is 0 if every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.scheme import ExclusionCategory  # noqa: E402
from backend.models.user_profile import UserProfile  # noqa: E402
from backend.services import assistant, llm  # noqa: E402
from backend.services.assistant import ChatTurnResult  # noqa: E402

LEDGER_PATH = PROJECT_ROOT / ".cache" / "gemini_calls.json"
RECORDING_PATH = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "e2e_recording.json"
DAILY_CALL_LIMIT = 20
MIN_SECONDS_BETWEEN_CALLS = 8.0
MAX_NO_TURNS = 12  # safety stop for the "keep answering No" loop

OPENING_EN = "I am a 35 year old farmer with 1 hectare of land"
OPENING_HI = "मैं 40 साल का किसान हूँ और मेरे पास 2 एकड़ ज़मीन है"
OPENING_HINGLISH = "Main 30 saal ka kisan hoon, mere paas 1 hectare zameen hai"
ACRES_2_IN_HECTARES = 2 * 0.404686


class QuotaGuardError(RuntimeError):
    """Making another real call would break the daily limit."""


class CallLedger:
    """Counts real Gemini calls per day in a local file, and spaces them out."""

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        """Load today's count (a new day starts at zero)."""
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        today = date.today().isoformat()
        self.count: int = data.get("count", 0) if data.get("date") == today else 0
        self.last_call_at: float = data.get("last_call_at", 0.0)
        self.today = today

    def before_call(self) -> None:
        """Refuse if the limit is reached, wait out the minimum spacing, then record the
        attempt (a failed or timed-out call still counts against the quota)."""
        if self.count >= DAILY_CALL_LIMIT:
            raise QuotaGuardError(f"Daily limit of {DAILY_CALL_LIMIT} real Gemini calls reached; try tomorrow.")
        wait = self.last_call_at + MIN_SECONDS_BETWEEN_CALLS - time.time()
        if wait > 0:
            time.sleep(wait)
        self.count += 1
        self.last_call_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"date": self.today, "count": self.count, "last_call_at": self.last_call_at}
        self.path.write_text(json.dumps(payload), encoding="utf-8")


@dataclass
class GeminiHarness:
    """Routes every Gemini call: through the ledger and into the recording (live), or
    straight from the recording (replay). Reply-writing is stubbed unless asked for."""

    replay: bool
    with_replies: bool = False
    recording: dict[str, str] = field(default_factory=dict)
    current_message: str = ""
    real_calls: int = 0
    ledger: CallLedger | None = None

    def install(self) -> None:
        """Patch llm._call_gemini (and, unless with_replies, the reply writer)."""
        if self.replay:
            self.recording = json.loads(RECORDING_PATH.read_text(encoding="utf-8"))
        else:
            self.ledger = CallLedger()
        real_call = llm._call_gemini

        def routed_call(**kwargs: object) -> object:
            is_understand = "response_format" in kwargs
            if self.replay:
                if not is_understand:
                    raise RuntimeError("replay mode has no recorded replies; do not use --with-replies")
                return SimpleNamespace(output_text=self.recording[self.current_message])
            assert self.ledger is not None
            self.ledger.before_call()
            self.real_calls += 1
            interaction = real_call(**kwargs)
            if is_understand:
                self.recording[self.current_message] = interaction.output_text  # type: ignore[attr-defined]
            return interaction

        llm._call_gemini = routed_call  # type: ignore[assignment]
        if not self.with_replies:
            assistant.generate_text = lambda prompt, system_instruction=None: "(reply stubbed in e2e run)"

    def save_recording(self) -> None:
        """Write the recorded understand answers (live mode only)."""
        if not self.replay and self.recording:
            RECORDING_PATH.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.recording, indent=2, ensure_ascii=False) + "\n"
            RECORDING_PATH.write_text(text, encoding="utf-8")


@dataclass
class Conversation:
    """One chat session's state, carried from turn to turn like chat_cli.py does."""

    harness: GeminiHarness
    profile: UserProfile = field(default_factory=UserProfile)
    last_question: str | None = None
    last_question_field: str | None = None
    matched_scheme_ids: list[str] = field(default_factory=list)

    def say(self, message: str) -> ChatTurnResult:
        """Send one message and carry the returned state forward."""
        self.harness.current_message = message
        result = assistant.handle_message(
            message,
            profile=self.profile,
            last_question=self.last_question,
            matched_scheme_ids=self.matched_scheme_ids,
            last_question_field=self.last_question_field,
        )
        self.profile = result.profile
        self.last_question = result.next_question
        self.last_question_field = result.next_question_field
        self.matched_scheme_ids = result.matched_scheme_ids
        return result


@dataclass
class Checker:
    """Collects pass/fail results and prints them as they happen."""

    failures: list[str] = field(default_factory=list)
    passed: int = 0

    def check(self, condition: bool, description: str) -> bool:
        """Record and print one check."""
        print(f"  [{'PASS' if condition else 'FAIL'}] {description}")
        if condition:
            self.passed += 1
        else:
            self.failures.append(description)
        return condition


def _answer_saved_as_no(profile: UserProfile, question_field: str) -> bool:
    """Whether a "No" to this question field is now stored in the profile."""
    if question_field.startswith("exclusion:"):
        category = ExclusionCategory(question_field.removeprefix("exclusion:"))
        return profile.exclusion_answer(category) is False
    if question_field == "monthly_pension":
        return profile.monthly_pension == 0
    return getattr(profile, question_field, None) is False


def _eligible_ids(result: ChatTurnResult) -> set[str]:
    """Ids of the schemes marked eligible this turn."""
    return {r.scheme_id for r in result.eligibility.eligible}


def _check_land_opening(
    checker: Checker, result: ChatTurnResult, age: int, hectares: float, style: str
) -> None:
    """Checks shared by every opening message that states age and land."""
    profile = result.profile
    checker.check(profile.age == age, f"age saved as {age} (got {profile.age})")
    checker.check(profile.owns_cultivable_land is True, f"land ownership saved (got {profile.owns_cultivable_land})")
    got = profile.landholding_hectares
    checker.check(got is not None and abs(got - hectares) < 0.01, f"land saved as ~{hectares:.3f} ha (got {got})")
    checker.check(result.language_style.value == style, f"language style {style} (got {result.language_style.value})")
    checker.check("pm-kisan" in result.matched_scheme_ids, f"PM-KISAN matched (got {result.matched_scheme_ids})")


def run_english_conversation(harness: GeminiHarness, checker: Checker) -> None:
    """The main scenario: opening message, then "No" until both schemes are eligible."""
    chat = Conversation(harness)
    print(f"\nTurn 1: {OPENING_EN!r}")
    result = chat.say(OPENING_EN)
    _check_land_opening(checker, result, age=35, hectares=1.0, style="english")
    checker.check({"pm-kisan", "pm-kmy"} <= set(result.matched_scheme_ids), "both schemes matched")

    for turn in range(2, 2 + MAX_NO_TURNS):
        if _eligible_ids(result) >= {"pm-kisan", "pm-kmy"}:
            break
        question, question_field = chat.last_question, chat.last_question_field
        if not checker.check(question_field is not None, "a follow-up question is asked"):
            return
        print(f"\nTurn {turn}: 'No'  (to {question_field})")
        result = chat.say("No")
        checker.check(result.understood_by == "quick_answer", "bare 'No' answered in Python (no Gemini call)")
        checker.check(_answer_saved_as_no(result.profile, question_field), f"'No' saved for {question_field}")
        checker.check(result.next_question != question, "next question changed")

    checker.check(
        _eligible_ids(result) >= {"pm-kisan", "pm-kmy"},
        f"both schemes eligible at the end (eligible: {sorted(_eligible_ids(result))})",
    )
    checker.check(not result.eligibility.not_eligible, "no scheme marked not eligible")


def run_opening_message(
    harness: GeminiHarness, checker: Checker, message: str, age: int, hectares: float, style: str
) -> None:
    """A fresh conversation with one opening message in another language."""
    print(f"\nNew conversation ({style}): {message!r}")
    result = Conversation(harness).say(message)
    _check_land_opening(checker, result, age=age, hectares=hectares, style=style)


SCENARIOS: tuple[Callable[[GeminiHarness, Checker], None], ...] = (
    run_english_conversation,
    lambda h, c: run_opening_message(h, c, OPENING_HI, age=40, hectares=ACRES_2_IN_HECTARES, style="hindi"),
    lambda h, c: run_opening_message(h, c, OPENING_HINGLISH, age=30, hectares=1.0, style="hinglish"),
)


def run(replay: bool, with_replies: bool = False) -> Checker:
    """Run every scenario and return the collected results."""
    harness = GeminiHarness(replay=replay, with_replies=with_replies)
    harness.install()
    checker = Checker()
    try:
        for scenario in SCENARIOS:
            scenario(harness, checker)
    finally:
        harness.save_recording()
    if harness.ledger is None:
        print("\nMode: replay (no real calls)")
    else:
        used = f"{harness.ledger.count}/{DAILY_CALL_LIMIT} used today"
        print(f"\nMode: live ({harness.real_calls} real Gemini calls this run; {used})")
    return checker


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the conversation, and report."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replay", action="store_true", help="use recorded Gemini answers, no network")
    parser.add_argument("--with-replies", action="store_true", help="also make the real reply-writing calls")
    args = parser.parse_args(argv)
    if args.replay and args.with_replies:
        parser.error("--with-replies needs live mode")
    for stream in (sys.stdout, sys.stdin):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")  # Hindi output on the Windows console

    checker = run(replay=args.replay, with_replies=args.with_replies)
    print(f"Result: {checker.passed} passed, {len(checker.failures)} failed")
    for failure in checker.failures:
        print(f"  FAILED: {failure}")
    return 1 if checker.failures else 0


if __name__ == "__main__":
    sys.exit(main())
