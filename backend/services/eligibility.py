"""Rule-based eligibility engine: compares a UserProfile against Scheme records.

How it decides (plain English)
-------------------------------
For every scheme, each eligibility rule (age, gender, state, land, exclusions, ...) is
checked one at a time and comes back as one of three answers:

- PASS    the rule is satisfied, or the scheme does not restrict on this at all.
- FAIL    we have a definite value from the user that breaks the rule.
- UNKNOWN the scheme cares about this rule, but we don't have the user's answer yet.

A scheme's overall status follows directly from those per-rule answers:

- "not_eligible"      if ANY rule FAILs, no matter how many other rules are UNKNOWN.
                      One broken rule is enough; we don't need to finish asking everything.
- "possibly_eligible" if no rule FAILs, but at least one rule is UNKNOWN.
- "eligible"          only if every rule that applies to the scheme PASSes.

This means "eligible" is a promise we can back up completely from what the user has told
us, and "possibly_eligible" always comes with the exact list of questions that would turn
it into a final answer. A missing value is never guessed as "no".

For a citizen with several possibly-eligible schemes, we also build one combined queue of
questions across all of them, ordered so that the question which would decide the most
schemes at once is asked first (see ``_build_question_queue``). That way the chatbot asks
the fewest questions needed to give a useful answer.

Two schemes in our data need small special notes:

- PM-KMY's age limits (18 to 40) are the age of *joining* the scheme, but we only ever ask
  the user's current age — there is nothing extra to compute, we just compare it directly.
- 'high_pensioner' is not answered with a yes/no like the other exclusions. Instead the
  user gives us a rupee amount (``monthly_pension``), and we compare it to the scheme's own
  ``excluded_pension_monthly_min``. This is more accurate, because different schemes can set
  a different rupee threshold, and it lets us reuse one plain number for every scheme.
- 'institutional_land_holder' is never checked or asked about, because this assistant is
  built for individual citizens, never institutions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from backend.models.scheme import Eligibility, ExclusionCategory, ExclusionScope, Gender, Scheme
from backend.models.user_profile import UserProfile

Verdict = Literal["fail", "unknown"]

# A priority order used only to break ties when two or more missing fields would each
# decide the same number of possibly-eligible schemes. Land ownership comes first because
# it is the single biggest gate for the farmer-focused schemes we have today; the rest
# follow a natural interview order. A field not listed here (e.g. a future scheme adds a
# new kind of question) simply sorts after all of these.
_PRIORITY_FIELD_ORDER: tuple[str, ...] = (
    "owns_cultivable_land",
    "landholding_hectares",
    "age",
    "annual_income",
    "monthly_pension",
    "gender",
    "state",
    "occupation",
    "social_category",
    "exclusion:income_tax_payer",
    "exclusion:government_employee",
    "exclusion:other_social_security_scheme",
    "exclusion:registered_professional",
    "exclusion:elected_representative",
    "exclusion:constitutional_post_holder",
)

# One question + one "why you are excluded" clause per generic exclusion category, in both
# the "just you" and "you or your family" wording. Written out in full (rather than built
# from one shared template) so each sentence reads naturally and stays easy to translate.
_EXCLUSIONS: dict[ExclusionCategory, dict[ExclusionScope, dict[str, str]]] = {
    ExclusionCategory.CONSTITUTIONAL_POST_HOLDER: {
        ExclusionScope.PERSON: {
            "question": "Have you ever held a constitutional post (for example President, Governor, or judge)?",
            "reason": "you have held a constitutional post",
        },
        ExclusionScope.FAMILY: {
            "question": (
                "Have you, or anyone in your immediate family, ever held a constitutional post "
                "(for example President, Governor, or judge)?"
            ),
            "reason": "you or a family member has held a constitutional post",
        },
    },
    ExclusionCategory.ELECTED_REPRESENTATIVE: {
        ExclusionScope.PERSON: {
            "question": (
                "Are you, or have you ever been, an elected representative "
                "(MP, MLA, MLC, Minister, Mayor, or District Panchayat chairperson)?"
            ),
            "reason": "you are or were an elected representative",
        },
        ExclusionScope.FAMILY: {
            "question": (
                "Are you, or is anyone in your immediate family, an elected representative "
                "(MP, MLA, MLC, Minister, Mayor, or District Panchayat chairperson) now or in the past?"
            ),
            "reason": "you or a family member is or was an elected representative",
        },
    },
    ExclusionCategory.GOVERNMENT_EMPLOYEE: {
        ExclusionScope.PERSON: {
            "question": (
                "Are you a serving or retired government employee (Central/State ministries, a PSU, "
                "or a local body)? This does NOT include Group D, Class IV, or Multi Tasking Staff — "
                "they are not excluded."
            ),
            "reason": "you are a serving or retired government employee (not Group D / Class IV / Multi Tasking Staff)",
        },
        ExclusionScope.FAMILY: {
            "question": (
                "Are you, or is anyone in your immediate family, a serving or retired government employee "
                "(Central/State ministries, a PSU, or a local body)? This does NOT include Group D, Class IV, "
                "or Multi Tasking Staff — they are not excluded."
            ),
            "reason": (
                "you or a family member is a serving or retired government employee "
                "(not Group D / Class IV / Multi Tasking Staff)"
            ),
        },
    },
    ExclusionCategory.INCOME_TAX_PAYER: {
        ExclusionScope.PERSON: {
            "question": "Did you pay income tax in the last assessment year?",
            "reason": "you paid income tax in the last assessment year",
        },
        ExclusionScope.FAMILY: {
            "question": "Did you, or anyone in your immediate family, pay income tax in the last assessment year?",
            "reason": "you or a family member paid income tax in the last assessment year",
        },
    },
    ExclusionCategory.REGISTERED_PROFESSIONAL: {
        ExclusionScope.PERSON: {
            "question": (
                "Are you a registered, practising Doctor, Engineer, Lawyer, Chartered Accountant, or Architect?"
            ),
            "reason": "you are a registered, practising professional (Doctor, Engineer, Lawyer, CA, or Architect)",
        },
        ExclusionScope.FAMILY: {
            "question": (
                "Are you, or is anyone in your immediate family, a registered, practising Doctor, "
                "Engineer, Lawyer, Chartered Accountant, or Architect?"
            ),
            "reason": (
                "you or a family member is a registered, practising professional "
                "(Doctor, Engineer, Lawyer, CA, or Architect)"
            ),
        },
    },
    ExclusionCategory.OTHER_SOCIAL_SECURITY_SCHEME: {
        ExclusionScope.PERSON: {
            "question": (
                "Are you already covered by another government pension or social security scheme, "
                "such as NPS, EPFO, ESIC, PM-SYM, or PM-LVM?"
            ),
            "reason": "you are already covered by another government pension or social security scheme",
        },
        ExclusionScope.FAMILY: {
            "question": (
                "Are you, or is anyone in your immediate family, already covered by another government "
                "pension or social security scheme, such as NPS, EPFO, ESIC, PM-SYM, or PM-LVM?"
            ),
            "reason": "you or a family member is already covered by another government pension or social security scheme",
        },
    },
}


class EligibilityStatus(StrEnum):
    """The three possible outcomes for one scheme."""

    ELIGIBLE = "eligible"
    POSSIBLY_ELIGIBLE = "possibly_eligible"
    NOT_ELIGIBLE = "not_eligible"


class Question(BaseModel):
    """One simple question that would help decide one or more possibly-eligible schemes."""

    field: str = Field(description="UserProfile field name, or 'exclusion:<category>' for an exclusion answer.")
    question: str
    affects_schemes: list[str] = Field(description="ids of the possibly_eligible schemes this would help resolve.")


class SchemeResult(BaseModel):
    """The eligibility outcome for one scheme."""

    scheme_id: str
    scheme_name: str
    status: EligibilityStatus
    reasons: list[str]
    missing_fields: list[str] = Field(default_factory=list, description="Fields still unknown, this scheme only.")


class EligibilityReport(BaseModel):
    """The full result of checking one profile against a list of schemes."""

    eligible: list[SchemeResult] = Field(default_factory=list)
    possibly_eligible: list[SchemeResult] = Field(default_factory=list)
    not_eligible: list[SchemeResult] = Field(default_factory=list)
    questions: list[Question] = Field(
        default_factory=list,
        description="Missing-field questions across all possibly_eligible schemes, most useful first.",
    )


@dataclass(frozen=True)
class _RuleOutcome:
    """One rule's verdict for one scheme. Only produced for a FAIL or an UNKNOWN — a rule
    that passes, or does not apply to the scheme at all, produces nothing."""

    verdict: Verdict
    field: str
    reason: str | None = None
    question: str | None = None


def _humanize(value: str) -> str:
    """Turn an enum value like 'salaried_private' into 'salaried private' for messages."""
    return value.replace("_", " ")


def _age_range_text(eligibility: Eligibility) -> str:
    """A readable phrase for an age rule, e.g. 'ages 18 to 40' or 'age 60 and above'."""
    if eligibility.min_age is not None and eligibility.max_age is not None:
        return f"ages {eligibility.min_age} to {eligibility.max_age}"
    if eligibility.min_age is not None:
        return f"age {eligibility.min_age} and above"
    return f"age up to {eligibility.max_age}"


def _scheme_label(scheme: Scheme) -> str:
    """A short, readable name for a scheme in messages, e.g. 'PM-KISAN'."""
    return scheme.id.upper()


def _check_age(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """Compare the user's current age to the scheme's age range, if it has one."""
    if eligibility.min_age is None and eligibility.max_age is None:
        return None
    if profile.age is None:
        return _RuleOutcome("unknown", field="age", question="What is your age?")
    range_text = _age_range_text(eligibility)
    if eligibility.min_age is not None and profile.age < eligibility.min_age:
        return _RuleOutcome("fail", field="age", reason=f"Not eligible: {label} is for {range_text}, you are {profile.age}.")
    if eligibility.max_age is not None and profile.age > eligibility.max_age:
        return _RuleOutcome("fail", field="age", reason=f"Not eligible: {label} is for {range_text}, you are {profile.age}.")
    return None


def _check_gender(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """A scheme may be restricted to one gender; Gender.ALL means no restriction."""
    if eligibility.gender is Gender.ALL:
        return None
    if profile.gender is None:
        return _RuleOutcome("unknown", field="gender", question="What is your gender?")
    if profile.gender.value != eligibility.gender.value:
        return _RuleOutcome(
            "fail", field="gender",
            reason=f"Not eligible: {label} is only for {eligibility.gender.value} applicants, you are {profile.gender.value}.",
        )  # fmt: skip
    return None


def _check_state(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """A state scheme (or a central one naming specific states) may restrict by state."""
    if not eligibility.allowed_states:
        return None
    if profile.state is None:
        return _RuleOutcome("unknown", field="state", question="Which state or union territory do you live in?")
    if profile.state not in eligibility.allowed_states:
        states = ", ".join(eligibility.allowed_states)
        return _RuleOutcome(
            "fail", field="state",
            reason=f"Not eligible: {label} is only available in {states}, you live in {profile.state}.",
        )  # fmt: skip
    return None


def _check_occupation(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """An empty occupations list means the scheme does not restrict by occupation."""
    if not eligibility.occupations:
        return None
    if profile.occupation is None:
        return _RuleOutcome("unknown", field="occupation", question="What is your main occupation?")
    if profile.occupation not in eligibility.occupations:
        allowed = ", ".join(_humanize(o.value) for o in eligibility.occupations)
        return _RuleOutcome(
            "fail", field="occupation",
            reason=f"Not eligible: {label} is only for these occupations: {allowed}. You are {_humanize(profile.occupation.value)}.",
        )  # fmt: skip
    return None


def _check_social_category(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """An empty social_categories list means the scheme does not restrict by social category."""
    if not eligibility.social_categories:
        return None
    if profile.social_category is None:
        return _RuleOutcome("unknown", field="social_category", question="What is your social category (General/OBC/SC/ST/EWS/Minority)?")
    if profile.social_category not in eligibility.social_categories:
        allowed = ", ".join(c.value.upper() for c in eligibility.social_categories)
        return _RuleOutcome(
            "fail", field="social_category",
            reason=f"Not eligible: {label} is only for these categories: {allowed}. You told us {profile.social_category.value.upper()}.",
        )  # fmt: skip
    return None


def _check_income(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """max_annual_income is None whenever a scheme has no income limit."""
    if eligibility.max_annual_income is None:
        return None
    if profile.annual_income is None:
        return _RuleOutcome(
            "unknown", field="annual_income", question="What is your family's approximate annual income, in rupees?"
        )
    if profile.annual_income > eligibility.max_annual_income:
        return _RuleOutcome(
            "fail", field="annual_income",
            reason=(
                f"Not eligible: {label} is only for families with annual income up to "
                f"Rs {eligibility.max_annual_income:,}, your income is Rs {profile.annual_income:,}."
            ),
        )  # fmt: skip
    return None


def _check_land(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """Land ownership and the maximum landholding size, checked together.

    We only need one field at a time here: if ownership is unknown we ask that first, and
    only ask about the hectare count once we know land is actually owned.
    """
    if not eligibility.requires_own_cultivable_land and eligibility.max_landholding_hectares is None:
        return None
    if profile.owns_cultivable_land is None:
        return _RuleOutcome(
            "unknown", field="owns_cultivable_land",
            question="Do you (or your family) own cultivable agricultural land in your own name?",
        )  # fmt: skip
    if profile.owns_cultivable_land is False:
        if eligibility.requires_own_cultivable_land:
            return _RuleOutcome(
                "fail", field="owns_cultivable_land",
                reason=f"Not eligible: {label} requires owning cultivable land in your own name, you have none.",
            )  # fmt: skip
        return None  # no land, and this scheme doesn't require any; any hectare limit is trivially satisfied
    if eligibility.max_landholding_hectares is not None:
        if profile.landholding_hectares is None:
            return _RuleOutcome(
                "unknown", field="landholding_hectares",
                question="How many hectares of cultivable land do you (or your family) own?",
            )  # fmt: skip
        if profile.landholding_hectares > eligibility.max_landholding_hectares:
            return _RuleOutcome(
                "fail", field="landholding_hectares",
                reason=(
                    f"Not eligible: {label} is only for farmers owning up to "
                    f"{eligibility.max_landholding_hectares:g} hectares, you have {profile.landholding_hectares:g} hectares."
                ),
            )  # fmt: skip
    return None


def _check_high_pensioner(eligibility: Eligibility, profile: UserProfile, label: str) -> _RuleOutcome | None:
    """'high_pensioner' is checked from the actual rupee amount, not a yes/no answer, so
    the same monthly_pension value can be compared against a different threshold per scheme."""
    if eligibility.excluded_pension_monthly_min is None:
        return None
    if profile.monthly_pension is None:
        return _RuleOutcome(
            "unknown", field="monthly_pension",
            question="What is your (or your family's) monthly pension amount, in rupees? Enter 0 if none.",
        )  # fmt: skip
    if profile.monthly_pension >= eligibility.excluded_pension_monthly_min:
        return _RuleOutcome(
            "fail", field="monthly_pension",
            reason=(
                f"Not eligible: {label} excludes pensioners receiving Rs {eligibility.excluded_pension_monthly_min:,} "
                f"or more per month, your pension is Rs {profile.monthly_pension:,}."
            ),
        )  # fmt: skip
    return None


def _check_generic_exclusion(
    category: ExclusionCategory, scope: ExclusionScope, profile: UserProfile, label: str
) -> _RuleOutcome | None:
    """Check one yes/no exclusion category (everything except high_pensioner, which uses
    a rupee amount instead, and institutional_land_holder, which is never asked)."""
    answer = profile.exclusion_answer(category)
    texts = _EXCLUSIONS[category][scope]
    field_key = f"exclusion:{category.value}"
    if answer is None:
        return _RuleOutcome("unknown", field=field_key, question=texts["question"])
    if answer is True:
        return _RuleOutcome("fail", field=field_key, reason=f"Not eligible: {label} — {texts['reason']}.")
    return None


def _check_exclusions(eligibility: Eligibility, profile: UserProfile, label: str) -> list[_RuleOutcome]:
    """Run every exclusion category the scheme lists, skipping the two special-cased ones."""
    outcomes: list[_RuleOutcome] = []
    for category in eligibility.excluded_if:
        if category is ExclusionCategory.INSTITUTIONAL_LAND_HOLDER:
            continue  # our users are always individual people, never institutions
        if category is ExclusionCategory.HIGH_PENSIONER:
            outcome = _check_high_pensioner(eligibility, profile, label)
        else:
            outcome = _check_generic_exclusion(category, eligibility.excluded_scope, profile, label)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def _evaluate_scheme(scheme: Scheme, profile: UserProfile) -> list[_RuleOutcome]:
    """Run every rule for one scheme and collect only the FAILs and UNKNOWNs."""
    eligibility = scheme.eligibility
    label = _scheme_label(scheme)
    outcomes = [
        _check_age(eligibility, profile, label),
        _check_gender(eligibility, profile, label),
        _check_state(eligibility, profile, label),
        _check_occupation(eligibility, profile, label),
        _check_social_category(eligibility, profile, label),
        _check_income(eligibility, profile, label),
        _check_land(eligibility, profile, label),
    ]
    result = [o for o in outcomes if o is not None]
    result.extend(_check_exclusions(eligibility, profile, label))
    return result


def _build_question_queue(missing_by_scheme: dict[str, list[_RuleOutcome]]) -> list[Question]:
    """Merge every possibly-eligible scheme's missing fields into one ordered queue.

    Fields that would decide more schemes at once are asked first; ties are broken by
    ``_PRIORITY_FIELD_ORDER``, and anything not listed there sorts last, alphabetically.
    """
    by_field: dict[str, tuple[str, list[str]]] = {}
    for scheme_id, outcomes in missing_by_scheme.items():
        for outcome in outcomes:
            question, schemes = by_field.setdefault(outcome.field, (outcome.question or "", []))
            schemes.append(scheme_id)

    def sort_key(field_name: str) -> tuple[int, int, str]:
        decides = -len(by_field[field_name][1])
        priority = _PRIORITY_FIELD_ORDER.index(field_name) if field_name in _PRIORITY_FIELD_ORDER else len(_PRIORITY_FIELD_ORDER)
        return (decides, priority, field_name)

    return [
        Question(field=f, question=by_field[f][0], affects_schemes=by_field[f][1])
        for f in sorted(by_field, key=sort_key)
    ]


def check_eligibility(profile: UserProfile, schemes: list[Scheme]) -> EligibilityReport:
    """Check one user's profile against a list of schemes and split them into three groups.

    See the module docstring for exactly how a scheme's status is decided.
    """
    report = EligibilityReport()
    possibly_eligible_outcomes: dict[str, list[_RuleOutcome]] = {}

    for scheme in schemes:
        label = _scheme_label(scheme)
        outcomes = _evaluate_scheme(scheme, profile)
        fails = [o for o in outcomes if o.verdict == "fail"]
        unknowns = [o for o in outcomes if o.verdict == "unknown"]

        if fails:
            report.not_eligible.append(
                SchemeResult(
                    scheme_id=scheme.id, scheme_name=scheme.name_en, status=EligibilityStatus.NOT_ELIGIBLE,
                    reasons=[o.reason for o in fails if o.reason],
                )
            )  # fmt: skip
        elif unknowns:
            # A scheme can list the same field more than once (it can't in practice today,
            # but keep this safe), so de-duplicate before recording it as missing.
            unique_unknowns = list({o.field: o for o in unknowns}.values())
            possibly_eligible_outcomes[scheme.id] = unique_unknowns
            report.possibly_eligible.append(
                SchemeResult(
                    scheme_id=scheme.id, scheme_name=scheme.name_en, status=EligibilityStatus.POSSIBLY_ELIGIBLE,
                    reasons=[f"Possibly eligible for {label}: we need a bit more information to confirm this."],
                    missing_fields=[o.field for o in unique_unknowns],
                )
            )  # fmt: skip
        else:
            report.eligible.append(
                SchemeResult(
                    scheme_id=scheme.id, scheme_name=scheme.name_en, status=EligibilityStatus.ELIGIBLE,
                    reasons=[f"Eligible: you meet all the known eligibility criteria for {label}."],
                )
            )  # fmt: skip

    report.questions = _build_question_queue(possibly_eligible_outcomes)
    return report
