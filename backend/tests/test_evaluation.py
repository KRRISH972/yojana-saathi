"""Runs the offline evaluation (scripts/evaluate.py) as part of the test suite, and checks
that the evaluation itself can actually catch a wrong answer."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    """Import scripts/evaluate.py (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("evaluate", PROJECT_ROOT / "scripts" / "evaluate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered first
    spec.loader.exec_module(module)
    return module


def test_every_area_meets_its_threshold() -> None:
    """The real evaluation set passes: search, eligibility, yes/no and land reading."""
    evaluate = _load_script()
    results = evaluate.run()
    assert evaluate.failing_areas(results) == [], [f for r in results for f in r.failures]
    assert {r.name for r in results} == {"search", "eligibility", "yes_no", "land"}
    assert all(r.total > 0 for r in results)


def test_a_wrong_expectation_is_reported(tmp_path: Path) -> None:
    """Guard against an evaluation that can never fail: flip one label and it must show up."""
    evaluate = _load_script()
    cases = json.loads(evaluate.CASES_PATH.read_text(encoding="utf-8"))
    cases["eligibility"][0]["expected"]["pm-kmy"] = "not_eligible"  # truly eligible
    cases["yes_no"][0]["expected"] = False  # "yes"
    wrong = tmp_path / "cases.json"
    wrong.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    results = {r.name: r for r in evaluate.run(wrong)}

    assert len(results["eligibility"].failures) == 1
    assert len(results["yes_no"].failures) == 1
    assert set(evaluate.failing_areas(list(results.values()))) == {"eligibility", "yes_no"}
