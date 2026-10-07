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

import copy
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ============================================================================
# SECTION 1 — Reference data (industry benchmarks, country data, stage
# parameters, scorecard lookup tables, sources). Bundled as a single JSON
# file alongside this module.
# ============================================================================

_DATA_PATH = Path(__file__).parent / "reference_data.json"


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
    """stage -> one-line definition shown in the wizard."""
    return _all_reference_data().get("stage_descriptions", {})


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


def scorecard_option_aliases() -> dict:
    """criterion -> {old option text: current option text} (old saved valuations keep working)."""
    return _all_reference_data().get("scorecard_option_aliases", {})


def market_parameters() -> dict:
    return _all_reference_data()["market_parameters"]


def data_sources() -> dict:
    return _all_reference_data()["sources"]


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
    committed_capital: float = Field(default=0.0, ge=0)
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
    benchmark_pre_money_override: Optional[float] = Field(default=None, gt=0)

    # Optional company logo for the PDF report cover page/header. Either a
    # plain base64 string or a data URL (e.g. "data:image/png;base64,....").
    logo_base64: Optional[str] = None

    @field_validator("industry")
    @classmethod
    def _current_industry_name(cls, v):
        """Inputs using an old industry name (e.g. a corrected misspelling) keep working."""
        return industry_aliases().get(v, v)

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
    current_revenue_last_12_months: float = Field(ge=0)
    current_ebitda: float
    cash_available: float = Field(default=0.0, ge=0)
    current_ppe_value: float = Field(default=0.0, ge=0)


class FinancialAssumptions(BaseModel):
    revenue_year1: float = Field(gt=0)
    # growth rate applied to get Y2, Y3, Y4, Y5 from the prior year (as fractions, e.g. 0.10)
    revenue_growth_rates: list[float] = Field(default_factory=lambda: [0.10, 0.10, 0.10, 0.10])
    # capex for Y1..Y5 (5 values)
    capex_by_year: list[float] = Field(default_factory=lambda: [0, 30000, 30000, 30000, 30000])
    # outstanding interest-bearing debt today (subtracted to get equity value)
    existing_debt_balance: float = Field(default=0.0, ge=0)
    # Optional Year-5 EBITDA margin to use instead of the industry's.
    target_ebitda_margin_override: Optional[float] = Field(default=None, ge=-1, le=0.9)

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
        return v


class Shareholder(BaseModel):
    name: str
    ownership_pct: float = Field(ge=0, le=1)


class FundingRequirement(BaseModel):
    capital_needed: float = Field(gt=0)
    use_of_funds: dict[str, float] = Field(default_factory=dict)

    @field_validator("use_of_funds")
    @classmethod
    def _non_negative(cls, v):
        if any(x < 0 for x in v.values()):
            raise ValueError("use-of-funds amounts can't be negative")
        return v


class VCMethodAssumptions(BaseModel):
    """Assumptions specific to the Venture Capital method."""
    number_of_existing_shares: float = Field(default=1_000_000.0, ge=1)


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
    ebitda_margin: float
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
    starting_margin_source: str  # "company" | "industry" (no revenue history)
    target_ebitda_margin: float
    target_margin_source: str  # "industry" | "user_override"
    tax_rate: float  # long-run rate (terminal value, WACC); the single rate unless taxes are stepped
    opening_working_capital: float
    years: list[YearProjection] = field(default_factory=list)
    tax_schedule: Optional[TaxSchedule] = None
    # Year-5 free cash flow re-taxed at the long-run rate: the base of the
    # terminal value. Equal to Year 5's free cash flow when the rate is flat.
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

    # Start from the company's own margin (if it has revenue) and move in equal
    # steps to the target margin, reached in Year 5.
    if operating.current_revenue_last_12_months > 0:
        start_margin = operating.current_ebitda / operating.current_revenue_last_12_months
        start_margin = max(-1.0, min(start_margin, 0.9))
        start_src = "company"
    else:
        start_margin, start_src = target_margin, "industry"

    da_pct = bench.get("da_pct_revenue")
    wc_pct = (bench.get("acc_receivable_pct_revenue") + bench.get("inventory_pct_revenue")
              - bench.get("acc_payable_pct_revenue"))
    ar_pct = bench.used["acc_receivable_pct_revenue"].value
    inv_pct = bench.used["inventory_pct_revenue"].value
    ap_pct = bench.used["acc_payable_pct_revenue"].value

    taxes = tax_schedule(company)
    vdate = valuation_date_of(company)

    revenues = [assumptions.revenue_year1]
    for g in assumptions.revenue_growth_rates:
        revenues.append(revenues[-1] * (1 + g))

    opening_wc = operating.current_revenue_last_12_months * wc_pct
    prior_wc = opening_wc
    loss_carryforward = 0.0
    years: list[YearProjection] = []
    n = len(revenues)
    for i, revenue in enumerate(revenues):
        margin = start_margin + (target_margin - start_margin) * (i + 1) / n
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
        terminal_fcf = ebit - taxable * taxes.long_run_rate + da - capex - change_in_wc

        period_end = _add_years(vdate, i + 1) - timedelta(days=1)
        years.append(YearProjection(
            year_label=f"Y{i + 1}",
            period_label=f"to {period_end:%b %Y}",
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
        benchmark, source, basis = company.benchmark_pre_money_override, "user_override", "Your own benchmark"
    elif country_row:
        benchmark, source, basis = country_row["eur"], "country_table", country_row["basis"]
        to_be_sourced = bool(country_row.get("to_be_sourced"))
    else:
        try:
            row = scorecard_benchmarks()[company.business_territory_region]
        except KeyError as e:
            raise KeyError(f"No Scorecard benchmark for {company.business_territory_region!r}") from e
        benchmark, source, basis = row["eur"], "table", row["basis"]

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
    not_meaningful_reason: Optional[str]


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
        result["not_meaningful_reason"] = "EBITDA-based exit values don't apply to banks and insurers."
        return VCMethodResult(**result)
    if exit_value <= 0:
        result["not_meaningful_reason"] = (
            f"Projected EBITDA in the exit year ({exit_year.year_label}) is not positive, "
            "so there is no exit value to discount.")
        return VCMethodResult(**result)
    if exit_equity <= 0:
        result["not_meaningful_reason"] = "Debt is larger than the projected exit value."
        return VCMethodResult(**result)

    post = exit_equity / (1 + target_return) ** T
    result["post_money_valuation"] = post
    if post <= I:
        result["not_meaningful_reason"] = (
            "The capital being raised is larger than the value the exit supports today, "
            "so the pre-money value would be negative.")
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
# SECTION 8 — Comparables method (EV/EBITDA multiple)
#
# Trailing (last-12-month) EBITDA x Damodaran's trailing EV/EBITDA multiple,
# reduced by a private-company discount, then converted from enterprise
# value to equity value (minus debt, plus cash).
# ============================================================================


@dataclass
class ComparablesResult:
    trailing_ebitda: float
    ev_ebitda_multiple: float
    ev_ebitda_multiple_source: str
    public_company_ev: float
    private_company_discount: float
    enterprise_value: float
    debt: float
    cash: float
    equity_value: Optional[float]
    pre_money_valuation: Optional[float]
    debt_exceeds_value: bool
    not_meaningful_reason: Optional[str]


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

    public_ev = ltm * multiple
    ev = public_ev * (1 - discount)
    equity = ev - debt + cash
    reason = None
    if company.industry in FINANCIAL_SECTOR_INDUSTRIES:
        reason = "EV/EBITDA multiples don't apply to banks and insurers."
    elif ltm <= 0:
        reason = "The company has no positive EBITDA over the last 12 months to apply a multiple to."
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
    not_meaningful_reason: Optional[str]


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
    risk_adj_ev = ev * p
    cash = operating.cash_available
    equity = risk_adj_ev - debt + cash

    reason = None
    if company.industry in FINANCIAL_SECTOR_INDUSTRIES:
        reason = "Free-cash-flow DCF doesn't apply to banks and insurers."
    elif ev <= 0:
        reason = "Projected free cash flows give a negative enterprise value."
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

# Country names that take "the" in a sentence ("in the United States").
_COUNTRIES_WITH_THE = {
    "United States", "United Kingdom", "Netherlands", "Bahamas", "Philippines", "Maldives", "Cayman Islands",
    "Turks and Caicos Islands", "Solomon Islands", "Isle of Man", "Czech Republic", "Dominican Republic",
    "United Arab Emirates", "Congo (Democratic Republic)", "Congo (Republic)",
}


def country_in_text(country: str) -> str:
    return f"the {country}" if country in _COUNTRIES_WITH_THE else country


_METRIC_LABELS = {
    "ebitda_margin": "EBITDA margin",
    "da_pct_revenue": "D&A (% of revenue)",
    "acc_receivable_pct_revenue": "accounts receivable (% of revenue)",
    "inventory_pct_revenue": "inventory (% of revenue)",
    "acc_payable_pct_revenue": "accounts payable (% of revenue)",
    "beta": "beta",
    "cost_of_debt": "cost of debt",
    "equity_pct_capital": "equity share of capital",
    "debt_pct_capital": "debt share of capital",
    "ev_ebitda_multiple": "EV/EBITDA multiple",
}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _eur(x: float) -> str:
    return f"€{x:,.0f}"


PRE_REVENUE_STAGES = {"Idea stage", "Development stage"}
# Revenue (last 12 months) above which an Idea/Development stage choice is questioned.
STAGE_REVENUE_THRESHOLD = 100_000
# Fall from last-12-month revenue to Year-1 revenue above which the plan is questioned.
REVENUE_DROP_WARNING = 0.2
# VC pre-money below this share of the raise is explained (the raise nearly uses up the exit value).
VC_LOW_SHARE_OF_RAISE = 0.25
# Share of the company the new investors would own above which the round is questioned.
INVESTOR_STAKE_WARNING = 0.5


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
    if ltm > 0:
        jump = fa.revenue_year1 / ltm - 1
        if jump > 1.0:
            warn("revenue_jump", f"Year-1 revenue ({_eur(fa.revenue_year1)}) is {jump:.0%} above the last "
                 f"12 months ({_eur(ltm)}). Check that this growth is realistic; every cash-flow method "
                 "builds on it.")
        elif jump < -REVENUE_DROP_WARNING:
            warn("revenue_drop", f"Year-1 revenue ({_eur(fa.revenue_year1)}) is {-jump:.0%} below the last "
                 f"12 months ({_eur(ltm)}). If you don't expect sales to fall, check both figures: every "
                 "cash-flow method builds on the Year-1 plan.")
    else:
        margin_used = ("your own Year-5 target margin" if fa.target_ebitda_margin_override is not None
                       else "the industry average margin")
        warn("no_revenue_history", f"No revenue in the last 12 months: the projection uses {margin_used} from "
             "Year 1, and the Comparables method can't be applied.", "info")
    # Growth above 100% a year is normal for a company that is only starting to sell.
    if ltm > 0 and any(g > 1.0 for g in fa.revenue_growth_rates):
        warn("high_growth", "One or more yearly growth rates is above 100%. Check these are realistic.")

    revenue_base = ltm if ltm > 0 else fa.revenue_year1
    if op.current_ppe_value > 2 * revenue_base:
        warn("ppe_scale", f"PP&E ({_eur(op.current_ppe_value)}) is {op.current_ppe_value / revenue_base:.1f}× "
             "revenue, which is unusual for most young companies. Check the figure.")
    for y in projections.years:
        if y.capex > 0.3 * y.revenue:
            warn("capex_scale", f"Capex in {y.year_label} ({_eur(y.capex)}) is more than 30% of revenue.")
            break

    if projections.starting_margin_source == "company":
        gap = projections.starting_ebitda_margin - projections.target_ebitda_margin
        if abs(gap) > 0.10:
            warn("margin_gap", f"Your current EBITDA margin ({_pct(projections.starting_ebitda_margin)}) "
                 f"differs from the {'target' if projections.target_margin_source == 'user_override' else 'industry'} "
                 f"margin ({_pct(projections.target_ebitda_margin)}) by more than 10 points. The projection "
                 "moves from one to the other over five years; "
                 + ("adjust your target margin if that isn't realistic."
                    if projections.target_margin_source == "user_override"
                    else "enter your own target margin if that isn't realistic."))

    if inputs.ownership:
        total = sum(s.ownership_pct for s in inputs.ownership)
        if abs(total - 1) > 0.005:
            warn("ownership_sum", f"Ownership adds up to {_pct(total)}, not 100%.")

    uof = inputs.funding.use_of_funds
    if uof:
        total_uof = sum(uof.values())
        cap = inputs.funding.capital_needed
        if abs(total_uof - cap) > 0.005 * cap:
            warn("use_of_funds_sum", f"Use of funds adds up to {_eur(total_uof)}, but the capital needed is "
                 f"{_eur(cap)}.")
        capex_uof = uof.get("Capital expenditures", 0) or 0
        if capex_uof > 0 and abs(capex_uof - fa.capex_by_year[0]) > 0.5:
            warn("capex_mismatch", f"Use of funds includes {_eur(capex_uof)} of capital expenditure, but "
                 f"planned capex for Year 1 is {_eur(fa.capex_by_year[0])}.")

    try:
        expected_region = default_region_for_country(cp.country)
    except KeyError:
        expected_region = None
    if expected_region and expected_region != "Global" and expected_region != cp.business_territory_region:
        warn("region_mismatch", f"Companies in {country_in_text(cp.country)} are usually compared with "
             f"{_REGION_ADJECTIVE.get(expected_region, expected_region)} industry figures; you chose "
             f"{_REGION_ADJECTIVE.get(cp.business_territory_region, cp.business_territory_region)} figures, "
             "so the benchmarks follow your choice.", "info")

    region_name = _REGION_SHORT.get(cp.business_territory_region, cp.business_territory_region)
    # Banks and insurers are valued by the Scorecard only, so industry-figure fallbacks don't matter.
    for b in ([] if cp.industry in FINANCIAL_SECTOR_INDUSTRIES else bench.used.values()):
        if b.source == "industry_global":
            warn(f"fallback_{b.metric}", f"Damodaran has no usable {region_name} figure for "
                 f"{_METRIC_LABELS.get(b.metric, b.metric)} in this industry, so the industry's global "
                 "figure is used.", "info")
        elif b.source == "cross_industry":
            warn(f"fallback_{b.metric}", f"No figure for {_METRIC_LABELS.get(b.metric, b.metric)} in this "
                 "industry: the median across all industries is used.", "info")

    if cp.industry in FINANCIAL_SECTOR_INDUSTRIES:
        warn("financial_sector", "Banks and insurers can't be valued on EBITDA or free cash flow, so only the "
             "Scorecard method is used.")
    if dcf.terminal_value_floor_applied:
        warn("tv_floor", "The discount rate is close to the long-run growth rate, so the terminal value was "
             f"calculated with a minimum {_pct(MIN_DISCOUNT_GROWTH_SPREAD)} gap between them.")
    if dcf.terminal_value_share is not None and dcf.terminal_value_share > 1:
        warn("tv_share", "The forecast years burn cash in total, so all of the DCF value comes from the years "
             "after Year 5 (the terminal value).", "info")
    elif dcf.terminal_value_share is not None and dcf.terminal_value_share > 0.75:
        warn("tv_share", f"The terminal value is {_pct(dcf.terminal_value_share)} of the DCF value, so the DCF "
             "depends mostly on years after the forecast.", "info")

    if (method_values["scorecard"].status == "ok" and scorecard.benchmark_source == "table"
            and cp.business_territory_region != "Global"
            and not scorecard_benchmarks()[cp.business_territory_region]["regional_figure"]):
        warn("benchmark_global", f"There is no separate published pre-revenue benchmark for "
             f"{region_name}, so the Scorecard uses the all-region median. Enter a local benchmark if you "
             "have one.", "info")
    if method_values["scorecard"].status == "ok" and scorecard.unanswered:
        n = len(scorecard.unanswered)
        warn("scorecard_unanswered", f"{n} of the {sum(len(q) for q in SCORECARD_CRITERIA_QUESTIONS.values())} "
             f"Scorecard question{'s were' if n > 1 else ' was'} not answered and {'are' if n > 1 else 'is'} "
             "scored as typical (100%). Answer them for a Scorecard value that reflects your company.")

    # Stage and revenue should tell the same story: the stage decides the method weights.
    stage_name = cp.company_stage.replace(" stage", "").replace(" Stage", "")
    if cp.company_stage in PRE_REVENUE_STAGES and ltm >= STAGE_REVENUE_THRESHOLD:
        warn("stage_revenue", f"You chose the {stage_name} stage, which is for companies without meaningful "
             f"revenue, but entered {_eur(ltm)} of revenue in the last 12 months. At this stage the Scorecard "
             f"(a comparison with typical pre-revenue startups) counts for {_pct(method_values['scorecard'].weight)} "
             "of the value. If you already sell, the Startup stage probably fits better.")
    elif cp.company_stage not in PRE_REVENUE_STAGES and ltm == 0:
        warn("stage_no_revenue", f"You chose the {stage_name} stage, which assumes the company already has "
             "customers and revenue, but entered no revenue for the last 12 months. The Scorecard, which is built "
             "for pre-revenue companies, is therefore not used. If you don't sell yet, the Idea or Development "
             "stage probably fits better.")

    # A round that hands investors most of the company is unusual and worth a second look.
    post = method_values_post_money(method_values, inputs.funding.capital_needed)
    if post:
        stake = inputs.funding.capital_needed / post
        if stake > INVESTOR_STAKE_WARNING:
            warn("investor_stake", f"At this valuation, the {_eur(inputs.funding.capital_needed)} you are raising "
                 f"would buy {_pct(stake)} of the company (post-money {_eur(post)}). Early rounds usually sell well "
                 "under half of a company, so check the amount you are raising and your plan.")

    vc_mv = method_values["venture_capital"]
    raise_amount = inputs.funding.capital_needed
    if vc_mv.status == "ok" and vc_mv.weight_used > 0 and vc_mv.pre_money_value < VC_LOW_SHARE_OF_RAISE * raise_amount:
        warn("vc_low", f"The Venture Capital method values the company at only {_eur(vc_mv.pre_money_value)} before "
             f"the round: the {_eur(raise_amount)} you are raising nearly uses up the value the projected exit "
             "supports today at the return investors at your stage expect. It counts for "
             f"{_pct(vc_mv.weight_used)} of the blend and pulls it down; a smaller round, a later exit or a "
             "stronger plan would raise it.")

    vdate = valuation_date_of(cp)
    today = date.today()
    if abs((vdate - today).days) > 366:
        warn("valuation_date", f"The valuation date ({vdate:%d %B %Y}) is more than a year "
             f"{'before' if vdate < today else 'after'} today, but the market data, tax rates and benchmarks are "
             "current figures. Check the date.")
    if cp.year_of_incorporation and cp.year_of_incorporation > vdate.year:
        warn("incorporation_year", f"The year of incorporation ({cp.year_of_incorporation}) is after the "
             f"valuation date ({vdate.year}). Check both.")

    if method_values["scorecard"].status == "ok" and scorecard.benchmark_to_be_sourced:
        warn("benchmark_to_be_sourced", f"The {cp.country}-specific pre-revenue benchmark is still to be "
             f"sourced, so the Scorecard uses the Europe figure ({_eur(scorecard.benchmark_pre_money_valuation)}) "
             "as a placeholder. Enter a local benchmark if you have one.", "info")

    taxes = projections.tax_schedule
    pays_tax = any(y.tax_on_ebit > 0 for y in projections.years)  # no note if losses mean no tax is due
    if taxes is not None and taxes.basis == "germany_schedule" and pays_tax:
        rates = ", ".join(f"{y.year_label} {_pct(y.tax_rate)}" for y in projections.years)
        hebesatz = (f"your Hebesatz of {taxes.hebesatz * 100:.0f}%" if taxes.hebesatz_source == "user"
                    else f"the national average Hebesatz of {taxes.hebesatz * 100:.0f}% (enter your "
                         "municipality's for a more precise figure)")
        kst = taxes.calendar_years
        warn("german_tax_schedule", f"German corporate tax falls from {kst[0]['corporate_tax']:.0%} to "
             f"{kst[-1]['corporate_tax']:.0%} by {kst[-1]['year']}, so each projection year uses its own combined "
             f"rate including solidarity surcharge and trade tax ({rates}; {_pct(taxes.long_run_rate)} after "
             f"Year 5). Trade tax uses {hebesatz}.", "info")

    for key, mv in method_values.items():
        if mv.status == "not_meaningful" and mv.weight > 0:
            # Without revenue, Comparables can't apply by design, and the "no_revenue_history"
            # note already says so: no second message.
            if key == "comparables" and ltm == 0:
                continue
            # Banks and insurers: the single "financial_sector" message already explains all three.
            if cp.industry in FINANCIAL_SECTOR_INDUSTRIES:
                continue
            warn(f"nm_{key}", f"{METHOD_NAMES[key]} left out of the blend: {mv.note}")
        elif mv.status == "ok" and mv.note:
            warn(f"zero_{key}", f"{METHOD_NAMES[key]}: {mv.note}")
    debt = inputs.financial_assumptions.existing_debt_balance
    if debt > 0 and method_values["scorecard"].status == "ok":
        warn("scorecard_debt", "The Scorecard compares you with a typical (usually debt-free) company and does "
             f"not subtract your {_eur(debt)} of debt; the other methods do.", "info")

    return out


# ============================================================================
# SECTION 11 — Orchestration and blend
# ============================================================================

METHOD_NAMES = {
    "scorecard": "Scorecard method",
    "venture_capital": "Venture Capital method",
    "comparables": "Comparables (EV/EBITDA multiple)",
    "dcf": "DCF method",
}


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
        raise ValuationError(
            "Banks and insurers can't be valued on EBITDA or free cash flow, and the Scorecard applies only to "
            "pre-revenue companies (Idea and Development stages), so this tool can't value this company.")
    if usable_weight <= 0:
        reasons = "\n".join(f"• {METHOD_NAMES[k]}: {r}" for k, (v, r) in raw.items() if r and weights[k] > 0)
        if op.current_revenue_last_12_months == 0 and company.company_stage not in PRE_REVENUE_STAGES:
            advice = ("Your company has no revenue yet: choose the Idea or Development stage, where the Scorecard "
                      "(which doesn't need revenue or profits) is used.")
        else:
            advice = ("Usually this means the plan never becomes profitable, or the amount raised is larger than "
                      "the plan supports. Check the revenue plan, the target margin and the amount you are raising.")
        raise ValuationError(
            "None of the methods for this stage can give a value with these inputs:\n" + reasons + "\n\n" + advice)

    method_values = {}
    blended = 0.0
    for key, (value, reason) in raw.items():
        w = weights[key]
        if w <= 0:
            status, note = "not_used", ("Used only for pre-revenue companies (Idea and Development stages)."
                                        if key == "scorecard" else "Not used at this stage.")
        elif value is None:
            status, note = "not_meaningful", reason
        elif key in ("comparables", "dcf") and (comparables_result if key == "comparables" else dcf_result).debt_exceeds_value:
            status, note = "ok", "Debt exceeds the enterprise value, so the equity is worth about zero."
        else:
            status, note = "ok", None
        w_used = w / usable_weight if status == "ok" else 0.0
        weighted = value * w_used if status == "ok" else None
        if weighted is not None:
            blended += weighted
        method_values[key] = MethodValue(
            pre_money_value=value, weight=w, weight_used=w_used, weighted_value=weighted,
            status=status, note=note)

    capital_needed = inputs.funding.capital_needed
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
    try:
        base = run_valuation(inputs)
    except ValuationError:
        return {}

    results: dict[str, ValuationOutput] = {}
    for m in multipliers:
        scaled_inputs = inputs.model_copy(deep=True)
        scaled_inputs.financial_assumptions.revenue_year1 = base_revenue * m
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
            note = mv.note if mv.status == "ok" else (
                "Gives no positive value in this scenario, so it counts as €0 (weights as in your main result).")
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
    return scenario
