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
    "Financial Svcs. (Non-bank & Insurance)",
    "Insurance (General)",
    "Insurance (Life)",
    "Insurance (Prop/Cas.)",
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
    # the country's statutory corporate tax rate. (Name kept for saved data.)
    dcf_tax_rate_override: Optional[float] = Field(default=None, ge=0, le=0.6)

    # Optional replacement for the regional "typical pre-revenue pre-money"
    # benchmark used by the Scorecard method (built-in: Equidam H1 2026 medians).
    benchmark_pre_money_override: Optional[float] = Field(default=None, gt=0)

    # Optional company logo for the PDF report cover page/header. Either a
    # plain base64 string or a data URL (e.g. "data:image/png;base64,....").
    logo_base64: Optional[str] = None


class MarketAndTeamAssessment(BaseModel):
    """
    Each field's value must be an option string present in
    scorecard_qualitative_lookup() under the matching key (or an old
    spelling listed in scorecard_option_aliases).
    """
    management_team_experience: str
    willingness_to_step_aside_for_ceo: str
    management_team_completeness: str
    product_development_stage: str
    product_compelling_to_customers: str
    product_can_be_duplicated: str
    strength_of_competitors_in_market: str
    strength_of_competitive_products: str
    target_market_size: str
    revenue_potential_in_5_years: str
    sales_channels_partners: str
    marketing_partners: str
    need_for_additional_funding_rounds: str
    key_competitor_1: Optional[str] = None
    key_competitor_2: Optional[str] = None
    key_competitor_3: Optional[str] = None

    @model_validator(mode="after")
    def _current_wording(self):
        """Old saved answers with misspelled option text are mapped to today's wording."""
        for criterion in scorecard_qualitative_lookup():
            if hasattr(self, criterion):
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


def effective_tax_rate(company: CompanyProfile) -> float:
    """One tax rate for the whole valuation: the user's, else the country's statutory rate."""
    if company.dcf_tax_rate_override is not None:
        return company.dcf_tax_rate_override
    return get_country(company.country)["corporate_tax_rate"]


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def valuation_date_of(company: CompanyProfile) -> date:
    return company.valuation_date or date.today()


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
    tax_rate: float
    opening_working_capital: float
    years: list[YearProjection] = field(default_factory=list)

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

    tax_rate = effective_tax_rate(company)
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
        if ebit <= 0:
            tax = 0.0
            loss_carryforward += -ebit
        else:
            used = min(loss_carryforward, ebit)
            loss_carryforward -= used
            tax = (ebit - used) * tax_rate

        working_capital = revenue * wc_pct
        change_in_wc = working_capital - prior_wc
        prior_wc = working_capital
        capex = assumptions.capex_by_year[i]
        fcf = ebit - tax + da - capex - change_in_wc

        period_end = _add_years(vdate, i + 1) - timedelta(days=1)
        years.append(YearProjection(
            year_label=f"Y{i + 1}",
            period_label=f"to {period_end:%b %Y}",
            revenue=revenue,
            ebitda_margin=margin,
            ebitda=ebitda,
            da=da,
            ebit=ebit,
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
        tax_rate=tax_rate,
        opening_working_capital=opening_wc,
        years=years,
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


def compute_wacc(company: CompanyProfile, bench: Optional[_BenchmarkRecorder] = None) -> WaccResult:
    bench = bench or _BenchmarkRecorder(company.industry, company.business_territory_region)
    country = get_country(company.country)
    rf = market_parameters()["risk_free_rate"]

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
    benchmark_source: str  # "table" | "user_override"
    benchmark_basis: str  # where the built-in figure comes from
    total_factor: float
    pre_money_valuation: float


def compute_scorecard(company: CompanyProfile, market: MarketAndTeamAssessment) -> ScorecardResult:
    if company.benchmark_pre_money_override is not None:
        benchmark, source, basis = company.benchmark_pre_money_override, "user_override", "Your own benchmark"
    else:
        try:
            row = scorecard_benchmarks()[company.business_territory_region]
        except KeyError as e:
            raise KeyError(f"No Scorecard benchmark for {company.business_territory_region!r}") from e
        benchmark, source, basis = row["eur"], "table", row["basis"]

    criteria_results = {}
    total_factor = 0.0
    for key, weight in SCORECARD_CRITERIA_WEIGHTS.items():
        questions = SCORECARD_CRITERIA_QUESTIONS[key]
        score = sum(get_scorecard_score(q, getattr(market, q)) for q in questions) / len(questions)
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
    tv = fcf[-1] * (1 + g) / (tv_rate - g)
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
    else:
        warn("no_revenue_history", "No revenue in the last 12 months: projected margins use the industry "
             "average from Year 1, and the Comparables method can't be applied.", "info")
    if any(g > 1.0 for g in fa.revenue_growth_rates):
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
                 "moves from one to the other over five years; enter your own target margin if that isn't "
                 "realistic.")

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
        warn("region_mismatch", f"Companies in {cp.country} are usually benchmarked against "
             f"{_REGION_SHORT.get(expected_region, expected_region)}; you chose "
             f"{_REGION_SHORT.get(cp.business_territory_region, cp.business_territory_region)}. "
             "Industry benchmarks follow the region you chose.", "info")

    region_name = _REGION_SHORT.get(cp.business_territory_region, cp.business_territory_region)
    for b in bench.used.values():
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
    if dcf.terminal_value_share is not None and dcf.terminal_value_share > 0.75:
        warn("tv_share", f"The terminal value is {_pct(dcf.terminal_value_share)} of the DCF value, so the DCF "
             "depends mostly on years after the forecast.", "info")

    if (method_values["scorecard"].status == "ok" and scorecard.benchmark_source == "table"
            and not scorecard_benchmarks()[cp.business_territory_region]["regional_figure"]):
        warn("benchmark_global", f"There is no separate published pre-revenue benchmark for "
             f"{region_name}, so the Scorecard uses the all-region median. Enter a local benchmark if you "
             "have one.", "info")

    for key, mv in method_values.items():
        if mv.status == "not_meaningful" and mv.weight > 0:
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
        reasons = "; ".join(f"{METHOD_NAMES[k]}: {r}" for k, (v, r) in raw.items() if r and weights[k] > 0)
        raise ValuationError(
            "None of the methods used for this stage gives a meaningful value for these inputs. " + reasons)

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

    results: dict[str, ValuationOutput] = {}
    for m in multipliers:
        scaled_inputs = inputs.model_copy(deep=True)
        scaled_inputs.financial_assumptions.revenue_year1 = base_revenue * m
        try:
            results[scenario_label(m)] = run_valuation(scaled_inputs)
        except ValuationError:
            continue
    return results
