"""Pydantic model for what the assistant currently knows about one citizen."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend.models.scheme import INDIAN_STATES_AND_UTS, ExclusionCategory, Gender, Occupation, SocialCategory


class UserProfile(BaseModel):
    """Everything the assistant has learned about a user so far.

    Every field is optional, because users answer questions bit by bit in conversation.
    ``None`` (or a missing key in ``exclusions``) always means "we don't know yet" and must
    never be treated as "no" — the eligibility engine relies on this to tell a real
    "not eligible" apart from "we just need to ask one more question".
    """

    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(default=None, ge=0, le=120)
    gender: Gender | None = None
    state: str | None = None
    occupation: Occupation | None = None
    social_category: SocialCategory | None = None
    annual_income: int | None = Field(default=None, ge=0, description="Family's annual income, in rupees.")
    owns_cultivable_land: bool | None = None
    landholding_hectares: float | None = Field(default=None, gt=0, description="Only set when land is owned.")
    monthly_pension: int | None = Field(
        default=None, ge=0, description="Rupees per month; 0 means retired but not receiving a pension."
    )
    exclusions: dict[ExclusionCategory, bool] = Field(
        default_factory=dict,
        description=(
            "One yes/no answer per ExclusionCategory the user has been asked about. A missing "
            "key means the question has not been asked yet, i.e. still unknown. "
            "'high_pensioner' is never read from here — see monthly_pension instead, which is "
            "compared against each scheme's own pension threshold. 'institutional_land_holder' "
            "is never asked, because our users are always individual people, never institutions."
        ),
    )

    @field_validator("state")
    @classmethod
    def _state_must_be_known(cls, state: str | None) -> str | None:
        """Reject state names that are not official state/UT names, to avoid silent mismatches."""
        if state is not None and state not in INDIAN_STATES_AND_UTS:
            raise ValueError(f"unknown state/UT name {state!r}. Use the exact official spelling.")
        return state

    @model_validator(mode="after")
    def _hectares_only_if_owns_land(self) -> Self:
        """A landholding size only makes sense once we know land is actually owned."""
        if self.landholding_hectares is not None and self.owns_cultivable_land is False:
            raise ValueError("landholding_hectares is set but owns_cultivable_land is False.")
        return self

    def exclusion_answer(self, category: ExclusionCategory) -> bool | None:
        """Return the stored yes/no answer for an exclusion category, or None if unknown."""
        return self.exclusions.get(category)


def invalid_field_names(error: ValidationError) -> list[str]:
    """Top-level UserProfile field names a validation error is about, safe to log.

    Never includes values, and never includes a key that is not a real field name (such a
    key, or a nested dict key, could have been made up from what the user typed). A
    cross-field error with no single field is reported as "(profile)".
    """
    names: set[str] = set()
    for err in error.errors():
        first = err["loc"][0] if err["loc"] else None
        if first is None:
            names.add("(profile)")
        else:
            names.add(first if first in UserProfile.model_fields else "(unknown field)")
    return sorted(names)
