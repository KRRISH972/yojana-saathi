# Data Guide: Adding a Scheme

All schemes live in `data/schemes.json` as one JSON list. Each entry must match the `Scheme` model in `backend/models/scheme.py`.

## Rules

1. **Use official sources only** (the ministry/department website, myscheme.gov.in, the official gazette or guidelines). Never fill in numbers from memory.
2. **Copy, don't guess.** If the official source does not state a limit (age, income), use `null`. `null` and `[]` mean "no restriction".
3. **Set `last_verified_date`** to the day you checked the official source (`YYYY-MM-DD`, not a future date).
4. **Replace every placeholder.** Remove the two `PLACEHOLDER` example entries once you have real ones.

## Steps

1. Copy an existing entry in `data/schemes.json` and edit it.
2. Run the validator from the project root:
   ```
   venv\Scripts\python scripts/validate_data.py
   ```
3. Fix everything it reports until it prints `OK: all entries are valid.`

## Field reference

| Field | Format |
|---|---|
| `id` | Unique lowercase slug, letters/digits/hyphens: `pm-example-scheme` |
| `name_en`, `name_hi` | Official English name and Hindi name (Devanagari) |
| `ministry` | Ministry or department running the scheme |
| `level` | `"central"` or `"state"` |
| `state` | `null` for central; exact official state/UT name for state schemes (e.g. `"Uttar Pradesh"`) |
| `category` | One of: `agriculture`, `education`, `health`, `housing`, `pension`, `women`, `employment`, `financial_inclusion`, `skill_development`, `disability`, `food_security`, `entrepreneurship`, `other` |
| `description_en`, `description_hi` | Short plain-language summary (write simply; rural users will hear this read aloud) |
| `benefits` | What the person receives (as stated officially) |
| `eligibility` | Object, see below |
| `documents_required` | List of strings, at least one |
| `how_to_apply` | Application steps or portal/office to visit |
| `official_url` | Full `https://` link to the official page |
| `last_verified_date` | `YYYY-MM-DD` |

### `eligibility` object

| Field | Format |
|---|---|
| `min_age`, `max_age` | Whole numbers 0–120, or `null`. `min_age` must not exceed `max_age` |
| `max_annual_income` | Rupees per year as a whole number, or `null` |
| `allowed_states` | List of exact official state/UT names; `[]` = all states |
| `occupations` | Any of `farmer`, `agricultural_labourer`, `student`, `salaried_private`, `salaried_government`, `self_employed`, `small_business`, `daily_wage_worker`, `street_vendor`, `artisan`, `unemployed`, `homemaker`, `retired`, `other`; `[]` = any. Use `other` only if nothing fits, and describe it in `other_conditions` |
| `gender` | `"all"`, `"male"`, `"female"` or `"transgender"` |
| `social_categories` | Any of `general`, `obc`, `sc`, `st`, `ews`, `minority`; `[]` = any |
| `requires_own_cultivable_land` | `true` if the person/family must own cultivable land in their own name; default `false` |
| `excluded_if` | List of groups who are NOT eligible (see below); `[]` = no exclusions |
| `excluded_pension_monthly_min` | Monthly pension in rupees at or above which a retired person is excluded. Required if `excluded_if` has `high_pensioner`, and not allowed otherwise |
| `excluded_scope` | `"person"` (only the applicant) or `"family"` (any family member); default `"person"` |
| `other_conditions` | Free text for rules the fields above can't express (e.g. "must not be an income-tax payer") |

#### `excluded_if` values

| Value | Meaning |
|---|---|
| `institutional_land_holder` | Land held by an institution |
| `constitutional_post_holder` | Former or present holder of a constitutional post |
| `elected_representative` | Former or present Minister, MP, MLA, MLC, Mayor, or District Panchayat chairperson |
| `government_employee` | Serving or retired government/PSE/local body employee (Group D / Class IV / MTS staff are not included — describe such exceptions in `other_conditions`) |
| `high_pensioner` | Retired person whose monthly pension is at or above `excluded_pension_monthly_min` |
| `income_tax_payer` | Paid income tax in the last assessment year. This is an exclusion, **not** an income limit, so leave `max_annual_income` as `null` |
| `registered_professional` | Doctor, engineer, lawyer, chartered accountant or architect registered with a professional body and practising |

Only list a group if the official source excludes it. Put exceptions and rules the fields cannot express (tenant farmers, land ownership cut-off dates, eKYC) in `other_conditions`.

Extra or misspelled field names are rejected, so typos are caught by the validator.

## Common validator errors

- `state must be null when level is 'central'` — set `state` to `null`.
- `state is required when level is 'state'` — add the state name.
- `unknown state/UT name` — check the spelling against the list in `backend/models/scheme.py`.
- `Input should be 'farmer', 'agricultural_labourer', …` (on `eligibility.occupations` or `category`/`gender`/`social_categories`) — the value isn't in the allowed list; copy it exactly from the tables above.
- `excluded_pension_monthly_min is required` / `does not contain 'high_pensioner'` — the pension threshold and the `high_pensioner` exclusion must be used together.
- `duplicate id` — every `id` must be unique.
- `entries still contain placeholder data` (warning) — some text still says PLACEHOLDER.
