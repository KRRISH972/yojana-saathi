"""Pydantic models describing a government scheme and its eligibility rules."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

INDIAN_STATES_AND_UTS: frozenset[str] = frozenset(
    {
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
        "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
        "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
        "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
        "Uttar Pradesh", "Uttarakhand", "West Bengal",
        "Andaman and Nicobar Islands", "Chandigarh",
        "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
        "Ladakh", "Lakshadweep", "Puducherry",
    }
)  # fmt: skip

_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Level(StrEnum):
    """Whether a scheme is run by the central or a state government."""

    CENTRAL = "central"
    STATE = "state"


class Category(StrEnum):
    """Broad topic a scheme belongs to. Use OTHER only when nothing else fits."""

    AGRICULTURE = "agriculture"
    EDUCATION = "education"
    HEALTH = "health"
    HOUSING = "housing"
    PENSION = "pension"
    WOMEN = "women"
    EMPLOYMENT = "employment"
    FINANCIAL_INCLUSION = "financial_inclusion"
    SKILL_DEVELOPMENT = "skill_development"
    DISABILITY = "disability"
    FOOD_SECURITY = "food_security"
    ENTREPRENEURSHIP = "entrepreneurship"
    OTHER = "other"


class Gender(StrEnum):
    """Gender restriction of a scheme. ALL means no restriction."""

    ALL = "all"
    MALE = "male"
    FEMALE = "female"
    TRANSGENDER = "transgender"


class SocialCategory(StrEnum):
    """Social category a scheme is restricted to."""

    GENERAL = "general"
    OBC = "obc"
    SC = "sc"
    ST = "st"
    EWS = "ews"
    MINORITY = "minority"


class Occupation(StrEnum):
    """Occupation a scheme is restricted to. Use OTHER only when nothing else fits."""

    FARMER = "farmer"
    AGRICULTURAL_LABOURER = "agricultural_labourer"
    STUDENT = "student"
    SALARIED_PRIVATE = "salaried_private"
    SALARIED_GOVERNMENT = "salaried_government"
    SELF_EMPLOYED = "self_employed"
    SMALL_BUSINESS = "small_business"
    DAILY_WAGE_WORKER = "daily_wage_worker"
    STREET_VENDOR = "street_vendor"
    ARTISAN = "artisan"
    UNEMPLOYED = "unemployed"
    HOMEMAKER = "homemaker"
    RETIRED = "retired"
    OTHER = "other"


class Eligibility(BaseModel):
    """Structured eligibility rules. ``None`` or an empty list means "no restriction"."""

    model_config = ConfigDict(extra="forbid")

    min_age: int | None = Field(default=None, ge=0, le=120)
    max_age: int | None = Field(default=None, ge=0, le=120)
    max_annual_income: int | None = Field(default=None, ge=0, description="In rupees per year.")
    allowed_states: list[str] = Field(default_factory=list)
    occupations: list[Occupation] = Field(default_factory=list)
    gender: Gender = Gender.ALL
    social_categories: list[SocialCategory] = Field(default_factory=list)
    other_conditions: str = Field(default="", description="Anything not captured above, free text.")

    @field_validator("allowed_states")
    @classmethod
    def _states_must_be_known(cls, states: list[str]) -> list[str]:
        """Reject state names that are not official state/UT names."""
        unknown = [s for s in states if s not in INDIAN_STATES_AND_UTS]
        if unknown:
            raise ValueError(f"unknown state/UT name(s): {unknown}. Use the exact official spelling.")
        return states

    @model_validator(mode="after")
    def _age_range_is_ordered(self) -> Self:
        """Ensure min_age is not greater than max_age."""
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            raise ValueError(f"min_age ({self.min_age}) is greater than max_age ({self.max_age}).")
        return self


class Scheme(BaseModel):
    """A single government scheme record."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique lowercase slug, e.g. 'my-scheme-name'.")
    name_en: str = Field(min_length=1)
    name_hi: str = Field(min_length=1)
    ministry: str = Field(min_length=1)
    level: Level
    state: str | None = Field(default=None, description="Must be null for central schemes.")
    category: Category
    description_en: str = Field(min_length=1)
    description_hi: str = Field(min_length=1)
    benefits: str = Field(min_length=1)
    eligibility: Eligibility
    documents_required: list[str] = Field(min_length=1)
    how_to_apply: str = Field(min_length=1)
    official_url: HttpUrl
    last_verified_date: date

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, value: str) -> str:
        """Require ids to be lowercase slugs (letters, digits, single hyphens)."""
        if not _ID_PATTERN.match(value):
            raise ValueError("id must be a lowercase slug like 'pm-example-scheme'.")
        return value

    @field_validator("documents_required")
    @classmethod
    def _documents_not_blank(cls, documents: list[str]) -> list[str]:
        """Reject blank entries in the documents list."""
        if any(not d.strip() for d in documents):
            raise ValueError("documents_required must not contain blank entries.")
        return documents

    @field_validator("last_verified_date")
    @classmethod
    def _date_not_in_future(cls, value: date) -> date:
        """A verification date cannot be in the future."""
        if value > date.today():
            raise ValueError(f"last_verified_date {value} is in the future.")
        return value

    @model_validator(mode="after")
    def _state_matches_level(self) -> Self:
        """Central schemes need state=null; state schemes need a valid state name."""
        if self.level is Level.CENTRAL and self.state is not None:
            raise ValueError("state must be null when level is 'central'.")
        if self.level is Level.STATE:
            if self.state is None:
                raise ValueError("state is required when level is 'state'.")
            if self.state not in INDIAN_STATES_AND_UTS:
                raise ValueError(f"unknown state/UT name {self.state!r}. Use the exact official spelling.")
        return self
