"""CLI: a scripted end-to-end conversation that checks the saved profile after every turn.

Usage (from the project root):
    venv\\Scripts\\python scripts/e2e_conversation.py              # live: real Gemini calls
    venv\\Scripts\\python scripts/e2e_conversation.py --replay     # offline: recorded answers
    venv\\Scripts\\python scripts/e2e_conversation.py --with-replies   # live, reply calls too
    venv\\Scripts\\python scripts/e2e_conversation.py --replay --via-api   # through POST /api/chat

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
from typing import Any

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
    api_client: Any = None  # a fastapi TestClient when running via the API

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


@dataclass(frozen=True)
class TurnView:
    """What the checks look at after one turn, the same whether it ran in-process or
    through the HTTP API."""

    profile: UserProfile
    next_question: str | None
    next_question_field: str | None
    matched_scheme_ids: list[str]
    eligible_ids: frozenset[str]
    not_eligible_ids: frozenset[str]
    language_style: str
    understood_by: str

    @classmethod
    def from_result(cls, result: ChatTurnResult) -> TurnView:
        """From an in-process handle_message result."""
        report = result.eligibility
        return cls(
            profile=result.profile,
            next_question=result.next_question,
            next_question_field=result.next_question_field,
            matched_scheme_ids=result.matched_scheme_ids,
            eligible_ids=frozenset(r.scheme_id for r in report.eligible),
            not_eligible_ids=frozenset(r.scheme_id for r in report.not_eligible),
            language_style=result.language_style.value,
            understood_by=result.understood_by,
        )

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> TurnView:
        """From a POST /api/chat JSON response."""
        state = body["state"]
        return cls(
            profile=UserProfile.model_validate(state["profile"]),
            next_question=state["last_question"],
            next_question_field=state["last_question_field"],
            matched_scheme_ids=state["matched_scheme_ids"],
            eligible_ids=frozenset(c["scheme_id"] for c in body["schemes"] if c["status"] == "eligible"),
            not_eligible_ids=frozenset(c["scheme_id"] for c in body["schemes"] if c["status"] == "not_eligible"),
            language_style=body["language_style"],
            understood_by=body["understood_by"],
        )


@dataclass
class Conversation:
    """One chat session's state, carried from turn to turn: in-process like chat_cli.py
    does, or (via the API) as the JSON state a browser sends back to POST /api/chat."""

    harness: GeminiHarness
    profile: UserProfile = field(default_factory=UserProfile)
    last_question: str | None = None
    last_question_field: str | None = None
    matched_scheme_ids: list[str] = field(default_factory=list)
    api_state: dict[str, Any] | None = None

    def say(self, message: str) -> TurnView:
        """Send one message and carry the returned state forward."""
        self.harness.current_message = message
        if self.harness.api_client is not None:
            return self._say_via_api(message)
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
        return TurnView.from_result(result)

    def _say_via_api(self, message: str) -> TurnView:
        """POST the message with the previous JSON state, exactly as the web UI does."""
        payload: dict[str, Any] = {"message": message}
        if self.api_state is not None:
            payload["state"] = self.api_state
        response = self.harness.api_client.post("/api/chat", json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"POST /api/chat returned {response.status_code}: {response.text}")
        body = response.json()
        self.api_state = body["state"]
        view = TurnView.from_api(body)
        self.last_question, self.last_question_field = view.next_question, view.next_question_field
        return view


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


def _check_land_opening(
    checker: Checker, result: TurnView, age: int, hectares: float, style: str
) -> None:
    """Checks shared by every opening message that states age and land."""
    profile = result.profile
    checker.check(profile.age == age, f"age saved as {age} (got {profile.age})")
    checker.check(profile.owns_cultivable_land is True, f"land ownership saved (got {profile.owns_cultivable_land})")
    got = profile.landholding_hectares
    checker.check(got is not None and abs(got - hectares) < 0.01, f"land saved as ~{hectares:.3f} ha (got {got})")
    checker.check(result.language_style == style, f"language style {style} (got {result.language_style})")
    checker.check("pm-kisan" in result.matched_scheme_ids, f"PM-KISAN matched (got {result.matched_scheme_ids})")


def run_english_conversation(harness: GeminiHarness, checker: Checker) -> None:
    """The main scenario: opening message, then "No" until both schemes are eligible."""
    chat = Conversation(harness)
    print(f"\nTurn 1: {OPENING_EN!r}")
    result = chat.say(OPENING_EN)
    _check_land_opening(checker, result, age=35, hectares=1.0, style="english")
    checker.check({"pm-kisan", "pm-kmy"} <= set(result.matched_scheme_ids), "both schemes matched")

    for turn in range(2, 2 + MAX_NO_TURNS):
        if result.eligible_ids >= {"pm-kisan", "pm-kmy"}:
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
        result.eligible_ids >= {"pm-kisan", "pm-kmy"},
        f"both schemes eligible at the end (eligible: {sorted(result.eligible_ids)})",
    )
    checker.check(not result.not_eligible_ids, "no scheme marked not eligible")


def run_opening_message(
    harness: GeminiHarness, checker: Checker, message: str, age: int, hectares: float, style: str
) -> None:
    """A fresh conversation with one opening message in another language."""
    print(f"\nNew conversation ({style}): {message!r}")
    result = Conversation(harness).say(message)
    _check_land_opening(checker, result, age=age, hectares=hectares, style=style)


def _api_client() -> Any:
    """An in-process client for the real FastAPI app, with the rate limit switched off
    (this script sends many messages quickly on purpose)."""
    from fastapi.testclient import TestClient

    from backend.app.main import app
    from backend.app.routes import enforce_chat_rate_limit

    app.dependency_overrides[enforce_chat_rate_limit] = lambda: None
    return TestClient(app)


def _clear_api_overrides() -> None:
    """Undo the rate-limit override made by _api_client."""
    from backend.app.main import app

    app.dependency_overrides.clear()


SCENARIOS: tuple[Callable[[GeminiHarness, Checker], None], ...] = (
    run_english_conversation,
    lambda h, c: run_opening_message(h, c, OPENING_HI, age=40, hectares=ACRES_2_IN_HECTARES, style="hindi"),
    lambda h, c: run_opening_message(h, c, OPENING_HINGLISH, age=30, hectares=1.0, style="hinglish"),
)


def run(replay: bool, with_replies: bool = False, via_api: bool = False) -> Checker:
    """Run every scenario and return the collected results. ``via_api`` sends every
    message through POST /api/chat (in-process, rate limit off) instead of calling
    handle_message directly."""
    harness = GeminiHarness(replay=replay, with_replies=with_replies)
    harness.install()
    if via_api:
        harness.api_client = _api_client()
    checker = Checker()
    try:
        for scenario in SCENARIOS:
            scenario(harness, checker)
    finally:
        harness.save_recording()
        if via_api:
            _clear_api_overrides()
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
    parser.add_argument("--via-api", action="store_true", help="send every message through POST /api/chat")
    args = parser.parse_args(argv)
    if args.replay and args.with_replies:
        parser.error("--with-replies needs live mode")
    for stream in (sys.stdout, sys.stdin):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")  # Hindi output on the Windows console

    checker = run(replay=args.replay, with_replies=args.with_replies, via_api=args.via_api)
    print(f"Result: {checker.passed} passed, {len(checker.failures)} failed")
    for failure in checker.failures:
        print(f"  FAILED: {failure}")
    return 1 if checker.failures else 0


if __name__ == "__main__":
    sys.exit(main())
