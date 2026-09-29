"""Replays the recorded end-to-end conversation (scripts/e2e_conversation.py --replay).

The recording holds Gemini's real raw answers from the last live run, so this checks the
whole pipeline (quick answers, parsing, land-size reading, merging, search, eligibility)
against real model output, offline and for free. Search uses a temporary index built from
the real data/schemes.json, so it does not depend on scripts/ingest.py having been run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from backend.models.scheme import Scheme
from backend.services import assistant, llm, retriever
from backend.services.ingest import rebuild_collection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "e2e_conversation.py"


def _load_script() -> ModuleType:
    """Import scripts/e2e_conversation.py (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("e2e_conversation", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered first
    spec.loader.exec_module(module)
    return module


def test_recorded_conversation_passes_every_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every profile check in the scripted conversation passes on the recorded answers."""
    e2e = _load_script()
    # The harness patches these module globals; register them so they are restored after.
    monkeypatch.setattr(llm, "_call_gemini", llm._call_gemini)
    monkeypatch.setattr(assistant, "generate_text", assistant.generate_text)

    entries = json.loads((PROJECT_ROOT / "data" / "schemes.json").read_text(encoding="utf-8-sig"))
    collection = rebuild_collection(
        [Scheme.model_validate(e) for e in entries], persist_directory=tmp_path, collection_name="e2e-replay"
    )
    monkeypatch.setattr(retriever, "_collection", collection)

    checker = e2e.run(replay=True)

    assert checker.failures == [], capsys.readouterr().out
    assert checker.passed >= 40
