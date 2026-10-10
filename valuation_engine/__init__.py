"""
valuation_engine - a pure-Python startup valuation calculation engine.

Everything lives in this one module (input models, reference-data loading,
financial projections, WACC, the four valuation methods, the blend and the
input sanity checks) so the whole engine is a single file to read, copy, or
upload - the logic underneath is still organized into clearly separated
sections below.

Methodology (see AUDIT_REPORT.md for why each choice was made):

- Each stage parameter has exactly one meaning and is used by exactly one
  method: the VC target return (Venture Capital method only), the
  private-company discount (Comparables only) and the survival probability
  (DCF only). The Scorecard applies no haircut, as in Payne's original.
- Projected EBITDA starts from the company's own last-12-month margin and
  moves in equal steps to the industry EBITDA margin (Damodaran EBITDA/Sales,
  which already includes R&D) by Year 5.
- Comparables uses trailing (last-12-month) EBITDA with Damodaran's trailing
  EV/EBITDA multiple.
- DCF discounts at WACC only (no stacked hurdle), then weights the result by
  the probability that the business survives.
- Comparables and DCF produce enterprise value; debt is subtracted and cash
  added to get equity value. The VC method already produces equity value.
- A method whose value isn't meaningful (e.g. negative EBITDA) is left out
  of the blend and the remaining stage weights are rescaled.
"""
from __future__ import annotations

import contextvars
import copy
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel as _PydanticModel, ConfigDict, Field, field_validator, model_validator

# ============================================================================
# SECTION 1 — Reference data (industry benchmarks, country data, stage
# parameters, scorecard lookup tables, sources). Bundled as a single JSON
# file alongside this module.
# ============================================================================

_DATA_PATH = Path(__file__).parent / "reference_data.json"


# ============================================================================
# Language: every text the engine writes (warnings, notes, errors, labels)
# exists in English and German. Callers choose with `with language("de"):`;
# the numbers are the same in both.
# ============================================================================

LANGUAGES = ("en", "de")
_LANG: contextvars.ContextVar = contextvars.ContextVar("valuation_language", default="en")


@contextmanager
def language(lang: Optional[str]):
    token = _LANG.set(lang if lang in LANGUAGES else "en")
    try:
        yield
    finally:
        _LANG.reset(token)


def current_language() -> str:
    return _LANG.get()


def _t(en: str, de: str) -> str:
    """The text in the current language."""
    return de if _LANG.get() == "de" else en


def _num(x: float, decimals: int = 0) -> str:
    """1,234,567.8 in English, 1.234.567,8 in German."""
    text = f"{x:,.{decimals}f}"
    return text.translate(str.maketrans(",.", ".,")) if _LANG.get() == "de" else text


_MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
              "November", "Dezember"]
_MONTHS_DE_SHORT = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]


def _month_year(d: date) -> str:
    """'Oct 2027' / 'Okt. 2027'."""
    return _t(f"{d:%b %Y}", f"{_MONTHS_DE_SHORT[d.month - 1]} {d.year}")


def _long_date(d: date) -> str:
    """'06 October 2026' / '6. Oktober 2026'."""
    return _t(f"{d:%d %B %Y}", f"{d.day}. {_MONTHS_DE[d.month - 1]} {d.year}")


STAGE_NAMES_DE = {
    "Idea stage": "Ideenphase",
    "Development stage": "Entwicklungsphase",
    "Startup stage": "Startphase",
    "Expansion stage": "Expansionsphase",
    "Growth stage": "Wachstumsphase",
    "Maturity stage": "Reifephase",
}


def stage_label(stage: str) -> str:
    """The stage as shown to the user: 'Startup stage' / 'Startphase'."""
    return _t(stage, STAGE_NAMES_DE.get(stage, stage))


@lru_cache(maxsize=1)
def _all_reference_data() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def industry_benchmarks() -> dict:
    """industry -> metric -> region -> value"""
    return _all_reference_data()["industry_benchmarks"]


def country_data() -> dict:
    """country -> {moodys_rating, equity_risk_premium, corporate_tax_rate, region_grouping, ...}"""
    return _all_reference_data()["country_data"]


def stage_parameters() -> dict:
    """stage -> {vc_target_return, private_company_discount, survival_probability, method_weights}"""
    return _all_reference_data()["stage_parameters"]


def stage_descriptions() -> dict:
    """stage -> one-line definition shown in the wizard (in the current language)."""
    data = _all_reference_data()
    return data.get("stage_descriptions_de" if _LANG.get() == "de" else "stage_descriptions", {})


def scorecard_benchmarks() -> dict:
    """region -> {eur, usd, basis, regional_figure}: typical pre-revenue pre-money valuation."""
    return _all_reference_data()["scorecard_benchmarks"]["regions"]


def business_regions() -> list[str]:
    return categorical_options()["business_territory_region"]


def scorecard_qualitative_lookup() -> dict:
    """criterion -> {option_text: score}"""
    return _all_reference_data()["scorecard_qualitative_lookup"]


def country_aliases() -> dict:
    """{old country name: current name}, e.g. "Swaziland" -> "Eswatini"."""
    return _all_reference_data().get("country_aliases", {})


def name_sort_key(name: str) -> str:
    """Alphabetical order that ignores accents, so "Côte d'Ivoire" sorts under C and "Türkiye" under T."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c)).casefold()


def industry_aliases() -> dict:
    """{old industry name: current name}, e.g. Damodaran's "Heathcare" spelling."""
    return _all_reference_data().get("industry_aliases", {})


def stage_aliases() -> dict:
    """{old stage name: current name}, e.g. "Growth Stage" -> "Growth stage"."""
    return _all_reference_data().get("stage_aliases", {})


def scorecard_option_aliases() -> dict:
    """criterion -> {old option text: current option text} (old saved valuations keep working)."""
    return _all_reference_data().get("scorecard_option_aliases", {})


def market_parameters() -> dict:
    return _all_reference_data()["market_parameters"]


def saas_arr_multiple() -> dict:
    """{value, as_of, basis}: public SaaS companies' enterprise value / ARR (Comparables, for SaaS)."""
    return _all_reference_data()["saas_arr_multiple"]


def round_benchmarks() -> dict:
    """Typical financing round per stage: dilution and round size (the 'round logic' cross-check)."""
    return _all_reference_data()["round_benchmarks"]


def data_sources() -> dict:
    """Where every benchmark and assumption comes from (in the current language)."""
    data = _all_reference_data()
    return {**data["sources"], **data.get("sources_de", {})} if _LANG.get() == "de" else data["sources"]


def option_label(criterion: str, option_text: Optional[str]) -> Optional[str]:
    """A questionnaire answer as shown to the user (German label in German; the English text stays the key)."""
    if option_text is None or _LANG.get() != "de":
        return option_text
    return _all_reference_data().get("scorecard_option_labels_de", {}).get(criterion, {}).get(option_text, option_text)


def categorical_options() -> dict:
    """Valid values for country / industry / business_territory_region / company_stage."""
    return _all_reference_data()["categorical_options"]


def country_specific(country: str) -> dict:
    """Inputs that replace the generic ones for this country (today: Germany's
    risk-free rate, tax schedule and stage benchmarks). Empty for other countries."""
    return _all_reference_data().get("country_specific", {}).get(country, {})


def _is_usable_number(v: Any, min_valid: Optional[float] = None) -> bool:
    """True for a real, finite number that's at or above min_valid (if given).
    False for Damodaran's raw source artifacts - "NA", "#N/A", "#VALUE!",
    None, etc. - which are strings, not numbers, in the underlying data."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return False
    if min_valid is not None and v < min_valid:
        return False
    return True


def _cross_industry_fallback(metric: str, min_valid: Optional[float] = None) -> Optional[float]:
    """Median of every industry's own Global value for this metric, as a last-resort
    fallback when neither the requested region nor that industry's Global figure is
    usable. Grounded in the same dataset, just aggregated more broadly - not invented."""
    pool = sorted(
        v for data in industry_benchmarks().values()
        for v in [data.get(metric, {}).get("Global")]
        if _is_usable_number(v, min_valid)
    )
    if not pool:
        return None
    mid = len(pool) // 2
    return pool[mid] if len(pool) % 2 else (pool[mid - 1] + pool[mid]) / 2


# Lowest acceptable value per metric. Margins can legitimately be negative
# (None = any number); multiples, betas and capital weights must be positive.
_INDUSTRY_METRIC_MIN_VALID = {
    "beta": 0.01,
    "equity_pct_capital": 0.01,
    "ev_ebitda_multiple": 0.01,
    "ev_sales_multiple": 0.01,
    "ebitda_margin": None,
}


def _min_valid(metric: str) -> Optional[float]:
    return _INDUSTRY_METRIC_MIN_VALID.get(metric, 0.0)


def get_industry_metric_with_source(industry: str, metric: str, region: str) -> tuple[float, str]:
    """
    Resilient accessor for an industry benchmark. Damodaran's raw tables contain
    literal "NA" text where a region has too few companies or the figure isn't
    meaningful (e.g. EV/EBITDA for banks).

    Falls back in order: the requested region -> that industry's own "Global"
    figure -> the median "Global" figure across all industries for this metric.
    Returns (value, source) with source "region", "industry_global" or
    "cross_industry", so every fallback can be disclosed in the report.
    """
    min_valid = _min_valid(metric)
    try:
        table = industry_benchmarks()[industry][metric]
    except KeyError as e:
        raise KeyError(
            f"No benchmark for industry={industry!r}, metric={metric!r}, region={region!r}"
        ) from e

    region_val = table.get(region)
    if _is_usable_number(region_val, min_valid):
        return region_val, "region"

    global_val = table.get("Global")
    if _is_usable_number(global_val, min_valid):
        return global_val, "industry_global"

    fallback = _cross_industry_fallback(metric, min_valid)
    if fallback is not None:
        return fallback, "cross_industry"

    raise KeyError(
        f"No usable benchmark for industry={industry!r}, metric={metric!r}, region={region!r}"
    )


def get_industry_metric(industry: str, metric: str, region: str) -> float:
    return get_industry_metric_with_source(industry, metric, region)[0]


def resolved_industry_benchmarks(industry: str) -> dict:
    """An industry's benchmark table with the same fallback chain the engine
    applies, so anything displayed (wizard, PDF) matches what was calculated.
    Shape: {metric: {region: value}}; value is None only if nothing resolves."""
    resolved = {}
    for metric, table in industry_benchmarks()[industry].items():
        resolved[metric] = {}
        for region in table:
            try:
                resolved[metric][region] = get_industry_metric(industry, metric, region)
            except KeyError:
                resolved[metric][region] = None
    return resolved


def get_country(country: str) -> dict:
    try:
        return country_data()[country]
    except KeyError as e:
        raise KeyError(f"No country data for {country!r}") from e


def get_stage_params(stage: str) -> dict:
    try:
        return stage_parameters()[stage]
    except KeyError as e:
        raise KeyError(f"No stage parameters for {stage!r}") from e


def canonical_option(criterion: str, option_text: str) -> str:
    """Maps an old (misspelled) option text to its current wording."""
    return scorecard_option_aliases().get(criterion, {}).get(option_text, option_text)


def get_scorecard_score(criterion: str, option_text: str) -> float:
    table = scorecard_qualitative_lookup()[criterion]
    text = canonical_option(criterion, option_text)
    if text not in table:
        raise KeyError(f"Unknown answer {option_text!r} for {criterion!r}")
    return table[text]


# Industries where EBITDA, EV/EBITDA and free cash flow aren't meaningful
# (debt is raw material, not financing), so the VC, Comparables and DCF
# methods are not applied.
FINANCIAL_SECTOR_INDUSTRIES = {
    "Bank (Money Center)",
    "Banks (Regional)",
    "Brokerage & Investment Banking",
    "Financial Services (Non-bank & Insurance)",
    "Insurance (General)",
    "Insurance (Life)",
    "Insurance (Property & Casualty)",
    "Investments & Asset Management",
    "Reinsurance",
}

# Which business region a country's companies are usually benchmarked in.
_COUNTRY_DEFAULT_REGION = {
    "United States": "US",
    "Japan": "Japan",
    "China": "China",
    "India": "India",
}
_EUROPE_GROUPINGS = {"Western Europe"}
EUROPE_REGION = "Europe (EU, UK, Switzerland & Scandinavia)"
EMERGING_REGION = "Emerging Markets (Asia, Latin America, Eastern Europe, Mid East and Africa)"


def default_region_for_country(country: str) -> str:
    if country in _COUNTRY_DEFAULT_REGION:
        return _COUNTRY_DEFAULT_REGION[country]
    grouping = get_country(country).get("region_grouping")
    if grouping in _EUROPE_GROUPINGS:
        return EUROPE_REGION
    if grouping in ("North America", "Australia & New Zealand"):
        return "Global"
    return EMERGING_REGION


# ============================================================================
# SECTION 2 — Input models
#
# Validation here is the single source of truth for what the API accepts;
# out-of-range values are rejected with a readable message instead of
# crashing a calculation later.
# ============================================================================


class BaseModel(_PydanticModel):
    """Every input model: "NaN" and "Infinity" are not numbers a valuation can use."""
    model_config = ConfigDict(allow_inf_nan=False)


# Largest amount (in euros) any input may hold: far above any real company, low enough that every
# result and the PDF stay printable.
MAX_AMOUNT = 10_000_000_000_000


class CompanyProfile(BaseModel):
    company_name: str = Field(min_length=1)
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    country: str
    website: Optional[str] = None
    num_founders: Optional[int] = Field(default=None, ge=0)
    num_employees: Optional[int] = Field(default=None, ge=0)
    year_of_incorporation: Optional[int] = Field(default=None, ge=1800, le=2100)
    company_stage: str  # must match a key in stage_parameters
    committed_capital: float = Field(default=0.0, ge=0, le=MAX_AMOUNT)
    business_activity: Optional[str] = None
    industry: str  # must match a key in industry_benchmarks
    business_territory_region: str  # must match a region key, e.g. "Emerging Markets (...)"
    business_model: Optional[str] = None
    exit_strategy: Optional[str] = None
    planned_time_to_exit_years: int = Field(default=3, ge=1, le=5)

    # Date the valuation is made as of; Year 1 is the 12 months after it.
    # Defaults to today.
    valuation_date: Optional[date] = None

    # Tax rate used for the free cash flows AND the WACC. Leave empty to use
    # the country's statutory corporate tax rate (Germany: the year-by-year
    # schedule). (Name kept for saved data.)
    dcf_tax_rate_override: Optional[float] = Field(default=None, ge=0, le=0.6)

    # Germany only: the municipality's trade-tax multiplier as a percentage
    # (e.g. 490 for Munich). Leave empty for the national average.
    trade_tax_hebesatz: Optional[float] = Field(default=None, ge=200, le=1000)

    # Optional replacement for the regional "typical pre-revenue pre-money"
    # benchmark used by the Scorecard method (built-in: Equidam H1 2026 medians).
    benchmark_pre_money_override: Optional[float] = Field(default=None, gt=0, le=MAX_AMOUNT)

    # Optional company logo for the PDF report cover page/header. Either a
    # plain base64 string or a data URL (e.g. "data:image/png;base64,....").
    logo_base64: Optional[str] = None

    @field_validator("industry")
    @classmethod
    def _current_industry_name(cls, v):
        """Inputs using an old industry name (e.g. a corrected misspelling) keep working."""
        return industry_aliases().get(v, v)

    @field_validator("company_stage")
    @classmethod
    def _current_stage_name(cls, v):
        """Inputs saved with an old stage spelling ("Growth Stage") keep working."""
        return stage_aliases().get(v, v)

    @field_validator("country")
    @classmethod
    def _current_country_name(cls, v):
        """Inputs using an old country name (e.g. "Swaziland") keep working."""
        return country_aliases().get(v, v)


class MarketAndTeamAssessment(BaseModel):
    """
    Each answer must be an option string present in
    scorecard_qualitative_lookup() under the matching key (or an old
    spelling listed in scorecard_option_aliases). An answer may be left
    out (None): it is then scored as typical (100%) and, if the Scorecard
    is used, flagged in the warnings. Companies with revenue don't need to
    answer, since the Scorecard only counts before revenue.
    """
    management_team_experience: Optional[str] = None
    willingness_to_step_aside_for_ceo: Optional[str] = None
    management_team_completeness: Optional[str] = None
    product_development_stage: Optional[str] = None
    product_compelling_to_customers: Optional[str] = None
    product_can_be_duplicated: Optional[str] = None
    strength_of_competitors_in_market: Optional[str] = None
    strength_of_competitive_products: Optional[str] = None
    target_market_size: Optional[str] = None
    revenue_potential_in_5_years: Optional[str] = None
    sales_channels_partners: Optional[str] = None
    marketing_partners: Optional[str] = None
    need_for_additional_funding_rounds: Optional[str] = None
    key_competitor_1: Optional[str] = None
    key_competitor_2: Optional[str] = None
    key_competitor_3: Optional[str] = None

    @model_validator(mode="after")
    def _current_wording(self):
        """Old saved answers with misspelled option text are mapped to today's wording."""
        for criterion in scorecard_qualitative_lookup():
            if hasattr(self, criterion) and getattr(self, criterion) is not None:
                object.__setattr__(self, criterion, canonical_option(criterion, getattr(self, criterion)))
        return self


class OperatingPerformance(BaseModel):
    current_revenue_last_12_months: float = Field(ge=0, le=MAX_AMOUNT)
    current_ebitda: float = Field(ge=-MAX_AMOUNT, le=MAX_AMOUNT)
    cash_available: float = Field(default=0.0, ge=0, le=MAX_AMOUNT)
    current_ppe_value: float = Field(default=0.0, ge=0, le=MAX_AMOUNT)
    # Subscription (SaaS) companies: annualised recurring revenue today (monthly recurring revenue x 12).
    # When given, the Comparables method values revenue with the SaaS ARR multiple instead of EV/Sales.
    annual_recurring_revenue: Optional[float] = Field(default=None, ge=0, le=MAX_AMOUNT)

    @field_validator("current_ebitda")
    @classmethod
    def _ebitda_within_revenue(cls, v, info):
        """EBITDA is what is left of revenue after operating costs, so it can't be larger (same rule as the wizard)."""
        revenue = info.data.get("current_revenue_last_12_months")
        if revenue is not None and v > revenue:
            if revenue == 0:
                raise ValueError("with no revenue, EBITDA can't be positive; enter 0 or your operating loss as a "
                                 "negative number")
            raise ValueError("EBITDA can't be larger than revenue (it is what's left of revenue after operating costs)")
        return v


class FinancialAssumptions(BaseModel):
    # Set to revenue_by_year[0] when that is given (which may be 0: sales start later).
    revenue_year1: float = Field(gt=0, le=MAX_AMOUNT)
    # growth rate applied to get Y2, Y3, Y4, Y5 from the prior year (as fractions, e.g. 0.10)
    revenue_growth_rates: list[float] = Field(default_factory=lambda: [0.10, 0.10, 0.10, 0.10])
    # Optional revenue for each of Y1..Y5, used instead of revenue_year1 and the growth rates. Years before
    # sales start may be 0 (a biotech that licenses from Year 4, deep tech with a long build phase).
    revenue_by_year: Optional[list[float]] = None
    # capex for Y1..Y5 (5 values)
    capex_by_year: list[float] = Field(default_factory=lambda: [0, 30000, 30000, 30000, 30000])
    # outstanding interest-bearing debt today (subtracted to get equity value)
    existing_debt_balance: float = Field(default=0.0, ge=0, le=MAX_AMOUNT)
    # Optional Year-5 EBITDA margin to use instead of the industry's.
    target_ebitda_margin_override: Optional[float] = Field(default=None, ge=-1, le=0.9)

    @field_validator("revenue_by_year")
    @classmethod
    def _five_revenue_years(cls, v):
        if v is None:
            return v
        if len(v) != 5:
            raise ValueError("revenue_by_year must have exactly 5 values (for Y1..Y5)")
        if any(r < 0 for r in v):
            raise ValueError("revenue can't be negative")
        if any(r > MAX_AMOUNT for r in v):
            raise ValueError(f"revenue can be at most {MAX_AMOUNT:,.0f}")
        if not any(r > 0 for r in v):
            raise ValueError("enter revenue for at least one of the five years; the cash-flow methods need a "
                             "revenue plan")
        first = next(i for i, r in enumerate(v) if r > 0)
        if any(r == 0 for r in v[first:]):
            raise ValueError("once sales have started, every later year needs revenue above 0")
        return v

    @model_validator(mode="before")
    @classmethod
    def _year1_placeholder(cls, data):
        """With a year-by-year plan, Year-1 revenue comes from it (below), so it isn't checked on its own."""
        if isinstance(data, dict) and data.get("revenue_by_year") is not None:
            data = {**data, "revenue_year1": 1.0}
        return data

    @model_validator(mode="after")
    def _year1_from_plan(self):
        if self.revenue_by_year is not None:
            object.__setattr__(self, "revenue_year1", self.revenue_by_year[0])
        return self

    def revenues(self) -> list[float]:
        """Planned revenue for Y1..Y5."""
        if self.revenue_by_year is not None:
            return list(self.revenue_by_year)
        out = [self.revenue_year1]
        for g in self.revenue_growth_rates:
            out.append(out[-1] * (1 + g))
        return out

    @field_validator("revenue_growth_rates")
    @classmethod
    def _four_growth_rates(cls, v):
        if len(v) != 4:
            raise ValueError("revenue_growth_rates must have exactly 4 values (for Y2, Y3, Y4, Y5)")
        if any(g <= -1 or g > 10 for g in v):
            raise ValueError("each growth rate must be above -100% and at most 1000%")
        return v

    @field_validator("capex_by_year")
    @classmethod
    def _five_capex_years(cls, v):
        if len(v) != 5:
            raise ValueError("capex_by_year must have exactly 5 values (for Y1..Y5)")
        if any(c < 0 for c in v):
            raise ValueError("capex can't be negative")
        if any(c > MAX_AMOUNT for c in v):
            raise ValueError(f"capex can be at most {MAX_AMOUNT:,.0f}")
        return v


class Shareholder(BaseModel):
    name: str
    ownership_pct: float = Field(ge=0, le=1)


class FundingRequirement(BaseModel):
    capital_needed: float = Field(gt=0, le=MAX_AMOUNT)
    use_of_funds: dict[str, float] = Field(default_factory=dict)

    @field_validator("use_of_funds")
    @classmethod
    def _non_negative(cls, v):
        if any(x < 0 for x in v.values()):
            raise ValueError("use-of-funds amounts can't be negative")
        if any(x > MAX_AMOUNT for x in v.values()):
            raise ValueError(f"use-of-funds amounts can be at most {MAX_AMOUNT:,.0f}")
        return v


class VCMethodAssumptions(BaseModel):
    """Assumptions specific to the Venture Capital method."""
    number_of_existing_shares: float = Field(default=1_000_000.0, ge=1, le=MAX_AMOUNT)


class ValuationInput(BaseModel):
    """Top-level input bundle - everything the engine needs for one valuation run."""
    company_profile: CompanyProfile
    market_and_team_assessment: MarketAndTeamAssessment
    operating_performance: OperatingPerformance
    financial_assumptions: FinancialAssumptions
    ownership: list[Shareholder] = Field(default_factory=list)
    funding: FundingRequirement
    vc_assumptions: VCMethodAssumptions = Field(default_factory=VCMethodAssumptions)


# ============================================================================
# SECTION 3 — Shared helpers: tax rate, dates, benchmark lookups with sources
# ============================================================================


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def valuation_date_of(company: CompanyProfile) -> date:
    return company.valuation_date or date.today()


PROJECTION_YEARS = 5


@dataclass
class TaxSchedule:
    """The tax rate for each projection year, and the long-run rate used for the
    terminal value and the WACC. One flat rate everywhere except where a country
    has a legislated schedule (Germany) and the user hasn't entered their own rate."""
    basis: str  # "user_override" | "country_statutory" | "germany_schedule"
    rates_by_year: list[float]
    long_run_rate: float
    # Germany only: the Hebesatz used and the combined rate per calendar year.
    hebesatz: Optional[float] = None  # e.g. 4.09 for 409%
    hebesatz_source: Optional[str] = None  # "national_average" | "user"
    calendar_years: list[dict] = field(default_factory=list)


def _german_rate_for_calendar_year(tax: dict, year: int, hebesatz: float) -> dict:
    schedule = {int(y): r for y, r in tax["corporate_tax_by_year"].items()}
    first, last = min(schedule), max(schedule)
    kst = schedule[min(max(year, first), last)]
    soli = kst * tax["solidarity_surcharge"]
    trade = tax["trade_tax_base_rate"] * hebesatz
    return {"year": year, "corporate_tax": kst, "solidarity_surcharge": soli,
            "trade_tax": trade, "combined": kst + soli + trade}


def _german_tax_schedule(company: CompanyProfile, tax: dict) -> TaxSchedule:
    if company.trade_tax_hebesatz is not None:
        hebesatz, hebesatz_source = company.trade_tax_hebesatz / 100, "user"
    else:
        hebesatz, hebesatz_source = tax["average_hebesatz"], "national_average"
    vdate = valuation_date_of(company)
    last_scheduled = max(int(y) for y in tax["corporate_tax_by_year"])
    last_year = max(_add_years(vdate, PROJECTION_YEARS).year, last_scheduled)
    by_year = {y: _german_rate_for_calendar_year(tax, y, hebesatz) for y in range(vdate.year, last_year + 1)}

    # A projection year usually spans two calendar years; its rate is the two
    # years' rates weighted by the number of days falling in each.
    rates = []
    for i in range(PROJECTION_YEARS):
        start, end = _add_years(vdate, i), _add_years(vdate, i + 1)
        weighted = 0.0
        for y in range(start.year, end.year + 1):
            days = (min(end, date(y + 1, 1, 1)) - max(start, date(y, 1, 1))).days
            if days > 0:
                weighted += days * by_year[y]["combined"]
        rates.append(weighted / (end - start).days)

    return TaxSchedule(
        basis="germany_schedule",
        rates_by_year=rates,
        long_run_rate=by_year[last_year]["combined"],
        hebesatz=hebesatz,
        hebesatz_source=hebesatz_source,
        calendar_years=list(by_year.values()),
    )


def tax_schedule(company: CompanyProfile) -> TaxSchedule:
    """The user's flat rate if given; else the country's legislated schedule
    (Germany); else the country's single statutory rate."""
    if company.dcf_tax_rate_override is not None:
        rate = company.dcf_tax_rate_override
        return TaxSchedule("user_override", [rate] * PROJECTION_YEARS, rate)
    german_tax = country_specific(company.country).get("tax")
    if german_tax:
        return _german_tax_schedule(company, german_tax)
    rate = get_country(company.country)["corporate_tax_rate"]
    return TaxSchedule("country_statutory", [rate] * PROJECTION_YEARS, rate)


def effective_tax_rate(company: CompanyProfile) -> float:
    """The long-run tax rate (used for the WACC and the terminal value). Equal to
    the single rate everywhere except under a stepped schedule (Germany)."""
    return tax_schedule(company).long_run_rate


def risk_free_rate_for(company: CompanyProfile) -> tuple[float, str]:
    """(rate, source): the country's own government-bond yield where one is
    configured (Germany: 10-year Bund), else the default long-term US Treasury rate."""
    own = country_specific(company.country).get("risk_free_rate")
    if own:
        return own["value"], own["source"]
    return market_parameters()["risk_free_rate"], data_sources()["risk_free_rate"]


@dataclass
class BenchmarkUsed:
    metric: str
    value: float
    source: str  # "region" | "industry_global" | "cross_industry" | "user_override"


class _BenchmarkRecorder:
    """Looks up benchmarks and remembers where each one came from."""

    def __init__(self, industry: str, region: str):
        self.industry, self.region = industry, region
        self.used: dict[str, BenchmarkUsed] = {}

    def get(self, metric: str) -> float:
        value, source = get_industry_metric_with_source(self.industry, metric, self.region)
        self.used[metric] = BenchmarkUsed(metric, value, source)
        return value


# ============================================================================
# SECTION 4 — Financial projections
# ============================================================================


@dataclass
class YearProjection:
    year_label: str
    period_label: str  # e.g. "to Sep 2027"
    revenue: float
    ebitda_margin: Optional[float]  # None in a year before sales start
    ebitda: float
    da: float
    ebit: float
    tax_rate: float
    tax_on_ebit: float
    accounts_receivable: float
    inventory: float
    accounts_payable: float
    working_capital: float
    change_in_working_capital: float
    capex: float
    unlevered_fcf: float


@dataclass
class FinancialProjections:
    valuation_date: str
    starting_ebitda_margin: float
    starting_margin_source: str  # "company" (last-12-month margin) | "company_costs" (no revenue yet: EBITDA / Year-1 revenue)
    target_ebitda_margin: float
    target_margin_source: str  # "industry" | "user_override"
    tax_rate: float  # long-run rate (terminal value, WACC); the single rate unless taxes are stepped
    opening_working_capital: float
    years: list[YearProjection] = field(default_factory=list)
    tax_schedule: Optional[TaxSchedule] = None
    # Base of the terminal value: Year-5 free cash flow taxed in full at the long-run
    # rate, with the working-capital change at the long-run growth rate.
    terminal_fcf: Optional[float] = None

    def revenue(self) -> list[float]:
        return [y.revenue for y in self.years]

    def ebitda(self) -> list[float]:
        return [y.ebitda for y in self.years]

    def fcf(self) -> list[float]:
        return [y.unlevered_fcf for y in self.years]


def build_projections(
    company: CompanyProfile,
    assumptions: FinancialAssumptions,
    operating: OperatingPerformance,
    bench: Optional[_BenchmarkRecorder] = None,
) -> FinancialProjections:
    bench = bench or _BenchmarkRecorder(company.industry, company.business_territory_region)

    if assumptions.target_ebitda_margin_override is not None:
        target_margin, target_src = assumptions.target_ebitda_margin_override, "user_override"
    else:
        target_margin, target_src = bench.get("ebitda_margin"), "industry"

    revenues = assumptions.revenues()
    # First plan year with revenue (Year 1 unless the user's plan starts sales later).
    first = next(i for i, r in enumerate(revenues) if r > 0)

    # Start from the company's own margin and move in equal steps to the target
    # margin, reached in Year 5. Without revenue yet, today's EBITDA (usually the
    # operating loss) is measured against the first year's revenue plan, so current
    # costs carry into that year instead of the industry margin applying at once.
    if operating.current_revenue_last_12_months > 0:
        start_margin, start_src = operating.current_ebitda / operating.current_revenue_last_12_months, "company"
    else:
        start_margin, start_src = operating.current_ebitda / revenues[first], "company_costs"
    start_margin = max(-1.0, min(start_margin, 0.9))

    da_pct = bench.get("da_pct_revenue")
    wc_pct = (bench.get("acc_receivable_pct_revenue") + bench.get("inventory_pct_revenue")
              - bench.get("acc_payable_pct_revenue"))
    ar_pct = bench.used["acc_receivable_pct_revenue"].value
    inv_pct = bench.used["inventory_pct_revenue"].value
    ap_pct = bench.used["acc_payable_pct_revenue"].value

    taxes = tax_schedule(company)
    vdate = valuation_date_of(company)

    opening_wc = operating.current_revenue_last_12_months * wc_pct
    prior_wc = opening_wc
    loss_carryforward = 0.0
    years: list[YearProjection] = []
    n = len(revenues)
    for i, revenue in enumerate(revenues):
        if i < first:
            # Before sales start, today's costs carry on: EBITDA stays at today's (a loss, or 0).
            margin, ebitda = None, min(operating.current_ebitda, 0.0)
        else:
            margin = start_margin + (target_margin - start_margin) * (i - first + 1) / (n - first)
            ebitda = revenue * margin
        da = da_pct * revenue
        ebit = ebitda - da

        # Unlevered tax on EBIT; losses are carried forward against later profits.
        tax_rate = taxes.rates_by_year[i]
        taxable = 0.0
        if ebit <= 0:
            loss_carryforward += -ebit
        else:
            used = min(loss_carryforward, ebit)
            loss_carryforward -= used
            taxable = ebit - used
        tax = taxable * tax_rate

        working_capital = revenue * wc_pct
        change_in_wc = working_capital - prior_wc
        prior_wc = working_capital
        capex = assumptions.capex_by_year[i]
        fcf = ebit - tax + da - capex - change_in_wc
        # Base of the terminal value, a business growing at the long-run rate forever: Year 5 taxed at the
        # long-run rate on its full profit (losses carried into Year 5 are used up there, not a tax saving
        # that lasts forever); capex at least D&A (a growing business can't invest less than its assets wear
        # out); working capital growing at the long-run rate, not at Year 5's growth rate.
        terminal_fcf = (ebit - max(ebit, 0.0) * taxes.long_run_rate + da - max(capex, da)
                        - working_capital * market_parameters()["perpetual_growth_rate"])

        period_end = _add_years(vdate, i + 1) - timedelta(days=1)
        years.append(YearProjection(
            year_label=_t(f"Y{i + 1}", f"J{i + 1}"),
            period_label=_t("to ", "bis ") + _month_year(period_end),
            revenue=revenue,
            ebitda_margin=margin,
            ebitda=ebitda,
            da=da,
            ebit=ebit,
            tax_rate=tax_rate,
            tax_on_ebit=tax,
            accounts_receivable=ar_pct * revenue,
            inventory=inv_pct * revenue,
            accounts_payable=ap_pct * revenue,
            working_capital=working_capital,
            change_in_working_capital=change_in_wc,
            capex=capex,
            unlevered_fcf=fcf,
        ))

    return FinancialProjections(
        valuation_date=vdate.isoformat(),
        starting_ebitda_margin=start_margin,
        starting_margin_source=start_src,
        target_ebitda_margin=target_margin,
        target_margin_source=target_src,
        tax_rate=taxes.long_run_rate,
        opening_working_capital=opening_wc,
        years=years,
        tax_schedule=taxes,
        terminal_fcf=terminal_fcf,
    )


# ============================================================================
# SECTION 5 — WACC (discount rate for the DCF)
# ============================================================================


def _mround(value: float, multiple: float) -> float:
    """Excel MROUND equivalent."""
    return round(value / multiple) * multiple


@dataclass
class WaccResult:
    beta: float
    country_equity_risk_premium: float
    risk_free_rate: float
    cost_of_equity: float
    pre_tax_cost_of_debt: float
    tax_rate: float
    after_tax_cost_of_debt: float
    equity_pct_capital: float
    debt_pct_capital: float
    wacc: float
    risk_free_rate_source: str = ""


def compute_wacc(company: CompanyProfile, bench: Optional[_BenchmarkRecorder] = None) -> WaccResult:
    bench = bench or _BenchmarkRecorder(company.industry, company.business_territory_region)
    country = get_country(company.country)
    rf, rf_source = risk_free_rate_for(company)

    beta = bench.get("beta")
    # Total equity risk premium for the country (mature-market premium plus the
    # country's own premium), so cost of equity = rf + beta x ERP (CAPM).
    erp = country["equity_risk_premium"]
    cost_of_equity = _mround(rf + beta * erp, 0.0025)

    kd = bench.get("cost_of_debt")
    tax_rate = effective_tax_rate(company)
    after_tax_kd = (1 - tax_rate) * kd

    we = bench.get("equity_pct_capital")
    wd = bench.get("debt_pct_capital")
    total = we + wd
    we, wd = we / total, wd / total  # Damodaran's weights sum to 1; normalize defensively

    wacc = _mround(wd * after_tax_kd + we * cost_of_equity, 0.0025)

    return WaccResult(
        beta=beta,
        country_equity_risk_premium=erp,
        risk_free_rate=rf,
        cost_of_equity=cost_of_equity,
        pre_tax_cost_of_debt=kd,
        tax_rate=tax_rate,
        after_tax_cost_of_debt=after_tax_kd,
        equity_pct_capital=we,
        debt_pct_capital=wd,
        wacc=wacc,
        risk_free_rate_source=rf_source,
    )


# ============================================================================
# SECTION 6 — Scorecard method (Bill Payne / Ohio TechAngels)
#
# Pre-money = benchmark pre-money for the stage/region x sum(weight x score).
# No extra haircut: the benchmark is already a typical pre-money for a
# company at this stage.
# ============================================================================

SCORECARD_CRITERIA_WEIGHTS = {
    "strength_of_the_team": 0.30,
    "size_of_the_opportunity": 0.25,
    "competitive_environment": 0.10,
    "strength_and_protection_of_product": 0.15,
    "strategic_relationships_with_partners": 0.10,
    "funding_required": 0.10,
}

SCORECARD_CRITERIA_QUESTIONS = {
    "strength_of_the_team": ["management_team_experience", "willingness_to_step_aside_for_ceo",
                             "management_team_completeness"],
    "size_of_the_opportunity": ["target_market_size", "revenue_potential_in_5_years"],
    "competitive_environment": ["strength_of_competitors_in_market", "strength_of_competitive_products"],
    "strength_and_protection_of_product": ["product_development_stage", "product_compelling_to_customers",
                                           "product_can_be_duplicated"],
    "strategic_relationships_with_partners": ["sales_channels_partners", "marketing_partners"],
    "funding_required": ["need_for_additional_funding_rounds"],
}


@dataclass
class ScorecardCriterionResult:
    weight: float
    score: float
    amount_assigned: float


@dataclass
class ScorecardResult:
    criteria: dict[str, ScorecardCriterionResult]
    benchmark_pre_money_valuation: float
    benchmark_source: str  # "table" | "country_table" | "user_override"
    benchmark_basis: str  # where the built-in figure comes from
    total_factor: float
    pre_money_valuation: float
    benchmark_to_be_sourced: bool = False  # the country table still holds placeholder figures
    unanswered: list[str] = field(default_factory=list)  # questions scored as typical (100%)


def country_stage_benchmark(country: str, stage: str) -> Optional[dict]:
    """The country's own typical pre-money for this stage (Germany), or None."""
    return country_specific(country).get("stage_benchmarks", {}).get("stages", {}).get(stage)


def compute_scorecard(company: CompanyProfile, market: MarketAndTeamAssessment) -> ScorecardResult:
    to_be_sourced = False
    country_row = country_stage_benchmark(company.country, company.company_stage)
    if company.benchmark_pre_money_override is not None:
        benchmark, source, basis = (company.benchmark_pre_money_override, "user_override",
                                    _t("Your own benchmark", "Ihr eigener Vergleichswert"))
    elif country_row:
        benchmark, source, basis = country_row["eur"], "country_table", _t(country_row["basis"],
                                                                           country_row.get("basis_de", country_row["basis"]))
        to_be_sourced = bool(country_row.get("to_be_sourced"))
    else:
        try:
            row = scorecard_benchmarks()[company.business_territory_region]
        except KeyError as e:
            raise KeyError(f"No Scorecard benchmark for {company.business_territory_region!r}") from e
        benchmark, source, basis = row["eur"], "table", _t(row["basis"], row.get("basis_de", row["basis"]))

    criteria_results = {}
    total_factor = 0.0
    unanswered = [q for qs in SCORECARD_CRITERIA_QUESTIONS.values() for q in qs if getattr(market, q) is None]
    for key, weight in SCORECARD_CRITERIA_WEIGHTS.items():
        questions = SCORECARD_CRITERIA_QUESTIONS[key]
        # An unanswered question counts as "typical" (score 1.0).
        score = sum(1.0 if getattr(market, q) is None else get_scorecard_score(q, getattr(market, q))
                    for q in questions) / len(questions)
        criteria_results[key] = ScorecardCriterionResult(
            weight=weight, score=score, amount_assigned=benchmark * weight * score)
        total_factor += weight * score

    return ScorecardResult(
        criteria=criteria_results,
        benchmark_pre_money_valuation=benchmark,
        benchmark_source=source,
        benchmark_basis=basis,
        total_factor=total_factor,
        pre_money_valuation=benchmark * total_factor,
        benchmark_to_be_sourced=to_be_sourced,
        unanswered=unanswered,
    )


# ============================================================================
# SECTION 7 — Venture Capital method
#
# Exit value = exit-year EBITDA x EV/EBITDA multiple; today's debt is assumed
# still outstanding at exit, so exit equity = exit value - debt. Post-money
# today = exit equity / (1 + target return)^years. Pre-money = post - investment.
# ============================================================================


@dataclass
class VCMethodResult:
    exit_year_label: str
    exit_year_revenue: float
    exit_year_ebitda: float
    ev_ebitda_multiple: float
    ev_ebitda_multiple_source: str
    exit_value: float
    debt: float
    exit_equity_value: float
    time_to_exit: int
    target_return: float
    investment_amount: float
    number_of_existing_shares: float
    post_money_valuation: Optional[float]
    pre_money_valuation: Optional[float]
    ownership_fraction_investors: Optional[float]
    ownership_fraction_entrepreneurs: Optional[float]
    number_of_new_shares: Optional[float]
    price_per_share: Optional[float]
    final_wealth_investors: Optional[float]
    final_wealth_entrepreneurs: Optional[float]
    not_meaningful_reason: Optional[str]  # the method doesn't apply to this company (left out of the blend)
    # The method applies but leaves no value before the round, so it counts as €0 (not left out:
    # dropping it would hand its weight to the other methods and raise the blend on bad news).
    no_value_reason: Optional[str] = None


def compute_venture_capital(
    company: CompanyProfile,
    projections: FinancialProjections,
    funding: FundingRequirement,
    vc_assumptions: VCMethodAssumptions,
    bench: Optional[_BenchmarkRecorder] = None,
    debt: float = 0.0,
) -> VCMethodResult:
    bench = bench or _BenchmarkRecorder(company.industry, company.business_territory_region)
    target_return = get_stage_params(company.company_stage)["vc_target_return"]
    T = company.planned_time_to_exit_years
    exit_year = projections.years[T - 1]

    multiple = bench.get("ev_ebitda_multiple")
    multiple_source = bench.used["ev_ebitda_multiple"].source
    exit_value = exit_year.ebitda * multiple
    exit_equity = exit_value - debt
    I = funding.capital_needed
    x = vc_assumptions.number_of_existing_shares

    result = dict(
        exit_year_label=exit_year.year_label, exit_year_revenue=exit_year.revenue,
        exit_year_ebitda=exit_year.ebitda, ev_ebitda_multiple=multiple,
        ev_ebitda_multiple_source=multiple_source, exit_value=exit_value, debt=debt,
        exit_equity_value=exit_equity, time_to_exit=T,
        target_return=target_return, investment_amount=I, number_of_existing_shares=x,
        post_money_valuation=None, pre_money_valuation=None, ownership_fraction_investors=None,
        ownership_fraction_entrepreneurs=None, number_of_new_shares=None, price_per_share=None,
        final_wealth_investors=None, final_wealth_entrepreneurs=None, not_meaningful_reason=None,
    )

    if company.industry in FINANCIAL_SECTOR_INDUSTRIES:
        result["not_meaningful_reason"] = _t("EBITDA-based exit values don't apply to banks and insurers.",
                                             "Exit-Werte auf EBITDA-Basis passen nicht zu Banken und Versicherern.")
        return VCMethodResult(**result)
    if exit_value <= 0:
        result.update(pre_money_valuation=0.0, no_value_reason=_t(
            f"Projected EBITDA in the exit year ({exit_year.year_label}) is not positive, so there is no exit value.",
            f"Das geplante EBITDA im Exit-Jahr ({exit_year.year_label}) ist nicht positiv, es gibt also keinen "
            "Exit-Wert."))
        return VCMethodResult(**result)
    if exit_equity <= 0:
        result.update(pre_money_valuation=0.0, no_value_reason=_t(
            "Debt is larger than the projected exit value.", "Die Schulden sind höher als der geplante Exit-Wert."))
        return VCMethodResult(**result)

    post = exit_equity / (1 + target_return) ** T
    result["post_money_valuation"] = post
    if post <= I:
        result.update(pre_money_valuation=0.0, no_value_reason=_t(
            "The capital being raised is larger than the value the exit supports today, "
            "so nothing is left for the existing shares.",
            "Das eingeworbene Kapital ist höher als der Wert, den der Exit heute trägt; für die bestehenden "
            "Anteile bleibt nichts übrig."))
        return VCMethodResult(**result)

    F = I / post
    y = x * F / (1 - F)
    result.update(
        pre_money_valuation=post - I,
        ownership_fraction_investors=F,
        ownership_fraction_entrepreneurs=1 - F,
        number_of_new_shares=y,
        price_per_share=I / y,
        final_wealth_investors=exit_equity * F,
        final_wealth_entrepreneurs=exit_equity * (1 - F),
    )
    return VCMethodResult(**result)


# ============================================================================
# SECTION 8 — Comparables method (EV/EBITDA or revenue multiple)
#
# Public-company value of the company's last 12 months: the higher of
#   EBITDA x Damodaran's trailing EV/EBITDA multiple, and
#   revenue x EV/Sales (or, for subscription companies, ARR x the SaaS ARR multiple),
# reduced by a private-company discount, then converted from enterprise value to
# equity value (minus debt, plus cash).
#
# Young companies are compared on revenue until their profit is the bigger value
# driver. Taking the higher of the two means a company never loses value by
# turning profitable (or by a bit more profit), and loss-making companies with
# revenue are valued by this method instead of being left out.
# ============================================================================


@dataclass
class ComparablesResult:
    trailing_ebitda: float
    ev_ebitda_multiple: float
    ev_ebitda_multiple_source: str
    public_company_ev: float  # the higher of ebitda_based_ev and revenue_based_ev
    private_company_discount: float
    enterprise_value: float
    debt: float
    cash: float
    equity_value: Optional[float]
    pre_money_valuation: Optional[float]
    debt_exceeds_value: bool
    not_meaningful_reason: Optional[str]
    ebitda_based_ev: float = 0.0  # max(EBITDA, 0) x EV/EBITDA
    revenue_basis: str = "revenue"  # "revenue" (last 12 months x EV/Sales) | "arr" (ARR x SaaS ARR multiple)
    revenue_amount: float = 0.0
    revenue_multiple: Optional[float] = None
    revenue_multiple_source: str = ""  # "region" | "industry_global" | "cross_industry" | "saas_index"
    revenue_based_ev: float = 0.0
    basis_used: str = "ebitda"  # "ebitda" | "revenue" | "arr"


def compute_comparables(
    company: CompanyProfile,
    operating: OperatingPerformance,
    debt: float,
    bench: Optional[_BenchmarkRecorder] = None,
) -> ComparablesResult:
    bench = bench or _BenchmarkRecorder(company.industry, company.business_territory_region)
    multiple = bench.get("ev_ebitda_multiple")
    source = bench.used["ev_ebitda_multiple"].source
    discount = get_stage_params(company.company_stage)["private_company_discount"]
    ltm = operating.current_ebitda
    cash = operating.cash_available

    arr = operating.annual_recurring_revenue or 0.0
    if arr > 0:
        basis, amount = "arr", arr
        rev_multiple, rev_source = saas_arr_multiple()["value"], "saas_index"
    else:
        basis, amount = "revenue", operating.current_revenue_last_12_months
        rev_multiple, rev_source = get_industry_metric_with_source(
            company.industry, "ev_sales_multiple", company.business_territory_region)

    ebitda_ev = max(ltm, 0.0) * multiple
    revenue_ev = amount * rev_multiple
    basis_used = "ebitda" if ebitda_ev > 0 and ebitda_ev >= revenue_ev else basis
    if basis_used == "revenue":  # its benchmark (and any fallback) is then part of the result
        bench.used["ev_sales_multiple"] = BenchmarkUsed("ev_sales_multiple", rev_multiple, rev_source)
    public_ev = max(ebitda_ev, revenue_ev)
    ev = public_ev * (1 - discount)
    equity = ev - debt + cash
    reason = None
    if company.industry in FINANCIAL_SECTOR_INDUSTRIES:
        reason = _t("EV/EBITDA and revenue multiples don't apply to banks and insurers.",
                    "EV/EBITDA- und Umsatz-Multiplikatoren passen nicht zu Banken und Versicherern.")
    elif public_ev <= 0:
        reason = _t("The company has no revenue and no positive EBITDA over the last 12 months to apply a multiple to.",
                    "Das Unternehmen hatte in den letzten 12 Monaten weder Umsatz noch ein positives EBITDA, auf das "
                    "sich ein Multiplikator anwenden ließe.")
    # Shareholders can't lose more than their shares: if debt exceeds the
    # enterprise value, this method says the equity is worth (about) nothing.
    floored = reason is None and equity < 0
    equity = max(equity, 0.0)

    return ComparablesResult(
        trailing_ebitda=ltm,
        ev_ebitda_multiple=multiple,
        ev_ebitda_multiple_source=source,
        public_company_ev=public_ev,
        private_company_discount=discount,
        enterprise_value=ev,
        debt=debt,
        cash=cash,
        equity_value=None if reason else equity,
        pre_money_valuation=None if reason else equity,
        debt_exceeds_value=floored,
        not_meaningful_reason=reason,
        ebitda_based_ev=ebitda_ev,
        revenue_basis=basis,
        revenue_amount=amount,
        revenue_multiple=rev_multiple,
        revenue_multiple_source=rev_source,
        revenue_based_ev=revenue_ev,
        basis_used=basis_used,
    )


# ============================================================================
# SECTION 8b — Round logic (a cross-check, not part of the blend)
#
# How early rounds are actually priced: investors buy a typical share of the
# company for the amount raised. Pre-money = raise x (1 - dilution) / dilution.
# ============================================================================

ROUND_NAMES_DE = {
    "Pre-seed (angels)": "Pre-Seed (Business Angels)",
    "Pre-seed": "Pre-Seed",
    "Seed": "Seed",
    "Series A": "Series A",
    "Series B": "Series B",
    "Series C and later": "Series C und später",
}


@dataclass
class RoundLogicResult:
    round_name: str
    dilution_low: float
    dilution_median: float
    dilution_high: float
    typical_round_low: float
    typical_round_high: float
    capital_needed: float
    implied_pre_money_low: float  # at the high dilution
    implied_pre_money_median: float
    implied_pre_money_high: float  # at the low dilution
    dilution_at_blend: Optional[float]  # the share the raise buys at the blended valuation
    raise_position: str  # "below" | "within" | "above" the typical round size


def implied_pre_money(capital_needed: float, dilution: float) -> float:
    """The pre-money at which `capital_needed` buys `dilution` of the company after the round."""
    return capital_needed * (1 - dilution) / dilution


def compute_round_logic(stage: str, capital_needed: float, blended: Optional[float]) -> Optional[RoundLogicResult]:
    row = round_benchmarks()["stages"].get(stage)
    if not row:
        return None
    low, high = row["round_eur_low"], row["round_eur_high"]
    return RoundLogicResult(
        round_name=_t(row["round"], ROUND_NAMES_DE.get(row["round"], row["round"])),
        dilution_low=row["dilution_low"],
        dilution_median=row["dilution_median"],
        dilution_high=row["dilution_high"],
        typical_round_low=low,
        typical_round_high=high,
        capital_needed=capital_needed,
        implied_pre_money_low=implied_pre_money(capital_needed, row["dilution_high"]),
        implied_pre_money_median=implied_pre_money(capital_needed, row["dilution_median"]),
        implied_pre_money_high=implied_pre_money(capital_needed, row["dilution_low"]),
        dilution_at_blend=capital_needed / (blended + capital_needed) if blended and blended > 0 else None,
        raise_position="below" if capital_needed < low else "above" if capital_needed > high else "within",
    )


# ============================================================================
# SECTION 9 — DCF method
#
# Five years of unlevered free cash flow + Gordon Growth terminal value,
# discounted at WACC (end-of-year convention). The going-concern value is
# then weighted by the stage's probability of survival (failure value
# assumed to be zero), and converted to equity (minus debt, plus cash).
# ============================================================================

# Minimum gap between the discount rate and the perpetual growth rate inside
# the terminal-value formula, so it stays well defined.
MIN_DISCOUNT_GROWTH_SPREAD = 0.02


@dataclass
class DCFResult:
    tax_rate: float
    discount_rate: float
    perpetual_growth_rate: float
    unlevered_fcf_by_year: list[float]
    terminal_fcf: float
    pv_of_fcf: float
    terminal_value: float
    pv_of_terminal_value: float
    enterprise_value: float
    terminal_value_share: Optional[float]
    terminal_value_floor_applied: bool
    survival_probability: float
    risk_adjusted_enterprise_value: float
    debt: float
    cash: float
    equity_value: Optional[float]
    pre_money_valuation: Optional[float]
    debt_exceeds_value: bool
    not_meaningful_reason: Optional[str]  # the method doesn't apply to this company (left out of the blend)
    # Applies, but the forecast cash flows are worth less than nothing: the business itself is
    # valued at €0 (its owners would rather close it), leaving only cash net of debt.
    no_value_reason: Optional[str] = None


def _npv(rate: float, cashflows: list[float]) -> float:
    """Discounts cashflows[0] as period 1, cashflows[1] as period 2, etc."""
    return sum(cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cashflows))


def compute_dcf(
    company: CompanyProfile,
    projections: FinancialProjections,
    wacc_result: WaccResult,
    operating: OperatingPerformance,
    debt: float,
) -> DCFResult:
    r = wacc_result.wacc
    g = market_parameters()["perpetual_growth_rate"]
    p = get_stage_params(company.company_stage)["survival_probability"]
    fcf = projections.fcf()
    n = len(fcf)

    tv_rate = max(r, g + MIN_DISCOUNT_GROWTH_SPREAD)
    floor_applied = tv_rate != r
    pv_fcf = _npv(r, fcf)
    terminal_fcf = projections.terminal_fcf if projections.terminal_fcf is not None else fcf[-1]
    tv = terminal_fcf * (1 + g) / (tv_rate - g)
    pv_tv = tv / (1 + r) ** n
    ev = pv_fcf + pv_tv
    # A business whose cash flows are worth less than nothing is worth €0, not a negative amount:
    # its owners can stop. So the value never jumps when the enterprise value crosses zero.
    risk_adj_ev = max(ev, 0.0) * p
    cash = operating.cash_available
    equity = risk_adj_ev - debt + cash

    reason = no_value = None
    if company.industry in FINANCIAL_SECTOR_INDUSTRIES:
        reason = _t("Free-cash-flow DCF doesn't apply to banks and insurers.",
                    "Ein DCF auf Basis freier Cashflows passt nicht zu Banken und Versicherern.")
    elif ev <= 0:
        no_value = _t("Projected free cash flows give a negative enterprise value, so the business is valued at €0 "
                      "and only cash net of debt is left.",
                      "Die geplanten freien Cashflows ergeben einen negativen Unternehmenswert; das Geschäft wird "
                      "daher mit 0 € bewertet, und es bleiben nur die liquiden Mittel abzüglich Schulden.")
    floored = reason is None and equity < 0
    equity = max(equity, 0.0)

    return DCFResult(
        tax_rate=projections.tax_rate,
        discount_rate=r,
        perpetual_growth_rate=g,
        unlevered_fcf_by_year=fcf,
        terminal_fcf=terminal_fcf,
        pv_of_fcf=pv_fcf,
        terminal_value=tv,
        pv_of_terminal_value=pv_tv,
        enterprise_value=ev,
        terminal_value_share=pv_tv / ev if ev > 0 else None,
        terminal_value_floor_applied=floor_applied,
        survival_probability=p,
        risk_adjusted_enterprise_value=risk_adj_ev,
        debt=debt,
        cash=cash,
        equity_value=None if reason else equity,
        pre_money_valuation=None if reason else equity,
        debt_exceeds_value=floored,
        not_meaningful_reason=reason,
        no_value_reason=no_value,
    )


# ============================================================================
# SECTION 10 — Input sanity checks (warnings)
# ============================================================================


@dataclass
class ValuationWarning:
    code: str
    severity: str  # "warning" | "info"
    message: str


_REGION_SHORT = {
    "US": "US",
    EUROPE_REGION: "Europe",
    "Japan": "Japan",
    EMERGING_REGION: "Emerging Markets",
    "China": "China",
    "India": "India",
    "Global": "Global",
}
_REGION_SHORT_DE = {
    "US": "USA",
    EUROPE_REGION: "Europa",
    "Japan": "Japan",
    EMERGING_REGION: "Schwellenländer",
    "China": "China",
    "India": "Indien",
    "Global": "weltweit",
}


def region_label(region: str) -> str:
    return _t(_REGION_SHORT.get(region, region), _REGION_SHORT_DE.get(region, region))


# How each benchmark region is described in a sentence ("... compared with European figures").
_REGION_ADJECTIVE = {
    "US": "US",
    EUROPE_REGION: "European",
    "Japan": "Japanese",
    EMERGING_REGION: "emerging-market",
    "China": "Chinese",
    "India": "Indian",
    "Global": "global",
}
# German: "mit Branchenzahlen für Europa" etc.
_REGION_FOR_DE = {
    "US": "die USA",
    EUROPE_REGION: "Europa",
    "Japan": "Japan",
    EMERGING_REGION: "Schwellenländer",
    "China": "China",
    "India": "Indien",
    "Global": "die ganze Welt",
}

# Country names that take "the" in a sentence ("in the United States").
_COUNTRIES_WITH_THE = {
    "United States", "United Kingdom", "Netherlands", "Bahamas", "Philippines", "Maldives", "Cayman Islands",
    "Turks and Caicos Islands", "Solomon Islands", "Isle of Man", "Czech Republic", "Dominican Republic",
    "United Arab Emirates", "Congo (Democratic Republic)", "Congo (Republic)",
}
# German names of the countries German founders most often pick; others keep Damodaran's English name.
COUNTRY_NAMES_DE = {
    "Germany": "Deutschland", "Austria": "Österreich", "Switzerland": "Schweiz", "Netherlands": "den Niederlanden",
    "Belgium": "Belgien", "France": "Frankreich", "Italy": "Italien", "Spain": "Spanien", "Poland": "Polen",
    "Czech Republic": "Tschechien", "Denmark": "Dänemark", "Sweden": "Schweden", "Norway": "Norwegen",
    "Finland": "Finnland", "United Kingdom": "dem Vereinigten Königreich", "Ireland": "Irland",
    "United States": "den USA", "Luxembourg": "Luxemburg", "Liechtenstein": "Liechtenstein", "Portugal": "Portugal",
    "Hungary": "Ungarn", "Croatia": "Kroatien", "Slovenia": "Slowenien", "Slovakia": "der Slowakei",
    "Greece": "Griechenland", "Türkiye": "der Türkei", "Israel": "Israel", "Canada": "Kanada", "Japan": "Japan",
    "China": "China", "India": "Indien", "Estonia": "Estland", "Latvia": "Lettland", "Lithuania": "Litauen",
    "Romania": "Rumänien", "Bulgaria": "Bulgarien", "Ukraine": "der Ukraine",
    "Abu Dhabi (UAE)": "Abu Dhabi (VAE)", "Albania": "Albanien", "Andorra": "Andorra", "Angola": "Angola",
    "Argentina": "Argentinien", "Armenia": "Armenien", "Aruba": "Aruba", "Australia": "Australien",
    "Azerbaijan": "Aserbaidschan", "Bahamas": "den Bahamas", "Bahrain": "Bahrain", "Bangladesh": "Bangladesch",
    "Barbados": "Barbados", "Belarus": "Belarus", "Belize": "Belize", "Benin": "Benin", "Bermuda": "Bermuda",
    "Bolivia": "Bolivien", "Bosnia and Herzegovina": "Bosnien und Herzegowina", "Botswana": "Botswana",
    "Brazil": "Brasilien", "Burkina Faso": "Burkina Faso", "Cambodia": "Kambodscha", "Cameroon": "Kamerun",
    "Cape Verde": "Kap Verde", "Cayman Islands": "den Kaimaninseln", "Chile": "Chile", "Colombia": "Kolumbien",
    "Congo (Democratic Republic)": "der Demokratischen Republik Kongo", "Congo (Republic)": "der Republik Kongo",
    "Cook Islands": "den Cookinseln", "Costa Rica": "Costa Rica", "Côte d'Ivoire": "der Côte d'Ivoire",
    "Cuba": "Kuba", "Curacao": "Curaçao", "Cyprus": "Zypern", "Dominican Republic": "der Dominikanischen Republik",
    "Ecuador": "Ecuador", "Egypt": "Ägypten", "El Salvador": "El Salvador", "Eswatini": "Eswatini",
    "Ethiopia": "Äthiopien", "Fiji": "Fidschi", "Gabon": "Gabun", "Georgia": "Georgien", "Ghana": "Ghana",
    "Guatemala": "Guatemala", "Guernsey": "Guernsey", "Honduras": "Honduras", "Hong Kong": "Hongkong",
    "Iceland": "Island", "Indonesia": "Indonesien", "Iraq": "dem Irak", "Isle of Man": "der Isle of Man",
    "Jamaica": "Jamaika", "Jersey": "Jersey", "Jordan": "Jordanien", "Kazakhstan": "Kasachstan", "Kenya": "Kenia",
    "Kuwait": "Kuwait", "Kyrgyzstan": "Kirgisistan", "Laos": "Laos", "Lebanon": "dem Libanon", "Macao": "Macau",
    "Malaysia": "Malaysia", "Maldives": "den Malediven", "Mali": "Mali", "Malta": "Malta", "Mauritius": "Mauritius",
    "Mexico": "Mexiko", "Moldova": "Moldau", "Mongolia": "der Mongolei", "Montenegro": "Montenegro",
    "Montserrat": "Montserrat", "Morocco": "Marokko", "Mozambique": "Mosambik", "Namibia": "Namibia",
    "Nepal": "Nepal", "New Zealand": "Neuseeland", "Nicaragua": "Nicaragua", "Niger": "Niger",
    "Nigeria": "Nigeria", "North Macedonia": "Nordmazedonien", "Oman": "Oman", "Pakistan": "Pakistan",
    "Panama": "Panama", "Papua New Guinea": "Papua-Neuguinea", "Paraguay": "Paraguay", "Peru": "Peru",
    "Philippines": "den Philippinen", "Qatar": "Katar", "Ras Al Khaimah (UAE)": "Ras al-Chaima (VAE)",
    "Russia": "Russland", "Rwanda": "Ruanda", "Saudi Arabia": "Saudi-Arabien", "Senegal": "Senegal",
    "Serbia": "Serbien", "Sharjah (UAE)": "Schardscha (VAE)", "Singapore": "Singapur",
    "Solomon Islands": "den Salomonen", "South Africa": "Südafrika", "South Korea": "Südkorea",
    "Sri Lanka": "Sri Lanka", "St. Maarten": "Sint Maarten", "St. Vincent & the Grenadines":
    "St. Vincent und den Grenadinen", "Suriname": "Suriname", "Taiwan": "Taiwan", "Tajikistan": "Tadschikistan",
    "Tanzania": "Tansania", "Thailand": "Thailand", "Togo": "Togo", "Trinidad and Tobago": "Trinidad und Tobago",
    "Tunisia": "Tunesien", "Turks and Caicos Islands": "den Turks- und Caicosinseln", "Uganda": "Uganda",
    "United Arab Emirates": "den Vereinigten Arabischen Emiraten", "Uruguay": "Uruguay", "Uzbekistan": "Usbekistan",
    "Venezuela": "Venezuela", "Vietnam": "Vietnam", "Zambia": "Sambia",
}
# Names above that carry a case ending after "in"; on their own (a list or a label) they read like this.
_COUNTRY_NOMINATIVE_DE = {
    "den Niederlanden": "Niederlande", "dem Vereinigten Königreich": "Vereinigtes Königreich", "den USA": "USA",
    "der Slowakei": "Slowakei", "der Türkei": "Türkei", "der Ukraine": "Ukraine", "den Bahamas": "Bahamas",
    "den Kaimaninseln": "Kaimaninseln", "der Demokratischen Republik Kongo": "Kongo (Demokratische Republik)",
    "der Republik Kongo": "Kongo (Republik)", "den Cookinseln": "Cookinseln", "der Côte d'Ivoire": "Côte d'Ivoire",
    "der Dominikanischen Republik": "Dominikanische Republik", "dem Irak": "Irak", "der Isle of Man": "Isle of Man",
    "dem Libanon": "Libanon", "den Malediven": "Malediven", "der Mongolei": "Mongolei",
    "den Philippinen": "Philippinen", "den Salomonen": "Salomonen", "St. Vincent und den Grenadinen":
    "St. Vincent und die Grenadinen", "den Turks- und Caicosinseln": "Turks- und Caicosinseln",
    "den Vereinigten Arabischen Emiraten": "Vereinigte Arabische Emirate",
}


# Full names of the benchmark regions, as offered in the wizard.
REGION_NAMES_DE = {
    "US": "USA",
    EUROPE_REGION: "Europa (EU, Vereinigtes Königreich, Schweiz & Skandinavien)",
    "Japan": "Japan",
    EMERGING_REGION: "Schwellenländer (Asien, Lateinamerika, Osteuropa, Naher Osten und Afrika)",
    "China": "China",
    "India": "Indien",
    "Global": "Weltweit",
}


def ui_labels() -> dict:
    """What the wizard shows for each stage, region, country and questionnaire answer in the current language
    ({value: label}; the value is what is sent back). Values without a translation keep their English name."""
    data = _all_reference_data()
    return {
        "stages": {s: stage_label(s) for s in stage_parameters()},
        "regions": {r: _t(r, REGION_NAMES_DE.get(r, r)) for r in business_regions()},
        "countries": {c: country_label(c) for c in country_data()},
        # After "in": "in den Niederlanden".
        "countries_in_text": {c: _t(c, COUNTRY_NAMES_DE.get(c, c)) for c in country_data()},
        "scorecard_options": {k: {o: option_label(k, o) for o in opts}
                              for k, opts in data["scorecard_qualitative_lookup"].items()},
    }


def country_label(country: str) -> str:
    """The country's name on its own: 'Germany' / 'Deutschland'."""
    if _LANG.get() != "de" or country not in COUNTRY_NAMES_DE:
        return country
    name = COUNTRY_NAMES_DE[country]
    return _COUNTRY_NOMINATIVE_DE.get(name, name)


def country_in_text(country: str) -> str:
    """'in {country_in_text(c)}': 'the United States' / 'den USA'."""
    return _t(f"the {country}" if country in _COUNTRIES_WITH_THE else country, COUNTRY_NAMES_DE.get(country, country))


_METRIC_LABELS = {
    "ebitda_margin": ("EBITDA margin", "EBITDA-Marge"),
    "da_pct_revenue": ("D&A (% of revenue)", "Abschreibungen (% vom Umsatz)"),
    "acc_receivable_pct_revenue": ("accounts receivable (% of revenue)", "Forderungen (% vom Umsatz)"),
    "inventory_pct_revenue": ("inventory (% of revenue)", "Vorräte (% vom Umsatz)"),
    "acc_payable_pct_revenue": ("accounts payable (% of revenue)", "Verbindlichkeiten aus L+L (% vom Umsatz)"),
    "beta": ("beta", "Beta"),
    "cost_of_debt": ("cost of debt", "Fremdkapitalkosten"),
    "equity_pct_capital": ("equity share of capital", "Eigenkapitalanteil"),
    "debt_pct_capital": ("debt share of capital", "Fremdkapitalanteil"),
    "ev_ebitda_multiple": ("EV/EBITDA multiple", "EV/EBITDA-Multiplikator"),
    "ev_sales_multiple": ("EV/Sales multiple", "EV/Umsatz-Multiplikator"),
}


def metric_label(metric: str) -> str:
    en, de = _METRIC_LABELS.get(metric, (metric, metric))
    return _t(en, de)


def _pct(x: float) -> str:
    """16.7% / 16,7 %."""
    return _t(f"{x * 100:.1f}%", f"{_num(x * 100, 1)}\u00a0%")


def _pct0(x: float) -> str:
    """233% / 233 %."""
    return _t(f"{x * 100:.0f}%", f"{_num(x * 100)}\u00a0%")


def _eur(x: float) -> str:
    """-€100,000 / -100.000 € (never "-€0")."""
    sign = "-" if round(x) < 0 else ""
    return _t(f"{sign}€{abs(x):,.0f}", f"{sign}{_num(abs(x))}\u00a0€")


def _times(x: float, decimals: int = 1) -> str:
    """4.1× / 4,1×."""
    return f"{_num(x, decimals)}×"


PRE_REVENUE_STAGES = {"Idea stage", "Development stage"}
# Revenue (last 12 months) above which an Idea/Development stage choice is questioned.
STAGE_REVENUE_THRESHOLD = 100_000
# Fall from last-12-month revenue to Year-1 revenue above which the plan is questioned.
REVENUE_DROP_WARNING = 0.2
# VC pre-money below this share of the raise is explained (the raise nearly uses up the exit value).
VC_LOW_SHARE_OF_RAISE = 0.25
# Share of the company the new investors would own above which the round is questioned.
INVESTOR_STAKE_WARNING = 0.5
# Highest method value above this multiple of the lowest gets a note: the blend hides a wide disagreement.
METHODS_DISAGREE_RATIO = 3
# Upper end, in US dollars, of the questionnaire answers about size; open-ended answers have none.
_REVENUE_POTENTIAL_MAX_USD = {"< $20 Million": 20e6, "$20 to $50 Million": 50e6, "$50 to $100 Million": 100e6}
_MARKET_SIZE_MAX_USD = {"< $50 million": 50e6, "$50 to $100 million": 100e6}
# A Year-5 target margin more than this above the industry's is questioned (as for the starting margin).
TARGET_MARGIN_GAP = 0.10
# Blended value above this multiple of last-12-month revenue is questioned: startups are rarely valued higher.
IMPLAUSIBLE_REVENUE_MULTIPLE = 50
# A Year-1 cash release from working capital above this share of Year-1 EBITDA gets a note.
WC_RELEASE_SHARE = 0.20


def usd_per_eur() -> float:
    """The ECB rate already used to convert the Equidam benchmarks (see sources)."""
    us = scorecard_benchmarks()["US"]
    return us["usd"] / us["eur"]


def _method_driver(key: str) -> str:
    """What the lowest method's value depends on most, named in the 'methods disagree' note."""
    return {
        "scorecard": _t("your questionnaire answers and the regional benchmark",
                        "Ihren Antworten im Fragebogen und dem regionalen Vergleichswert"),
        "venture_capital": _t("the amount raised compared with the projected exit value (a smaller round, a later "
                              "exit or a stronger plan would raise it)",
                              "dem eingeworbenen Betrag im Vergleich zum geplanten Exit-Wert (eine kleinere Runde, "
                              "ein späterer Exit oder ein stärkerer Plan würden ihn erhöhen)"),
        "comparables": _t("the last 12 months' revenue and EBITDA", "Umsatz und EBITDA der letzten 12 Monate"),
        "dcf": _t("the five-year cash flows and the discount rate", "den Cashflows der fünf Jahre und dem Diskontsatz"),
    }[key]


def method_values_post_money(method_values: dict, capital_needed: float) -> Optional[float]:
    """Post-money value from the blended pre-money (same blend as run_valuation)."""
    blended = sum(mv.weighted_value for mv in method_values.values() if mv.weighted_value is not None)
    return blended + capital_needed if blended > 0 else None


def collect_warnings(inputs: ValuationInput, projections: FinancialProjections, dcf: DCFResult,
                     bench: _BenchmarkRecorder, scorecard: ScorecardResult,
                     method_values: dict) -> list[ValuationWarning]:
    cp = inputs.company_profile
    op = inputs.operating_performance
    fa = inputs.financial_assumptions
    out: list[ValuationWarning] = []

    def warn(code, msg, severity="warning"):
        out.append(ValuationWarning(code, severity, msg))

    ltm = op.current_revenue_last_12_months
    has_arr = bool(op.annual_recurring_revenue)
    revenues = fa.revenues()
    first = next(i for i, r in enumerate(revenues) if r > 0)
    if ltm > 0 and first == 0:
        jump = fa.revenue_year1 / ltm - 1
        if jump > 1.0:
            warn("revenue_jump", _t(
                f"Year-1 revenue ({_eur(fa.revenue_year1)}) is {_pct0(jump)} above the last 12 months ({_eur(ltm)}). "
                "Check that this growth is realistic; every cash-flow method builds on it.",
                f"Der Umsatz in Jahr 1 ({_eur(fa.revenue_year1)}) liegt {_pct0(jump)} über dem der letzten 12 Monate "
                f"({_eur(ltm)}). Prüfen Sie, ob dieses Wachstum realistisch ist; alle Cashflow-Methoden bauen darauf "
                "auf."))
        elif jump < -REVENUE_DROP_WARNING:
            warn("revenue_drop", _t(
                f"Year-1 revenue ({_eur(fa.revenue_year1)}) is {_pct0(-jump)} below the last 12 months ({_eur(ltm)}). "
                "If you don't expect sales to fall, check both figures: every cash-flow method builds on the Year-1 "
                "plan.",
                f"Der Umsatz in Jahr 1 ({_eur(fa.revenue_year1)}) liegt {_pct0(-jump)} unter dem der letzten 12 Monate "
                f"({_eur(ltm)}). Wenn Sie keinen Umsatzrückgang erwarten, prüfen Sie beide Zahlen: alle "
                "Cashflow-Methoden bauen auf dem Plan für Jahr 1 auf."))
    elif ltm > 0:
        warn("revenue_pause", _t(
            f"Your plan has no revenue until Year {first + 1}, although the company had {_eur(ltm)} in the last "
            "12 months. Until sales start again, the projection keeps today's costs; check that this is your plan.",
            f"Ihre Planung sieht bis Jahr {first + 1} keinen Umsatz vor, obwohl das Unternehmen in den letzten 12 "
            f"Monaten {_eur(ltm)} umgesetzt hat. Bis der Umsatz wieder einsetzt, rechnet die Planung mit den "
            "heutigen Kosten; prüfen Sie, ob das Ihr Plan ist."))
    else:
        margin_used = (_t("your own Year-5 target margin", "Ihre eigene Zielmarge für Jahr 5")
                       if fa.target_ebitda_margin_override is not None
                       else _t("the industry average margin", "die durchschnittliche Branchenmarge"))
        before_sales = "" if first == 0 else _t(
            f" Until sales start in Year {first + 1}, EBITDA stays at today's {_eur(min(op.current_ebitda, 0.0))} a "
            "year.",
            f" Bis zum Umsatzbeginn in Jahr {first + 1} bleibt das EBITDA bei den heutigen "
            f"{_eur(min(op.current_ebitda, 0.0))} pro Jahr.")
        warn("no_revenue_history", _t(
            f"No revenue in the last 12 months: the projection starts from your current EBITDA "
            f"({_eur(op.current_ebitda)}) measured against Year-{first + 1} revenue, a "
            f"{_pct(projections.starting_ebitda_margin)} margin, and moves to {margin_used} by Year 5."
            + before_sales + ("" if has_arr else " The Comparables method can't be applied."),
            f"Kein Umsatz in den letzten 12 Monaten: Die Planung beginnt mit Ihrem aktuellen EBITDA "
            f"({_eur(op.current_ebitda)}) im Verhältnis zum Umsatz in Jahr {first + 1}, also einer Marge von "
            f"{_pct(projections.starting_ebitda_margin)}, und erreicht bis Jahr 5 {margin_used}."
            + before_sales + ("" if has_arr else " Die Vergleichsmethode kann nicht angewendet werden.")), "info")
    # Growth above 100% a year is normal for a company that is only starting to sell.
    growth = [b / a - 1 for a, b in zip(revenues, revenues[1:]) if a > 0]
    if ltm > 0 and any(g > 1.0 for g in growth):
        warn("high_growth", _t("One or more yearly growth rates is above 100%. Check these are realistic.",
                               "Mindestens eine jährliche Wachstumsrate liegt über 100 %. Prüfen Sie, ob das "
                               "realistisch ist."))

    revenue_base = ltm if ltm > 0 else revenues[first]
    if op.current_ppe_value > 2 * revenue_base:
        ratio = _times(op.current_ppe_value / revenue_base)
        warn("ppe_scale", _t(
            f"PP&E ({_eur(op.current_ppe_value)}) is {ratio} revenue, which is unusual for most young companies. "
            "Check the figure.",
            f"Das Sachanlagevermögen ({_eur(op.current_ppe_value)}) beträgt das {ratio} des Umsatzes; das ist für "
            "junge Unternehmen ungewöhnlich. Prüfen Sie die Zahl."))
    y1 = projections.years[0]
    if y1.change_in_working_capital < 0 and -y1.change_in_working_capital > WC_RELEASE_SHARE * abs(y1.ebitda):
        released = _eur(-y1.change_in_working_capital)
        warn("wc_release", _t(
            f"In Year 1, working capital releases {released} of cash: in this industry, suppliers' credit "
            "(accounts payable) is larger than receivables and inventory, so growing revenue from "
            f"{_eur(ltm)} to {_eur(y1.revenue)} frees cash. This raises the DCF value; check that your suppliers "
            "will really give you this much credit.",
            f"In Jahr 1 setzt das Working Capital {released} an liquiden Mitteln frei: In dieser Branche sind die "
            "Lieferantenkredite (Verbindlichkeiten) größer als Forderungen und Vorräte, deshalb macht das "
            f"Umsatzwachstum von {_eur(ltm)} auf {_eur(y1.revenue)} Geld frei. Das erhöht den DCF-Wert; prüfen Sie, "
            "ob Ihre Lieferanten Ihnen wirklich so viel Zahlungsziel geben."), "info")
    for y in projections.years:
        if y.revenue > 0 and y.capex > 0.3 * y.revenue:
            warn("capex_scale", _t(f"Capex in {y.year_label} ({_eur(y.capex)}) is more than 30% of revenue.",
                                   f"Die Investitionen in {y.year_label} ({_eur(y.capex)}) betragen mehr als 30 % "
                                   "des Umsatzes."))
            break

    if projections.starting_margin_source == "company":
        gap = projections.starting_ebitda_margin - projections.target_ebitda_margin
        if abs(gap) > 0.10:
            own_target = projections.target_margin_source == "user_override"
            warn("margin_gap", _t(
                f"Your current EBITDA margin ({_pct(projections.starting_ebitda_margin)}) differs from the "
                f"{'target' if own_target else 'industry'} margin ({_pct(projections.target_ebitda_margin)}) by more "
                "than 10 points. The projection moves from one to the other over five years; "
                + ("adjust your target margin if that isn't realistic." if own_target
                   else "enter your own target margin if that isn't realistic."),
                f"Ihre aktuelle EBITDA-Marge ({_pct(projections.starting_ebitda_margin)}) weicht um mehr als 10 "
                f"Prozentpunkte von der {'Ziel' if own_target else 'Branchen'}marge "
                f"({_pct(projections.target_ebitda_margin)}) ab. Die Planung geht in fünf Jahren von der einen zur "
                "anderen; "
                + ("passen Sie Ihre Zielmarge an, wenn das nicht realistisch ist." if own_target
                   else "geben Sie eine eigene Zielmarge ein, wenn das nicht realistisch ist.")))

    # The plan against the founder's own answers on market size and revenue potential.
    year5 = projections.years[-1].revenue
    mk = inputs.market_and_team_assessment
    market_max = _MARKET_SIZE_MAX_USD.get(mk.target_market_size)
    potential_max = _REVENUE_POTENTIAL_MAX_USD.get(mk.revenue_potential_in_5_years)
    if market_max and year5 > market_max / usd_per_eur():
        about = _eur(round(market_max / usd_per_eur(), -4))
        warn("plan_above_market", _t(
            f"Year-5 revenue in your plan ({_eur(year5)}) is more than the whole market you described "
            f"(\"{mk.target_market_size}\", about {about}). Check the plan or the market size answer.",
            f"Der Umsatz in Jahr 5 Ihres Plans ({_eur(year5)}) ist größer als der gesamte Markt, den Sie angegeben "
            f"haben (\"{mk.target_market_size}\", etwa {about}). Prüfen Sie den Plan oder die Angabe zur "
            "Marktgröße."))
    elif potential_max and year5 > potential_max / usd_per_eur():
        about = _eur(round(potential_max / usd_per_eur(), -4))
        warn("plan_above_revenue_potential", _t(
            f"Year-5 revenue in your plan ({_eur(year5)}) is above the revenue potential you gave "
            f"(\"{mk.revenue_potential_in_5_years}\", about {about}). Check the plan or that answer: the cash-flow "
            "methods use the plan.",
            f"Der Umsatz in Jahr 5 Ihres Plans ({_eur(year5)}) liegt über dem angegebenen Umsatzpotenzial "
            f"(\"{mk.revenue_potential_in_5_years}\", etwa {about}). Prüfen Sie den Plan oder diese Antwort: die "
            "Cashflow-Methoden verwenden den Plan."))
    industry_margin = get_industry_metric(cp.industry, "ebitda_margin", cp.business_territory_region)
    if (projections.target_margin_source == "user_override"
            and projections.target_ebitda_margin > industry_margin + TARGET_MARGIN_GAP):
        warn("target_margin_high", _t(
            f"Your Year-5 EBITDA margin ({_pct(projections.target_ebitda_margin)}) is more than 10 points above the "
            f"industry's ({_pct(industry_margin)}). Check that it is realistic: the cash-flow methods depend on it.",
            f"Ihre EBITDA-Marge für Jahr 5 ({_pct(projections.target_ebitda_margin)}) liegt mehr als 10 Prozentpunkte "
            f"über der Branche ({_pct(industry_margin)}). Prüfen Sie, ob das realistisch ist: die Cashflow-Methoden "
            "hängen davon ab."))

    if inputs.ownership:
        total = sum(s.ownership_pct for s in inputs.ownership)
        if abs(total - 1) > 0.005:
            warn("ownership_sum", _t(f"Ownership adds up to {_pct(total)}, not 100%.",
                                     f"Die Beteiligungen ergeben zusammen {_pct(total)}, nicht 100 %."))

    uof = inputs.funding.use_of_funds
    if uof:
        total_uof = sum(uof.values())
        cap = inputs.funding.capital_needed
        if abs(total_uof - cap) > 0.005 * cap:
            warn("use_of_funds_sum", _t(
                f"Use of funds adds up to {_eur(total_uof)}, but the capital needed is {_eur(cap)}.",
                f"Die Mittelverwendung ergibt {_eur(total_uof)}, der Kapitalbedarf beträgt aber {_eur(cap)}."))
        capex_uof = uof.get("Capital expenditures", 0) or 0
        if capex_uof > 0 and abs(capex_uof - fa.capex_by_year[0]) > 0.5:
            warn("capex_mismatch", _t(
                f"Use of funds includes {_eur(capex_uof)} of capital expenditure, but planned capex for Year 1 is "
                f"{_eur(fa.capex_by_year[0])}.",
                f"Die Mittelverwendung enthält {_eur(capex_uof)} für Investitionen, die geplanten Investitionen in "
                f"Jahr 1 betragen aber {_eur(fa.capex_by_year[0])}."))

    try:
        expected_region = default_region_for_country(cp.country)
    except KeyError:
        expected_region = None
    if expected_region and expected_region != "Global" and expected_region != cp.business_territory_region:
        warn("region_mismatch", _t(
            f"Companies in {country_in_text(cp.country)} are usually compared with "
            f"{_REGION_ADJECTIVE.get(expected_region, expected_region)} industry figures; you chose "
            f"{_REGION_ADJECTIVE.get(cp.business_territory_region, cp.business_territory_region)} figures, "
            "so the benchmarks follow your choice.",
            f"Unternehmen in {country_in_text(cp.country)} werden meist mit Branchenzahlen für "
            f"{_REGION_FOR_DE.get(expected_region, expected_region)} verglichen; Sie haben Zahlen für "
            f"{_REGION_FOR_DE.get(cp.business_territory_region, cp.business_territory_region)} gewählt, "
            "die Vergleichswerte folgen Ihrer Wahl."), "info")

    region_name = region_label(cp.business_territory_region)
    # Banks and insurers are valued by the Scorecard only, so industry-figure fallbacks don't matter.
    for b in ([] if cp.industry in FINANCIAL_SECTOR_INDUSTRIES else bench.used.values()):
        if b.source == "industry_global":
            warn(f"fallback_{b.metric}", _t(
                f"Damodaran has no usable {region_name} figure for {metric_label(b.metric)} in this industry, so "
                "the industry's global figure is used.",
                f"Damodaran hat für diese Branche keinen verwendbaren Wert für {metric_label(b.metric)} in der Region "
                f"{region_name}; deshalb wird der weltweite Branchenwert verwendet."), "info")
        elif b.source == "cross_industry":
            warn(f"fallback_{b.metric}", _t(
                f"No figure for {metric_label(b.metric)} in this industry: the median across all industries is used.",
                f"Kein Wert für {metric_label(b.metric)} in dieser Branche: der Median aller Branchen wird "
                "verwendet."), "info")

    if cp.industry in FINANCIAL_SECTOR_INDUSTRIES:
        warn("financial_sector", _t(
            "Banks and insurers can't be valued on EBITDA, revenue multiples or free cash flow, so only the "
            "Scorecard method is used.",
            "Banken und Versicherer lassen sich nicht über EBITDA, Umsatz-Multiplikatoren oder freie Cashflows "
            "bewerten; deshalb wird nur die Scorecard-Methode verwendet."))
    if dcf.terminal_value_floor_applied:
        warn("tv_floor", _t(
            "The discount rate is close to the long-run growth rate, so the terminal value was calculated with a "
            f"minimum {_pct(MIN_DISCOUNT_GROWTH_SPREAD)} gap between them.",
            "Der Diskontsatz liegt nahe an der langfristigen Wachstumsrate; der Endwert wurde deshalb mit einem "
            f"Mindestabstand von {_pct(MIN_DISCOUNT_GROWTH_SPREAD)} berechnet."))
    if dcf.terminal_value_share is not None and dcf.terminal_value_share > 1:
        warn("tv_share", _t(
            "The forecast years burn cash in total, so all of the DCF value comes from the years after Year 5 "
            "(the terminal value).",
            "Die Planjahre verbrauchen insgesamt Geld; der gesamte DCF-Wert stammt deshalb aus den Jahren nach "
            "Jahr 5 (dem Endwert)."), "info")
    elif dcf.terminal_value_share is not None and dcf.terminal_value_share > 0.75:
        warn("tv_share", _t(
            f"The terminal value is {_pct(dcf.terminal_value_share)} of the DCF value, so the DCF depends mostly on "
            "years after the forecast.",
            f"Der Endwert macht {_pct(dcf.terminal_value_share)} des DCF-Werts aus; der DCF hängt also vor allem von "
            "den Jahren nach dem Planungszeitraum ab."), "info")

    if (method_values["scorecard"].status == "ok" and scorecard.benchmark_source == "table"
            and cp.business_territory_region != "Global"
            and not scorecard_benchmarks()[cp.business_territory_region]["regional_figure"]):
        warn("benchmark_global", _t(
            f"There is no separate published pre-revenue benchmark for {region_name}, so the Scorecard uses the "
            "all-region median. Enter a local benchmark if you have one.",
            f"Für {region_name} gibt es keinen eigenen veröffentlichten Vergleichswert vor Umsatzbeginn; die "
            "Scorecard verwendet deshalb den Median aller Regionen. Geben Sie einen lokalen Vergleichswert ein, "
            "wenn Sie einen haben."), "info")
    if method_values["scorecard"].status == "ok" and scorecard.unanswered:
        n = len(scorecard.unanswered)
        total_q = sum(len(q) for q in SCORECARD_CRITERIA_QUESTIONS.values())
        warn("scorecard_unanswered", _t(
            f"{n} of the {total_q} Scorecard questions {'were' if n > 1 else 'was'} not answered and "
            f"{'are' if n > 1 else 'is'} scored as typical (100%). Answer them for a Scorecard value that reflects "
            "your company.",
            f"{n} der {total_q} Scorecard-Fragen {'wurden' if n > 1 else 'wurde'} nicht beantwortet und "
            f"{'werden' if n > 1 else 'wird'} als durchschnittlich (100 %) gewertet. Beantworten Sie sie für einen "
            "Scorecard-Wert, der Ihr Unternehmen widerspiegelt."))

    # Stage and revenue should tell the same story: the stage decides the method weights.
    stage_name = stage_label(cp.company_stage)
    if cp.company_stage in PRE_REVENUE_STAGES and ltm >= STAGE_REVENUE_THRESHOLD:
        sc_weight = _pct(method_values["scorecard"].weight)
        warn("stage_revenue", _t(
            f"You chose the {stage_name.replace(' stage', '')} stage, which is for companies without meaningful "
            f"revenue, but entered {_eur(ltm)} of revenue in the last 12 months. At this stage the Scorecard "
            f"(a comparison with typical pre-revenue startups) counts for {sc_weight} of the value. If you already "
            "sell, the Startup stage probably fits better.",
            f"Sie haben die {stage_name} gewählt, die für Unternehmen ohne nennenswerten Umsatz gedacht ist, aber "
            f"{_eur(ltm)} Umsatz in den letzten 12 Monaten angegeben. In dieser Phase zählt die Scorecard (ein "
            f"Vergleich mit typischen Startups vor Umsatzbeginn) mit {sc_weight}. Wenn Sie schon verkaufen, passt "
            "die Startphase wahrscheinlich besser."))
    elif cp.company_stage not in PRE_REVENUE_STAGES and ltm == 0:
        warn("stage_no_revenue", _t(
            f"You chose the {stage_name.replace(' stage', '')} stage, which assumes the company already has customers "
            "and revenue, but entered no revenue for the last 12 months. The Scorecard, which is built for "
            "pre-revenue companies, is therefore not used. If you don't sell yet, the Idea or Development stage "
            "probably fits better.",
            f"Sie haben die {stage_name} gewählt, die bereits Kunden und Umsatz voraussetzt, aber keinen Umsatz für "
            "die letzten 12 Monate angegeben. Die Scorecard, die für Unternehmen vor Umsatzbeginn gedacht ist, wird "
            "deshalb nicht verwendet. Wenn Sie noch nicht verkaufen, passt die Ideen- oder Entwicklungsphase "
            "wahrscheinlich besser."))

    # A round that hands investors most of the company is unusual and worth a second look.
    post = method_values_post_money(method_values, inputs.funding.capital_needed)
    if post:
        stake = inputs.funding.capital_needed / post
        if stake > INVESTOR_STAKE_WARNING:
            warn("investor_stake", _t(
                f"At this valuation, the {_eur(inputs.funding.capital_needed)} you are raising would buy "
                f"{_pct(stake)} of the company (post-money {_eur(post)}). Early rounds usually sell well under half "
                "of a company, so check the amount you are raising and your plan.",
                f"Bei dieser Bewertung würden die {_eur(inputs.funding.capital_needed)}, die Sie einwerben, "
                f"{_pct(stake)} des Unternehmens kaufen (Post-Money {_eur(post)}). In frühen Runden wird meist "
                "deutlich weniger als die Hälfte verkauft; prüfen Sie den Betrag und Ihren Plan."))

    blend = sum(mv.weighted_value for mv in method_values.values() if mv.weighted_value is not None)
    if ltm > 0 and blend > IMPLAUSIBLE_REVENUE_MULTIPLE * ltm:
        warn("implausible_value", _t(
            f"The blended value ({_eur(blend)}) is {_times(blend / ltm, 0)} the last 12 months' revenue. Startups are "
            f"rarely valued above about {IMPLAUSIBLE_REVENUE_MULTIPLE}× revenue, so check the growth plan, the "
            "target margin and the exit year before relying on this number.",
            f"Der gewichtete Wert ({_eur(blend)}) beträgt das {_times(blend / ltm, 0)} des Umsatzes der letzten 12 "
            f"Monate. Startups werden selten mit mehr als etwa dem {IMPLAUSIBLE_REVENUE_MULTIPLE}fachen des Umsatzes "
            "bewertet; prüfen Sie Wachstumsplan, Zielmarge und Exit-Jahr, bevor Sie sich auf diese Zahl verlassen."))

    rng = method_range(method_values)
    # A method at €0 already has its own note saying why; no second "methods disagree" note for it.
    disagree = bool(rng and rng.high > METHODS_DISAGREE_RATIO * rng.low and not method_values[rng.low_method].note)
    if disagree:
        apart = (_t(f"{_times(rng.high / rng.low, 0)} apart", f"Faktor {_num(rng.high / rng.low)}")
                 if rng.low > 0 else _t("far apart", "weit auseinander"))
        warn("methods_disagree", _t(
            f"The methods disagree: {method_name(rng.high_method)} gives {_eur(rng.high)}, "
            f"{method_name(rng.low_method)} only {_eur(rng.low)} ({apart}). The blended value sits between them, so "
            f"quote the range, not only the blend. The low value depends most on {_method_driver(rng.low_method)}; "
            "check those inputs first.",
            f"Die Methoden weichen stark voneinander ab: {method_name(rng.high_method)} ergibt {_eur(rng.high)}, "
            f"{method_name(rng.low_method)} nur {_eur(rng.low)} ({apart}). Der gewichtete Wert liegt dazwischen; "
            f"nennen Sie deshalb die Spanne, nicht nur den Mittelwert. Der niedrige Wert hängt vor allem von "
            f"{_method_driver(rng.low_method)} ab; prüfen Sie zuerst diese Angaben."))

    vc_mv = method_values["venture_capital"]
    raise_amount = inputs.funding.capital_needed
    # When the VC method is also the low end of a wide range, the note above already explains it.
    if (vc_mv.status == "ok" and vc_mv.weight_used > 0 and not vc_mv.note
            and vc_mv.pre_money_value < VC_LOW_SHARE_OF_RAISE * raise_amount
            and not (disagree and rng.low_method == "venture_capital")):
        warn("vc_low", _t(
            f"The Venture Capital method values the company at only {_eur(vc_mv.pre_money_value)} before the round: "
            f"the {_eur(raise_amount)} you are raising nearly uses up the value the projected exit supports today at "
            f"the return investors at your stage expect. It counts for {_pct(vc_mv.weight_used)} of the blend and "
            "pulls it down; a smaller round, a later exit or a stronger plan would raise it.",
            f"Die Venture-Capital-Methode bewertet das Unternehmen vor der Runde mit nur "
            f"{_eur(vc_mv.pre_money_value)}: Die {_eur(raise_amount)}, die Sie einwerben, verbrauchen fast den "
            "ganzen Wert, den der geplante Exit bei der Rendite trägt, die Investoren in Ihrer Phase erwarten. Sie "
            f"zählt mit {_pct(vc_mv.weight_used)} und zieht den Wert nach unten; eine kleinere Runde, ein späterer "
            "Exit oder ein stärkerer Plan würden ihn erhöhen."))

    vdate = valuation_date_of(cp)
    today = date.today()
    if abs((vdate - today).days) > 366:
        warn("valuation_date", _t(
            f"The valuation date ({_long_date(vdate)}) is more than a year {'before' if vdate < today else 'after'} "
            "today, but the market data, tax rates and benchmarks are current figures. Check the date.",
            f"Das Bewertungsdatum ({_long_date(vdate)}) liegt mehr als ein Jahr "
            f"{'vor' if vdate < today else 'nach'} heute, Marktdaten, Steuersätze und Vergleichswerte sind aber "
            "aktuelle Zahlen. Prüfen Sie das Datum."))
    if cp.year_of_incorporation and cp.year_of_incorporation > vdate.year:
        warn("incorporation_year", _t(
            f"The year of incorporation ({cp.year_of_incorporation}) is after the valuation date ({vdate.year}). "
            "Check both.",
            f"Das Gründungsjahr ({cp.year_of_incorporation}) liegt nach dem Bewertungsdatum ({vdate.year}). Prüfen "
            "Sie beides."))

    if method_values["scorecard"].status == "ok" and scorecard.benchmark_to_be_sourced:
        warn("benchmark_to_be_sourced", _t(
            f"The {cp.country}-specific pre-revenue benchmark is still to be sourced, so the Scorecard uses the "
            f"Europe figure ({_eur(scorecard.benchmark_pre_money_valuation)}) as a placeholder. Enter a local "
            "benchmark if you have one.",
            f"Der Vergleichswert vor Umsatzbeginn für {country_in_text(cp.country)} muss noch belegt werden; die "
            f"Scorecard verwendet bis dahin den Wert für Europa ({_eur(scorecard.benchmark_pre_money_valuation)}). "
            "Geben Sie einen lokalen Vergleichswert ein, wenn Sie einen haben."), "info")

    taxes = projections.tax_schedule
    pays_tax = any(y.tax_on_ebit > 0 for y in projections.years)  # no note if losses mean no tax is due
    if taxes is not None and taxes.basis == "germany_schedule" and pays_tax:
        rates = ", ".join(f"{y.year_label} {_pct(y.tax_rate)}" for y in projections.years)
        h = _pct0(taxes.hebesatz)
        hebesatz = (_t(f"your Hebesatz of {h}", f"Ihren Hebesatz von {h}") if taxes.hebesatz_source == "user"
                    else _t(f"the national average Hebesatz of {h} (enter your municipality's for a more precise "
                            "figure)",
                            f"den bundesweiten durchschnittlichen Hebesatz von {h} (geben Sie den Ihrer Gemeinde ein, "
                            "um genauer zu rechnen)"))
        kst = taxes.calendar_years
        warn("german_tax_schedule", _t(
            f"German corporate tax falls from {_pct0(kst[0]['corporate_tax'])} to {_pct0(kst[-1]['corporate_tax'])} "
            f"by {kst[-1]['year']}, so each projection year uses its own combined rate including solidarity surcharge "
            f"and trade tax ({rates}; {_pct(taxes.long_run_rate)} after Year 5). Trade tax uses {hebesatz}.",
            f"Die Körperschaftsteuer sinkt von {_pct0(kst[0]['corporate_tax'])} auf {_pct0(kst[-1]['corporate_tax'])} "
            f"bis {kst[-1]['year']}; jedes Planjahr verwendet deshalb seinen eigenen Gesamtsatz einschließlich "
            f"Solidaritätszuschlag und Gewerbesteuer ({rates}; {_pct(taxes.long_run_rate)} nach Jahr 5). Die "
            f"Gewerbesteuer verwendet {hebesatz}."), "info")

    for key, mv in method_values.items():
        if mv.status == "not_meaningful" and mv.weight > 0:
            # Without revenue, Comparables can't apply by design, and the "no_revenue_history"
            # note already says so: no second message.
            if key == "comparables" and ltm == 0:
                continue
            # Banks and insurers: the single "financial_sector" message already explains all three.
            if cp.industry in FINANCIAL_SECTOR_INDUSTRIES:
                continue
            warn(f"nm_{key}", _t(f"{method_name(key)} left out of the blend: {mv.note}",
                                 f"{method_name(key)} nicht in der Gewichtung: {mv.note}"))
        elif mv.status == "ok" and mv.note:
            warn(f"zero_{key}", f"{method_name(key)}: {mv.note}")
    debt = inputs.financial_assumptions.existing_debt_balance
    if debt > 0 and method_values["scorecard"].status == "ok":
        warn("scorecard_debt", _t(
            "The Scorecard compares you with a typical (usually debt-free) company and does not subtract your "
            f"{_eur(debt)} of debt; the other methods do.",
            "Die Scorecard vergleicht Sie mit einem typischen (meist schuldenfreien) Unternehmen und zieht Ihre "
            f"{_eur(debt)} Schulden nicht ab; die anderen Methoden tun das."), "info")

    return out


# ============================================================================
# SECTION 11 — Orchestration and blend
# ============================================================================

METHOD_NAMES = {
    "scorecard": "Scorecard method",
    "venture_capital": "Venture Capital method",
    "comparables": "Comparables (revenue or EBITDA multiple)",
    "dcf": "DCF method",
}
METHOD_NAMES_DE = {
    "scorecard": "Scorecard-Methode",
    "venture_capital": "Venture-Capital-Methode",
    "comparables": "Vergleichsmethode (Umsatz- oder EBITDA-Multiplikator)",
    "dcf": "DCF-Methode",
}


def method_name(key: str) -> str:
    return _t(METHOD_NAMES[key], METHOD_NAMES_DE[key])


class ValuationError(ValueError):
    """Inputs are valid but no method can produce a meaningful value."""


@dataclass
class MethodValue:
    pre_money_value: Optional[float]
    weight: float  # the stage's standard weight
    weight_used: float  # after leaving out methods that aren't meaningful
    weighted_value: Optional[float]
    status: str  # "ok" | "not_meaningful" | "not_used" (stage weight 0)
    note: Optional[str]


@dataclass
class MethodRange:
    """Lowest and highest value among the methods in the blend."""
    low: float
    low_method: str
    high: float
    high_method: str


def method_range(method_values: dict) -> Optional[MethodRange]:
    """None when fewer than two methods count in the blend."""
    used = {k: mv.pre_money_value for k, mv in method_values.items() if mv.status == "ok" and mv.weight_used > 0}
    if len(used) < 2:
        return None
    low, high = min(used, key=used.get), max(used, key=used.get)
    return MethodRange(low=used[low], low_method=low, high=used[high], high_method=high)


@dataclass
class EquityBridge:
    debt: float
    cash: float
    net_debt: float


@dataclass
class ValuationOutput:
    valuation_date: str
    projections: FinancialProjections
    wacc: WaccResult
    scorecard: ScorecardResult
    venture_capital: VCMethodResult
    comparables: ComparablesResult
    dcf: DCFResult
    equity_bridge: EquityBridge
    benchmarks_used: dict[str, BenchmarkUsed]
    method_values: dict[str, MethodValue]
    blended_pre_money_valuation: float
    capital_needed: float
    post_money_valuation: float
    warnings: list[ValuationWarning]
    method_range: Optional[MethodRange] = None
    round_logic: Optional[RoundLogicResult] = None  # cross-check: how a typical round would price the company


def run_valuation(inputs: ValuationInput) -> ValuationOutput:
    company = inputs.company_profile
    op = inputs.operating_performance
    get_country(company.country)  # fail early on an unknown country
    stage = get_stage_params(company.company_stage)
    if company.industry not in industry_benchmarks():
        raise KeyError(f"Unknown industry {company.industry!r}")
    if company.business_territory_region not in business_regions():
        raise KeyError(f"Unknown region {company.business_territory_region!r}")

    bench = _BenchmarkRecorder(company.industry, company.business_territory_region)
    projections = build_projections(company, inputs.financial_assumptions, op, bench)
    wacc_result = compute_wacc(company, bench)
    debt = inputs.financial_assumptions.existing_debt_balance

    scorecard_result = compute_scorecard(company, inputs.market_and_team_assessment)
    vc_result = compute_venture_capital(company, projections, inputs.funding, inputs.vc_assumptions, bench, debt)
    comparables_result = compute_comparables(company, op, debt, bench)
    dcf_result = compute_dcf(company, projections, wacc_result, op, debt)

    raw = {
        "scorecard": (scorecard_result.pre_money_valuation, None),
        "venture_capital": (vc_result.pre_money_valuation, vc_result.not_meaningful_reason),
        "comparables": (comparables_result.pre_money_valuation, comparables_result.not_meaningful_reason),
        "dcf": (dcf_result.pre_money_valuation, dcf_result.not_meaningful_reason),
    }
    weights = stage["method_weights"]
    usable_weight = sum(weights[k] for k, (v, _) in raw.items() if v is not None and weights[k] > 0)
    if usable_weight <= 0 and company.industry in FINANCIAL_SECTOR_INDUSTRIES:
        raise ValuationError(_t(
            "Banks and insurers can't be valued on EBITDA, revenue multiples or free cash flow, and the Scorecard "
            "applies only to pre-revenue companies (Idea and Development stages), so this tool can't value this "
            "company.",
            "Banken und Versicherer lassen sich nicht über EBITDA, Umsatz-Multiplikatoren oder freie Cashflows "
            "bewerten, und die Scorecard gilt nur vor Umsatzbeginn (Ideen- und Entwicklungsphase); dieses Tool kann "
            "das Unternehmen deshalb nicht bewerten."))

    def advice() -> str:
        if op.current_revenue_last_12_months == 0 and company.company_stage not in PRE_REVENUE_STAGES:
            return _t("Your company has no revenue yet: choose the Idea or Development stage, where the Scorecard "
                      "(which doesn't need revenue or profits) is used.",
                      "Ihr Unternehmen hat noch keinen Umsatz: Wählen Sie die Ideen- oder Entwicklungsphase, in der die "
                      "Scorecard verwendet wird (sie braucht weder Umsatz noch Gewinn).")
        debt = inputs.financial_assumptions.existing_debt_balance
        if debt > 0 and debt >= op.cash_available:
            return _t(f"The company's debt ({_eur(debt)}) is larger than the value the methods find for the business, "
                      "so the shares are worth nothing before the new money comes in.",
                      f"Die Schulden des Unternehmens ({_eur(debt)}) sind höher als der Wert, den die Methoden für das "
                      "Geschäft ermitteln; die Anteile sind vor dem neuen Geld also nichts wert.")
        return _t("Usually this means the plan never becomes profitable, or the amount raised is larger than "
                  "the plan supports. Check the revenue plan, the target margin and the amount you are raising.",
                  "Meist bedeutet das, dass der Plan nie profitabel wird oder der eingeworbene Betrag größer ist, als "
                  "der Plan trägt. Prüfen Sie Umsatzplan, Zielmarge und den Betrag, den Sie einwerben.")

    if usable_weight <= 0:
        reasons = "\n".join(f"• {method_name(k)}: {r}" for k, (v, r) in raw.items() if r and weights[k] > 0)
        raise ValuationError(_t("None of the methods for this stage can give a value with these inputs:",
                                "Keine der Methoden für diese Phase kann mit diesen Angaben einen Wert ermitteln:")
                             + "\n" + reasons + "\n\n" + advice())

    no_value = {"venture_capital": vc_result.no_value_reason, "dcf": dcf_result.no_value_reason}
    method_values = {}
    blended = 0.0
    for key, (value, reason) in raw.items():
        w = weights[key]
        if w <= 0:
            status, note = "not_used", (_t("Used only for pre-revenue companies (Idea and Development stages).",
                                           "Nur vor Umsatzbeginn verwendet (Ideen- und Entwicklungsphase).")
                                        if key == "scorecard" else _t("Not used at this stage.",
                                                                      "In dieser Phase nicht verwendet."))
        elif value is None:
            status, note = "not_meaningful", reason
        elif no_value.get(key):
            status, note = "ok", no_value[key] + _t(f" It counts as {_eur(value)} in the blend.",
                                                    f" Sie zählt mit {_eur(value)} in der Gewichtung.")
        elif key in ("comparables", "dcf") and (comparables_result if key == "comparables" else dcf_result).debt_exceeds_value:
            status, note = "ok", _t("Debt exceeds the enterprise value, so the equity is worth about zero.",
                                    "Die Schulden übersteigen den Unternehmenswert; das Eigenkapital ist also etwa "
                                    "nichts wert.")
        else:
            status, note = "ok", None
        w_used = w / usable_weight if status == "ok" else 0.0
        weighted = value * w_used if status == "ok" else None
        if weighted is not None:
            blended += weighted
        method_values[key] = MethodValue(
            pre_money_value=value, weight=w, weight_used=w_used, weighted_value=weighted,
            status=status, note=note)

    counted = [mv for mv in method_values.values() if mv.status == "ok" and mv.weight_used > 0]
    if blended <= 0 or all(mv.note for mv in counted):
        # Every method that applies finds no value for the business itself (at most the cash on hand is left):
        # say so, rather than report that as a valuation.
        reasons = "\n".join(f"• {method_name(k)}: {mv.note}" for k, mv in method_values.items()
                            if mv.note and mv.status != "not_used")
        raise ValuationError(_t("None of the methods for this stage finds any value for the business with these inputs:",
                                "Keine der Methoden für diese Phase findet mit diesen Angaben einen Wert für das "
                                "Geschäft:")
                             + "\n" + reasons + "\n\n" + advice())

    capital_needed = inputs.funding.capital_needed
    round_logic = compute_round_logic(company.company_stage, capital_needed, blended)
    warnings = collect_warnings(inputs, projections, dcf_result, bench, scorecard_result, method_values)

    return ValuationOutput(
        valuation_date=projections.valuation_date,
        projections=projections,
        wacc=wacc_result,
        scorecard=scorecard_result,
        venture_capital=vc_result,
        comparables=comparables_result,
        dcf=dcf_result,
        equity_bridge=EquityBridge(debt=debt, cash=op.cash_available, net_debt=debt - op.cash_available),
        benchmarks_used=dict(bench.used),
        method_values=method_values,
        blended_pre_money_valuation=blended,
        capital_needed=capital_needed,
        post_money_valuation=blended + capital_needed,
        warnings=warnings,
        method_range=method_range(method_values),
        round_logic=round_logic,
    )


# ============================================================================
# SECTION 12 — Scenario / sensitivity analysis
#
# Re-runs the full valuation at scaled Year-1 revenue levels (growth rates
# kept, so the whole trajectory scales). Scorecard and Comparables don't
# move: they don't use projected revenue.
# ============================================================================

SCENARIO_REVENUE_MULTIPLIERS = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3]


def scenario_label(multiplier: float) -> str:
    return f"{round(multiplier * 100)}%"


def run_valuation_scenarios(
    inputs: ValuationInput,
    multipliers: Optional[list[float]] = None,
) -> dict[str, ValuationOutput]:
    """
    Returns an ordered dict: scenario label (e.g. "80%") -> full ValuationOutput.
    A scenario in which no method is meaningful is left out.
    """
    multipliers = multipliers if multipliers is not None else SCENARIO_REVENUE_MULTIPLIERS
    base_revenue = inputs.financial_assumptions.revenue_year1
    base_plan = inputs.financial_assumptions.revenue_by_year
    try:
        base = run_valuation(inputs)
    except ValuationError:
        return {}

    results: dict[str, ValuationOutput] = {}
    for m in multipliers:
        scaled_inputs = inputs.model_copy(deep=True)
        scaled_inputs.financial_assumptions.revenue_year1 = base_revenue * m
        if base_plan is not None:  # a year-by-year plan moves as a whole
            scaled_inputs.financial_assumptions.revenue_by_year = [r * m for r in base_plan]
        try:
            scenario = run_valuation(scaled_inputs)
        except ValuationError:
            # No method gives a value at this revenue level: keep the scenario, with every method at 0.
            scenario = copy.deepcopy(base)
            for key, mv in scenario.method_values.items():
                scenario.method_values[key] = MethodValue(None, mv.weight, 0.0, None, "not_meaningful", None)
        results[scenario_label(m)] = _with_base_weights(scenario, base)
    return results


def _with_base_weights(scenario: ValuationOutput, base: ValuationOutput) -> ValuationOutput:
    """Re-blends a scenario with the main result's weights, so moving revenue only moves the
    values, never the mix of methods. (Otherwise a method that stops working at lower revenue
    would drop out, the others would be re-weighted, and the blend could rise as revenue falls.)
    A method used in the main result that gives no positive value in the scenario counts as 0;
    a method left out of the main result is left out here too."""
    blended = 0.0
    for key, mv in scenario.method_values.items():
        base_mv = base.method_values[key]
        if base_mv.status == "ok" and base_mv.weight_used > 0:
            value = mv.pre_money_value if mv.status == "ok" else 0.0
            note = mv.note if mv.status == "ok" else _t(
                "Gives no positive value in this scenario, so it counts as €0 (weights as in your main result).",
                "Ergibt in diesem Szenario keinen positiven Wert und zählt deshalb mit 0 € (Gewichte wie im "
                "Hauptergebnis).")
            scenario.method_values[key] = MethodValue(
                pre_money_value=value, weight=base_mv.weight, weight_used=base_mv.weight_used,
                weighted_value=value * base_mv.weight_used, status="ok", note=note)
            blended += value * base_mv.weight_used
        else:
            scenario.method_values[key] = MethodValue(
                pre_money_value=None, weight=base_mv.weight, weight_used=0.0, weighted_value=None,
                status=base_mv.status, note=base_mv.note)
    scenario.blended_pre_money_valuation = blended
    scenario.post_money_valuation = blended + scenario.capital_needed
    scenario.method_range = method_range(scenario.method_values)
    return scenario
