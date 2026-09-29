"""CLI: offline evaluation of the parts of Yojana Saathi that decide things, with a score report.

Usage (from the project root):
    venv\\Scripts\\python scripts/evaluate.py

Runs every case in data/eval/cases.json, with no Gemini calls:
  search       does the right scheme rank first (or nothing pass the relevance cut-off
               for an unrelated question)? Uses the real multilingual embedding model.
  eligibility  does the rule engine give each scheme the expected status, and ask the
               expected first question?
  yes_no       is a bare yes/no (English, Hinglish, Devanagari) read correctly, and is
               anything longer left to Gemini?
  land         is a stated land size read (and converted) correctly, and are local units
               and ambiguous messages left alone?

Exit code is 0 if every area meets its threshold in THRESHOLDS, 1 otherwise.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.scheme import ExclusionCategory  # noqa: E402
from backend.models.user_profile import UserProfile  # noqa: E402
from backend.services import retriever  # noqa: E402
from backend.services.assistant import SEARCH_SCORE_THRESHOLD  # noqa: E402
from backend.services.eligibility import check_eligibility  # noqa: E402
from backend.services.land import extract_land_hectares  # noqa: E402
from backend.services.quick_answer import parse_yes_no  # noqa: E402
from backend.services.scheme_store import load_all_schemes  # noqa: E402

CASES_PATH = PROJECT_ROOT / "data" / "eval" / "cases.json"
THRESHOLDS: dict[str, float] = {"search": 0.9, "eligibility": 1.0, "yes_no": 1.0, "land": 1.0}
_ASKABLE_EXCLUSIONS = [c.value for c in ExclusionCategory if c is not ExclusionCategory.INSTITUTIONAL_LAND_HOLDER]


@dataclass
class AreaResult:
    """Pass/fail counts and failure descriptions for one evaluated area."""

    name: str
    passed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Number of cases run."""
        return self.passed + len(self.failures)

    @property
    def score(self) -> float:
        """Fraction of cases passed (1.0 for an empty area)."""
        return self.passed / self.total if self.total else 1.0

    def record(self, ok: bool, description: str) -> None:
        """Count one case."""
        if ok:
            self.passed += 1
        else:
            self.failures.append(description)


def _profile_from_case(raw: dict[str, Any]) -> UserProfile:
    """Build a UserProfile; "ALL_NO" in exclusions means every askable exclusion is "no"."""
    data = dict(raw)
    exclusions = data.get("exclusions")
    if exclusions == "ALL_NO" or (isinstance(exclusions, dict) and exclusions.get("ALL_NO")):
        overrides = {k: v for k, v in exclusions.items() if k != "ALL_NO"} if isinstance(exclusions, dict) else {}
        data["exclusions"] = {**dict.fromkeys(_ASKABLE_EXCLUSIONS, False), **overrides}
    return UserProfile.model_validate(data)


def evaluate_search(cases: list[dict[str, Any]]) -> AreaResult:
    """Top-1 accuracy, counting "nothing above the cut-off" as right for unrelated queries."""
    area = AreaResult("search")
    for case in cases:
        matches = [m for m in retriever.search_schemes(case["query"], top_k=2) if m.score >= SEARCH_SCORE_THRESHOLD]
        got = matches[0].scheme_id if matches else None
        area.record(got == case["expected"], f"{case['query']!r}: expected {case['expected']}, got {got}")
    return area


def evaluate_eligibility(cases: list[dict[str, Any]]) -> AreaResult:
    """Every scheme's status must match, and the first question if the case names one."""
    area = AreaResult("eligibility")
    schemes = list(load_all_schemes())
    for case in cases:
        report = check_eligibility(_profile_from_case(case["profile"]), schemes)
        got = {r.scheme_id: r.status.value for r in report.eligible + report.possibly_eligible + report.not_eligible}
        area.record(got == case["expected"], f"{case['name']}: expected {case['expected']}, got {got}")
        if "first_question_field" in case:
            first = report.questions[0].field if report.questions else None
            area.record(
                first == case["first_question_field"],
                f"{case['name']}: first question expected {case['first_question_field']}, got {first}",
            )
    return area


def evaluate_yes_no(cases: list[dict[str, Any]]) -> AreaResult:
    """The bare yes/no reader: True/False, or None for anything that is not a bare yes/no."""
    area = AreaResult("yes_no")
    for case in cases:
        parsed = parse_yes_no(case["message"])
        got = parsed[0] if parsed else None
        area.record(got == case["expected"], f"{case['message']!r}: expected {case['expected']}, got {got}")
    return area


def evaluate_land(cases: list[dict[str, Any]]) -> AreaResult:
    """The land-size reader, to within 0.001 hectares."""
    area = AreaResult("land")
    for case in cases:
        got, expected = extract_land_hectares(case["message"]), case["expected_hectares"]
        ok = got is None if expected is None else got is not None and abs(got - expected) < 1e-3
        area.record(ok, f"{case['message']!r}: expected {expected}, got {got}")
    return area


EVALUATORS: dict[str, Callable[[list[dict[str, Any]]], AreaResult]] = {
    "search": evaluate_search,
    "eligibility": evaluate_eligibility,
    "yes_no": evaluate_yes_no,
    "land": evaluate_land,
}


def run(cases_path: Path = CASES_PATH) -> list[AreaResult]:
    """Evaluate every area in the cases file."""
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    retriever.ensure_ready()  # builds the search index if it does not exist yet (e.g. in CI)
    return [evaluator(cases.get(name, [])) for name, evaluator in EVALUATORS.items()]


def failing_areas(results: list[AreaResult]) -> list[str]:
    """Names of areas below their threshold."""
    return [r.name for r in results if r.score < THRESHOLDS[r.name]]


def print_report(results: list[AreaResult]) -> None:
    """A small score table, then every failed case."""
    print(f"\n{'area':<12} {'passed':>7} {'total':>6} {'score':>7} {'needed':>7}")
    for r in results:
        mark = "ok" if r.score >= THRESHOLDS[r.name] else "BELOW"
        print(f"{r.name:<12} {r.passed:>7} {r.total:>6} {r.score:>7.0%} {THRESHOLDS[r.name]:>7.0%}  {mark}")
    for r in results:
        for failure in r.failures:
            print(f"  FAILED [{r.name}] {failure}")


def main() -> int:
    """Run the evaluation and print the report."""
    for stream in (sys.stdout,):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")  # Hindi on the Windows console
    results = run()
    print_report(results)
    below = failing_areas(results)
    print(f"\nResult: {'all areas meet their thresholds' if not below else 'below threshold: ' + ', '.join(below)}")
    return 1 if below else 0


if __name__ == "__main__":
    sys.exit(main())
