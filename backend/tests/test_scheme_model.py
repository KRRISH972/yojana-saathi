"""Tests for the Scheme model's exclusion and cultivable-land fields and the validate_data script."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.models.scheme import Eligibility, ExclusionCategory, ExclusionScope, Scheme

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMES_JSON = PROJECT_ROOT / "data" / "schemes.json"
VALIDATE_SCRIPT = PROJECT_ROOT / "scripts" / "validate_data.py"


@pytest.fixture
def scheme_dict() -> dict[str, Any]:
    """A minimal valid scheme entry made of obvious dummy values."""
    return {
        "id": "test-scheme",
        "name_en": "Test Scheme",
        "name_hi": "परीक्षण योजना",
        "ministry": "Test Ministry",
        "level": "central",
        "state": None,
        "category": "other",
        "description_en": "Test description.",
        "description_hi": "परीक्षण विवरण।",
        "benefits": "Test benefits.",
        "eligibility": {},
        "documents_required": ["Test document"],
        "how_to_apply": "Test steps.",
        "official_url": "https://example.com",
        "last_verified_date": "2020-01-01",
    }


def test_new_fields_have_safe_defaults() -> None:
    """With no input, no land is required and nobody is excluded."""
    eligibility = Eligibility()
    assert eligibility.requires_own_cultivable_land is False
    assert eligibility.max_landholding_hectares is None
    assert eligibility.excluded_if == []
    assert eligibility.excluded_pension_monthly_min is None
    assert eligibility.excluded_scope is ExclusionScope.PERSON


def test_valid_exclusions_are_accepted(scheme_dict: dict[str, Any]) -> None:
    """A full set of new fields validates and is parsed into enums."""
    scheme_dict["eligibility"] = {
        "requires_own_cultivable_land": True,
        "excluded_if": ["income_tax_payer", "high_pensioner"],
        "excluded_pension_monthly_min": 5000,
        "excluded_scope": "family",
    }
    scheme = Scheme.model_validate(scheme_dict)
    assert scheme.eligibility.requires_own_cultivable_land is True
    assert scheme.eligibility.excluded_if == [ExclusionCategory.INCOME_TAX_PAYER, ExclusionCategory.HIGH_PENSIONER]
    assert scheme.eligibility.excluded_scope is ExclusionScope.FAMILY


@pytest.mark.parametrize("hectares", [2.0, 0.5])
def test_max_landholding_hectares_accepts_positive_numbers(hectares: float) -> None:
    """A positive hectare limit is accepted and kept as given."""
    assert Eligibility(max_landholding_hectares=hectares).max_landholding_hectares == hectares


@pytest.mark.parametrize("hectares", [0, -1])
def test_max_landholding_hectares_rejects_non_positive_numbers(hectares: float) -> None:
    """Zero or negative hectare limits make no sense and are rejected."""
    with pytest.raises(ValidationError):
        Eligibility(max_landholding_hectares=hectares)


def test_high_pensioner_requires_threshold() -> None:
    """'high_pensioner' without a pension threshold is rejected."""
    with pytest.raises(ValidationError, match="excluded_pension_monthly_min is required"):
        Eligibility(excluded_if=[ExclusionCategory.HIGH_PENSIONER])


def test_threshold_without_high_pensioner_is_rejected() -> None:
    """A pension threshold with no 'high_pensioner' exclusion is a data-entry mistake."""
    with pytest.raises(ValidationError, match="does not contain 'high_pensioner'"):
        Eligibility(excluded_pension_monthly_min=10000)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("excluded_if", ["retired_pensioner"]),
        ("excluded_scope", "household"),
        ("excluded_pension_monthly_min", -1),
        ("unknown_field", 1),
    ],
)
def test_invalid_new_field_values_are_rejected(field: str, value: Any) -> None:
    """Unknown categories/scopes, negative thresholds and typo'd field names all fail."""
    with pytest.raises(ValidationError):
        Eligibility.model_validate({field: value})


def test_real_pm_kisan_entry_uses_new_fields() -> None:
    """The shipped PM-KISAN entry has the agreed structured exclusions and no occupation limit."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    scheme = Scheme.model_validate(next(e for e in entries if e["id"] == "pm-kisan"))
    eligibility = scheme.eligibility
    assert eligibility.occupations == []
    assert eligibility.max_annual_income is None
    assert eligibility.requires_own_cultivable_land is True
    assert eligibility.excluded_scope is ExclusionScope.FAMILY
    assert eligibility.excluded_pension_monthly_min == 10000
    assert eligibility.max_landholding_hectares is None
    assert set(eligibility.excluded_if) == set(ExclusionCategory) - {ExclusionCategory.OTHER_SOCIAL_SECURITY_SCHEME}


def test_real_pm_kmy_entry_matches_sources() -> None:
    """The shipped PM-KMY entry has the age range and only exact-match exclusions."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    scheme = Scheme.model_validate(next(e for e in entries if e["id"] == "pm-kmy"))
    eligibility = scheme.eligibility
    assert (eligibility.min_age, eligibility.max_age) == (18, 40)
    assert eligibility.occupations == []
    assert eligibility.max_annual_income is None
    assert eligibility.requires_own_cultivable_land is True
    assert eligibility.max_landholding_hectares == 2.0
    assert ExclusionCategory.HIGH_PENSIONER not in eligibility.excluded_if  # PM-KMY has no pension rule
    assert ExclusionCategory.OTHER_SOCIAL_SECURITY_SCHEME in eligibility.excluded_if
    assert eligibility.excluded_pension_monthly_min is None
    assert eligibility.other_conditions.startswith("NOTE: Based on official documents dated August 2019")


def test_no_placeholder_entries_remain() -> None:
    """Once real scheme data exists, placeholder example entries must not ship in schemes.json."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    assert all("placeholder" not in json.dumps(e).lower() for e in entries)


def _run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    """Run scripts/validate_data.py on a file and capture its output."""
    return subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT), str(path)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip


def test_validator_script_reports_bad_exclusion_fields(scheme_dict: dict[str, Any], tmp_path: Path) -> None:
    """The script exits 1 and names the offending field for bad exclusion data."""
    bad = copy.deepcopy(scheme_dict)
    bad["eligibility"] = {"excluded_if": ["high_pensioner", "nonsense"]}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([bad]), encoding="utf-8")
    result = _run_validator(path)
    assert result.returncode == 1
    assert "excluded_if" in result.stdout


def test_validator_script_accepts_good_exclusion_fields(scheme_dict: dict[str, Any], tmp_path: Path) -> None:
    """The script exits 0 for a valid entry that uses the new fields."""
    scheme_dict["eligibility"] = {"excluded_if": ["income_tax_payer"], "excluded_scope": "family"}
    path = tmp_path / "good.json"
    path.write_text(json.dumps([scheme_dict]), encoding="utf-8")
    result = _run_validator(path)
    assert result.returncode == 0, result.stdout


def test_validator_script_reports_bad_landholding_limit(scheme_dict: dict[str, Any], tmp_path: Path) -> None:
    """The script exits 1 and names the field for a zero/negative hectare limit."""
    bad = copy.deepcopy(scheme_dict)
    bad["eligibility"] = {"max_landholding_hectares": 0}
    path = tmp_path / "bad_land.json"
    path.write_text(json.dumps([bad]), encoding="utf-8")
    result = _run_validator(path)
    assert result.returncode == 1
    assert "max_landholding_hectares" in result.stdout
