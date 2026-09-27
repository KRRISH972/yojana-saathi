"""Tests for the rule-based eligibility engine, using the real PM-KISAN and PM-KMY entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.scheme import ExclusionCategory, Occupation, Scheme
from backend.models.user_profile import UserProfile
from backend.services.eligibility import EligibilityStatus, check_eligibility

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMES_JSON = PROJECT_ROOT / "data" / "schemes.json"

# Every generic (non-high-pensioner, non-institutional) exclusion category used by our two
# schemes. Setting all of them to False means "we asked, and none of these apply".
_ALL_CLEAR_EXCLUSIONS: dict[ExclusionCategory, bool] = {
    ExclusionCategory.CONSTITUTIONAL_POST_HOLDER: False,
    ExclusionCategory.ELECTED_REPRESENTATIVE: False,
    ExclusionCategory.GOVERNMENT_EMPLOYEE: False,
    ExclusionCategory.INCOME_TAX_PAYER: False,
    ExclusionCategory.REGISTERED_PROFESSIONAL: False,
    ExclusionCategory.OTHER_SOCIAL_SECURITY_SCHEME: False,
}


@pytest.fixture(scope="module")
def schemes() -> list[Scheme]:
    """The real PM-KISAN and PM-KMY entries shipped in data/schemes.json."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    return [Scheme.model_validate(e) for e in entries if e["id"] in ("pm-kisan", "pm-kmy")]


def _status_by_id(schemes_result, scheme_id: str) -> EligibilityStatus | None:
    """Find a scheme's status across all three groups of a report, by id."""
    for group in (schemes_result.eligible, schemes_result.possibly_eligible, schemes_result.not_eligible):
        for result in group:
            if result.scheme_id == scheme_id:
                return result.status
    return None


def test_healthy_young_farmer_is_eligible_for_both(schemes: list[Scheme]) -> None:
    """A 30-year-old farmer with 1.5 hectares and a clean record clears every rule."""
    profile = UserProfile(
        age=30, occupation=Occupation.FARMER, owns_cultivable_land=True, landholding_hectares=1.5,
        monthly_pension=0, exclusions=_ALL_CLEAR_EXCLUSIONS,
    )  # fmt: skip
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.ELIGIBLE
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.ELIGIBLE


def test_45_year_old_farmer_fails_pm_kmy_on_age(schemes: list[Scheme]) -> None:
    """PM-KISAN has no age limit; PM-KMY's 18-40 joining age range rules this farmer out."""
    profile = UserProfile(
        age=45, occupation=Occupation.FARMER, owns_cultivable_land=True, landholding_hectares=1.5,
        monthly_pension=0, exclusions=_ALL_CLEAR_EXCLUSIONS,
    )  # fmt: skip
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.ELIGIBLE
    kmy_result = next(r for r in report.not_eligible if r.scheme_id == "pm-kmy")
    assert any("ages 18 to 40" in reason and "45" in reason for reason in kmy_result.reasons)


def test_three_hectare_farmer_fails_pm_kmy_on_land_size(schemes: list[Scheme]) -> None:
    """PM-KISAN has no landholding cap; PM-KMY's 2-hectare limit rules this farmer out."""
    profile = UserProfile(
        age=30, owns_cultivable_land=True, landholding_hectares=3.0,
        monthly_pension=0, exclusions=_ALL_CLEAR_EXCLUSIONS,
    )  # fmt: skip
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.ELIGIBLE
    kmy_result = next(r for r in report.not_eligible if r.scheme_id == "pm-kmy")
    assert any("2 hectares" in reason and "3 hectares" in reason for reason in kmy_result.reasons)


def test_income_tax_payer_is_not_eligible_for_either(schemes: list[Scheme]) -> None:
    """Both schemes exclude income tax payers, regardless of everything else being fine."""
    exclusions = dict(_ALL_CLEAR_EXCLUSIONS)
    exclusions[ExclusionCategory.INCOME_TAX_PAYER] = True
    profile = UserProfile(
        age=30, owns_cultivable_land=True, landholding_hectares=1.0, monthly_pension=0, exclusions=exclusions
    )
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.NOT_ELIGIBLE
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.NOT_ELIGIBLE


def test_high_pension_excludes_from_pm_kisan_only(schemes: list[Scheme]) -> None:
    """A monthly pension at/above PM-KISAN's Rs 10,000 threshold fails only that scheme,
    because PM-KMY has no pension-threshold rule at all."""
    profile = UserProfile(monthly_pension=12000)
    report = check_eligibility(profile, schemes)
    kisan_result = next(r for r in report.not_eligible if r.scheme_id == "pm-kisan")
    assert any("Rs 10,000" in reason and "12,000" in reason for reason in kisan_result.reasons)
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.POSSIBLY_ELIGIBLE


def test_group_d_employee_is_not_excluded_by_government_employee_rule(schemes: list[Scheme]) -> None:
    """Group D / Class IV / MTS staff are explicitly not covered by the exclusion, so
    answering 'no' to the (clarified) government-employee question keeps them eligible."""
    exclusions = dict(_ALL_CLEAR_EXCLUSIONS)  # government_employee is already False here
    profile = UserProfile(
        age=35, owns_cultivable_land=True, landholding_hectares=1.0, monthly_pension=0, exclusions=exclusions
    )
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.ELIGIBLE
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.ELIGIBLE


def test_private_employee_who_owns_land_is_eligible_for_pm_kisan(schemes: list[Scheme]) -> None:
    """PM-KISAN does not restrict by occupation, so a private-sector employee who owns
    land and has no exclusions qualifies."""
    profile = UserProfile(
        occupation=Occupation.SALARIED_PRIVATE, owns_cultivable_land=True, monthly_pension=0,
        exclusions=_ALL_CLEAR_EXCLUSIONS,
    )  # fmt: skip
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.ELIGIBLE


def test_landless_person_is_not_eligible_for_either(schemes: list[Scheme]) -> None:
    """Both schemes require owning cultivable land in one's own name."""
    profile = UserProfile(owns_cultivable_land=False)
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.NOT_ELIGIBLE
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.NOT_ELIGIBLE


def test_empty_profile_is_possibly_eligible_for_both_with_questions(schemes: list[Scheme]) -> None:
    """With nothing known yet, neither scheme can be ruled in or out, and the engine
    returns a non-empty, land-ownership-first question queue."""
    profile = UserProfile()
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.POSSIBLY_ELIGIBLE
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.POSSIBLY_ELIGIBLE
    assert len(report.questions) > 0
    # Land ownership is required by both schemes, so it decides the most schemes at once
    # and should be asked before a question that only affects one scheme (e.g. age).
    assert report.questions[0].field == "owns_cultivable_land"
    assert set(report.questions[0].affects_schemes) == {"pm-kisan", "pm-kmy"}


def test_epfo_coverage_excludes_from_pm_kmy_only(schemes: list[Scheme]) -> None:
    """other_social_security_scheme is only in PM-KMY's excluded_if, not PM-KISAN's."""
    exclusions = dict(_ALL_CLEAR_EXCLUSIONS)
    exclusions[ExclusionCategory.OTHER_SOCIAL_SECURITY_SCHEME] = True
    profile = UserProfile(
        age=30, owns_cultivable_land=True, landholding_hectares=1.0, monthly_pension=0, exclusions=exclusions
    )
    report = check_eligibility(profile, schemes)
    assert _status_by_id(report, "pm-kmy") is EligibilityStatus.NOT_ELIGIBLE
    assert _status_by_id(report, "pm-kisan") is EligibilityStatus.ELIGIBLE
