"""CLI: chat with Yojana Saathi in the terminal, for manual testing.

Usage (from the project root):
    venv\\Scripts\\python scripts/chat_cli.py

Type 'exit' or 'quit' to stop. Type 'debug' to print the current profile and eligibility
report as JSON (handy while developing).

Requires scripts/ingest.py to have been run at least once, and GEMINI_API_KEY set in .env.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.user_profile import UserProfile  # noqa: E402
from backend.services.assistant import ChatTurnResult, handle_message  # noqa: E402
from backend.services.llm import GeminiError, GeminiRateLimitError  # noqa: E402


def _use_utf8_console() -> None:
    """Switch stdout and stdin to UTF-8.

    The Windows console defaults to a legacy code page (e.g. cp1252) that cannot encode
    Devanagari, so printing a Hindi reply would crash with UnicodeEncodeError and typed
    Hindi input would arrive garbled.
    """
    for stream in (sys.stdout, sys.stdin):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def _show_backend_warnings() -> None:
    """Print warnings from our own code (e.g. dropped profile fields) to the console, so a
    silently ignored answer is visible while testing. Library loggers are left alone."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[warning] %(name)s: %(message)s"))
    backend_logger = logging.getLogger("backend")
    backend_logger.addHandler(handler)
    backend_logger.setLevel(logging.WARNING)


def _print_debug(
    profile: UserProfile, last_question: str | None, matched_scheme_ids: list[str], last_result: ChatTurnResult | None
) -> None:
    """Print the live conversation state first, then the last successful turn's full result."""
    known = profile.model_dump(mode="json", exclude_defaults=True)
    print("=== Current profile (facts known so far) ===")
    print(json.dumps(known, indent=2, ensure_ascii=False) if known else "(nothing known yet)")
    print(f"Next question: {last_question or '(none)'}")
    print(f"Matched schemes: {', '.join(matched_scheme_ids) or '(none)'}")
    if last_result is not None:
        print("\n=== Last successful turn (full result) ===")
        print(last_result.model_dump_json(indent=2))
    print()


def main() -> int:
    """Run an interactive chat loop against the assistant."""
    _use_utf8_console()
    _show_backend_warnings()
    print("Yojana Saathi (terminal chat). Type 'exit' to quit, 'debug' to inspect state.\n")

    profile = UserProfile()
    last_question: str | None = None
    matched_scheme_ids: list[str] = []
    last_result: ChatTurnResult | None = None

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            break
        if message.lower() == "debug":
            _print_debug(profile, last_question, matched_scheme_ids, last_result)
            continue

        try:
            result = handle_message(
                message, profile=profile, last_question=last_question, matched_scheme_ids=matched_scheme_ids
            )
        except GeminiRateLimitError as exc:
            print(f"Saathi: {exc}\n")
            continue
        except GeminiError as exc:
            print(f"Saathi: Something went wrong talking to Gemini ({exc}). Please try again.\n")
            continue

        profile = result.profile
        last_question = result.next_question
        matched_scheme_ids = result.matched_scheme_ids
        last_result = result
        print(f"Saathi: {result.reply_text}\n")

    print("Goodbye!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
