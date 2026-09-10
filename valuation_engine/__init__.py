"""
valuation_engine - a pure-Python startup valuation calculation engine.

Everything lives in this one module (input models, reference-data loading,
financial projections, WACC, and all four valuation methods) so the whole
engine is a single file to read, copy, or upload - the logic underneath is
still organized into clearly separated sections below.

Computes a blended pre-money / post-money valuation from the Scorecard,
Venture Capital, DCF Multiples, and DCF methods, exactly reproducing the
corrected version of the original Excel workbook (three formula bugs and
one data-entry error were found and fixed along the way - see docs/ at the
repo root for the full write-up).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# SECTION 1 — Reference data (industry benchmarks, country data, stage
# parameters, scorecard lookup tables). Generic to any valuation - doesn't
# depend on the company being valued. Bundled as a single JSON file
# alongside this module.
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
    """country -> {gdp, moodys_rating, ..., corporate_tax_rate, region_grouping}"""
    return _all_reference_data()["country_data"]


def stage_parameters() -> dict:
    """stage -> {hurdle_rate, risk_multiplier, method_weights: {...}}"""
    return _all_reference_data()["stage_parameters"]


def stage_region_pre_money_benchmarks() -> dict:
    """stage -> region -> value"""
    return _all_reference_data()["stage_region_pre_money_benchmarks"]


def scorecard_qualitative_lookup() -> dict:
    """criterion -> {option_text: score}"""
    return _all_reference_data()["scorecard_qualitative_lookup"]


def survival_rate_by_years_since_incorporation() -> dict:
    """year (str) -> survival_rate. Not currently used by any method."""
    return _all_reference_data()["survival_rate_by_years_since_incorporation"]


def categorical_options() -> dict:
    """Valid values for country / industry / business_territory_region / company_stage."""
    return _all_reference_data()["categorical_options"]


def get_industry_metric(industry: str, metric: str, region: str) -> float:
    """Convenience accessor with a clear error if industry/metric/region is unknown."""
    try:
        return industry_benchmarks()[industry][metric][region]
    except KeyError as e:
        raise KeyError(
            f"No benchmark for industry={industry!r}, metric={metric!r}, region={region!r}"
        ) from e


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


def get_scorecard_score(criterion: str, option_text: str) -> float:
    try:
        return scorecard_qualitative_lookup()[criterion][option_text]
    except KeyError as e:
        raise KeyError(
            f"No score for criterion={criterion!r}, option={option_text!r}"
        ) from e


# ============================================================================
# SECTION 2 — Input models
#
# These map field-for-field onto the sheets of `main_file_structured.xlsx`
# (Company_Profile, Market_And_Team_Assessment, Operating_Performance,
# Financial_Projections assumptions, Ownership_And_Funding), so that
# workbook can be used directly as a source of truth / regression fixture.
# ============================================================================


class CompanyProfile(BaseModel):
    company_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    country: str
    website: Optional[str] = None
    num_founders: Optional[int] = None
    num_employees: Optional[int] = None
    year_of_incorporation: Optional[int] = None
    company_stage: str  # must match a key in stage_parameters
    committed_capital: float = 0.0
    business_activity: Optional[str] = None
    industry: str  # must match a key in industry_benchmarks
    business_territory_region: str  # must match a region key, e.g. "Emerging Markets (...)"
    business_model: Optional[str] = None
    exit_strategy: Optional[str] = None
    planned_time_to_exit_years: int = 3

    # DCF's own tax-rate assumption. In the source workbook this is a
    # separately user-entered field (NOT the same as the country's
    # statutory corporate tax rate used elsewhere) - kept distinct here
    # to faithfully reproduce the original model's behavior.
    dcf_tax_rate_override: float = 0.10

    # Optional company logo for the PDF report cover page/header. Either a
    # plain base64 string or a data URL (e.g. "data:image/png;base64,....").
    # Not used in any valuation math - purely cosmetic for report.py.
    logo_base64: Optional[str] = None


class MarketAndTeamAssessment(BaseModel):
    """
    Each field's value must be an exact option string present in
    scorecard_qualitative_lookup() under the matching key.
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


class OperatingPerformance(BaseModel):
    current_revenue_last_12_months: float
    current_ebitda: float
    cash_available: float
    current_ppe_value: float = 0.0


class FinancialAssumptions(BaseModel):
    revenue_year1: float
    # growth rate applied to get Y2, Y3, Y4, Y5 from the prior year (as fractions, e.g. 0.10)
    revenue_growth_rates: list[float] = Field(default_factory=lambda: [0.10, 0.10, 0.10, 0.10])
    # capex for Y1..Y5 (5 values). Source workbook default: [0, 30000, 30000, 30000, 30000]
    capex_by_year: list[float] = Field(default_factory=lambda: [0, 30000, 30000, 30000, 30000])
    # outstanding interest-bearing debt balance (constant across years unless overridden)
    existing_debt_balance: float = 0.0

    @field_validator("revenue_growth_rates")
    @classmethod
    def _four_growth_rates(cls, v):
        if len(v) != 4:
            raise ValueError("revenue_growth_rates must have exactly 4 values (for Y2, Y3, Y4, Y5)")
        return v

    @field_validator("capex_by_year")
    @classmethod
    def _five_capex_years(cls, v):
        if len(v) != 5:
            raise ValueError("capex_by_year must have exactly 5 values (for Y1..Y5)")
        return v


class Shareholder(BaseModel):
    name: str
    ownership_pct: float


class FundingRequirement(BaseModel):
    capital_needed: float
    use_of_funds: dict[str, float] = Field(default_factory=dict)


class VCMethodAssumptions(BaseModel):
    """Assumptions specific to the Venture Capital method."""
    number_of_existing_shares: float = 1_000_000.0


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
# SECTION 3 — Financial projections
#
# Builds the 5-year financial projection (P&L + working capital) from a
# revenue trajectory and industry benchmarks - equivalent to
# INPUTS Questionnaire rows 71-102 in the original workbook.
# ============================================================================


@dataclass
class YearProjection:
    year_label: str
    revenue: float
    cogs: float
    sga: float
    other_opex: float
    ebitda: float
    da: float
    ebit: float
    interest_on_debt: float
    tax_amount: float
    net_profit: float
    accounts_receivable: float
    inventory: float
    accounts_payable: float
    working_capital: float
    change_in_working_capital: float
    capex: float


@dataclass
class FinancialProjections:
    years: list[YearProjection] = field(default_factory=list)

    def revenue(self) -> list[float]:
        return [y.revenue for y in self.years]

    def ebitda(self) -> list[float]:
        return [y.ebitda for y in self.years]

    def ebit(self) -> list[float]:
        return [y.ebit for y in self.years]

    def capex(self) -> list[float]:
        return [y.capex for y in self.years]

    def change_in_working_capital(self) -> list[float]:
        return [y.change_in_working_capital for y in self.years]

    def da(self) -> list[float]:
        return [y.da for y in self.years]


def build_projections(
    company: CompanyProfile,
    assumptions: FinancialAssumptions,
) -> FinancialProjections:
    industry = company.industry
    region = company.business_territory_region

    cogs_pct = get_industry_metric(industry, "cogs_pct_revenue", region)
    sga_pct = get_industry_metric(industry, "sga_pct_revenue", region)
    da_pct = get_industry_metric(industry, "da_pct_revenue", region)
    ar_pct = get_industry_metric(industry, "acc_receivable_pct_revenue", region)
    inv_pct = get_industry_metric(industry, "inventory_pct_revenue", region)
    ap_pct = get_industry_metric(industry, "acc_payable_pct_revenue", region)
    book_interest_rate = get_industry_metric(industry, "book_interest_rate", region)

    # "Other operational expenses" in the source workbook is a flat 1.5%
    # of Year-1 revenue, held constant in € terms across all 5 years
    # (not re-derived from a benchmark each year) - reproduced as-is.
    other_opex_pct_of_y1_revenue = 0.015

    # NOTE on tax rates (reproducing an inconsistency present in the source
    # workbook - see docs/ at the repo root): the P&L's Net Profit line and
    # the DCF Method's cash-flow taxes both use the user-entered
    # `dcf_tax_rate_override` (10% for Valuativa DOO) - NOT the country's
    # actual statutory corporate tax rate. The country's real corporate tax
    # rate (30% for Tanzania, from country_data) is only used later, in the
    # WACC build's after-tax cost of debt.
    tax_rate_for_pl_and_dcf = company.dcf_tax_rate_override

    revenues = [assumptions.revenue_year1]
    for g in assumptions.revenue_growth_rates:
        revenues.append(revenues[-1] * (1 + g))

    other_opex_y1 = other_opex_pct_of_y1_revenue * revenues[0]

    prior_working_capital = None
    years: list[YearProjection] = []
    for i, revenue in enumerate(revenues):
        cogs = cogs_pct * revenue
        sga = sga_pct * revenue
        other_opex = other_opex_y1 * ((1 + 0.10) ** i)  # grows with the same 10% used for the illustrative other-opex line in the source
        ebitda = revenue - cogs - sga - other_opex
        da = da_pct * revenue
        ebit = ebitda - da

        interest_on_debt = book_interest_rate * assumptions.existing_debt_balance
        tax_amount = ebit * tax_rate_for_pl_and_dcf
        net_profit = ebit - interest_on_debt - tax_amount

        ar = ar_pct * revenue
        inventory = inv_pct * revenue
        ap = ap_pct * revenue
        working_capital = ar + inventory - ap
        change_in_wc = 0.0 if prior_working_capital is None else working_capital - prior_working_capital
        prior_working_capital = working_capital

        years.append(YearProjection(
            year_label=f"Y{i+1}",
            revenue=revenue,
            cogs=cogs,
            sga=sga,
            other_opex=other_opex,
            ebitda=ebitda,
            da=da,
            ebit=ebit,
            interest_on_debt=interest_on_debt,
            tax_amount=tax_amount,
            net_profit=net_profit,
            accounts_receivable=ar,
            inventory=inventory,
            accounts_payable=ap,
            working_capital=working_capital,
            change_in_working_capital=change_in_wc,
            capex=assumptions.capex_by_year[i],
        ))

    return FinancialProjections(years=years)


# ============================================================================
# SECTION 4 — WACC (discount rate)
#
# Matches the 'Low' scenario column of the original 'WACC Calculation'
# sheet (the column actually used by the DCF Method - the 'High' column
# pulled a different data-derived estimate and was never wired into any of
# the four valuation methods).
# ============================================================================

RISK_FREE_RATE = 0.04
ADDITIONAL_RISK_ADJUSTMENT = 0.0


def _mround(value: float, multiple: float) -> float:
    """Excel MROUND equivalent."""
    return round(value / multiple) * multiple


@dataclass
class WaccResult:
    beta: float
    country_market_risk_premium: float
    adjusted_market_risk_premium: float
    risk_free_rate: float
    cost_of_equity: float
    long_term_debt_rate: float
    country_corporate_tax_rate: float
    after_tax_cost_of_debt: float
    equity_pct_capital: float
    debt_pct_capital: float
    wacc: float


def compute_wacc(company: CompanyProfile) -> WaccResult:
    industry = company.industry
    region = company.business_territory_region
    country = get_country(company.country)

    beta = get_industry_metric(industry, "beta", region)
    country_market_risk_premium = country["country_risk_premium"]
    adjusted_market_risk_premium = beta * country_market_risk_premium

    cost_of_equity = _mround(
        adjusted_market_risk_premium + RISK_FREE_RATE + ADDITIONAL_RISK_ADJUSTMENT,
        0.0025,
    )

    long_term_debt_rate = get_industry_metric(industry, "book_interest_rate", region)
    country_corporate_tax_rate = country["corporate_tax_rate"]
    after_tax_cost_of_debt = (1 - country_corporate_tax_rate) * long_term_debt_rate

    equity_pct_capital = get_industry_metric(industry, "equity_pct_capital", region)
    debt_pct_capital = get_industry_metric(industry, "debt_pct_capital", region)

    wacc = _mround(
        debt_pct_capital * after_tax_cost_of_debt + equity_pct_capital * cost_of_equity,
        0.0025,
    )

    return WaccResult(
        beta=beta,
        country_market_risk_premium=country_market_risk_premium,
        adjusted_market_risk_premium=adjusted_market_risk_premium,
        risk_free_rate=RISK_FREE_RATE,
        cost_of_equity=cost_of_equity,
        long_term_debt_rate=long_term_debt_rate,
        country_corporate_tax_rate=country_corporate_tax_rate,
        after_tax_cost_of_debt=after_tax_cost_of_debt,
        equity_pct_capital=equity_pct_capital,
        debt_pct_capital=debt_pct_capital,
        wacc=wacc,
    )


# ============================================================================
# SECTION 5 — Scorecard method (Payne / Ohio TechAngels)
#
# Each of 6 criteria has a fixed weight and a score derived from 1-3
# qualitative questionnaire answers (averaged when there's more than one).
# Pre-money value = sum(weight_i * score_i * benchmark_valuation) * risk_multiplier.
#
# This reproduces the CORRECTED formulas:
#   - each criterion uses its own weight (bug 1 - the original had every
#     criterion using criterion 1's 30% weight)
#   - "Strategic Relationships with Partners" now checks the actual
#     Marketing Partners answer (bug 2 - the original compared the "Need
#     for additional funding rounds" answer against the Marketing Partners
#     lookup table by mistake, so a user's real Marketing Partners answer
#     never mattered)
#
# Both fixes are also applied in docs/main_file_corrected.xlsx.
#
# Faithful "no match" behavior: in the source spreadsheet, every
# criterion's IF-chain falls back to 0 when a stored answer's text doesn't
# exactly match any option in its dropdown lookup table - EXCEPT the first
# half of "Size of the Opportunity" (target market size), which falls back
# to 90%. `_score_or_default` reproduces that per-criterion.
# ============================================================================

SCORECARD_CRITERIA_WEIGHTS = {
    "strength_of_the_team": 0.30,
    "size_of_the_opportunity": 0.25,
    "competitive_environment": 0.10,
    "strength_and_protection_of_product": 0.15,
    "strategic_relationships_with_partners": 0.10,
    "funding_required": 0.10,
}

_SCORECARD_FALLBACKS = {
    "target_market_size": 0.90,
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
    risk_multiplier: float
    pre_money_valuation: float


def _score_or_default(criterion_key: str, option_text: str) -> float:
    default = _SCORECARD_FALLBACKS.get(criterion_key, 0.0)
    return scorecard_qualitative_lookup()[criterion_key].get(option_text, default)


def compute_scorecard(
    company: CompanyProfile,
    market: MarketAndTeamAssessment,
) -> ScorecardResult:
    stage_params = get_stage_params(company.company_stage)
    risk_multiplier = stage_params["risk_multiplier"]

    benchmark_pre_money_valuation = stage_region_pre_money_benchmarks()[
        company.company_stage
    ][company.business_territory_region]

    scores = {
        "strength_of_the_team": (
            _score_or_default("management_team_experience", market.management_team_experience)
            + _score_or_default("willingness_to_step_aside_for_ceo", market.willingness_to_step_aside_for_ceo)
            + _score_or_default("management_team_completeness", market.management_team_completeness)
        ) / 3,
        "size_of_the_opportunity": (
            _score_or_default("target_market_size", market.target_market_size)
            + _score_or_default("revenue_potential_in_5_years", market.revenue_potential_in_5_years)
        ) / 2,
        "competitive_environment": (
            _score_or_default("strength_of_competitors_in_market", market.strength_of_competitors_in_market)
            + _score_or_default("strength_of_competitive_products", market.strength_of_competitive_products)
        ) / 2,
        "strength_and_protection_of_product": (
            _score_or_default("product_development_stage", market.product_development_stage)
            + _score_or_default("product_compelling_to_customers", market.product_compelling_to_customers)
            + _score_or_default("product_can_be_duplicated", market.product_can_be_duplicated)
        ) / 3,
        # BUG 2, FIXED: now checks the real Marketing Partners answer.
        "strategic_relationships_with_partners": (
            _score_or_default("sales_channels_partners", market.sales_channels_partners)
            + _score_or_default("marketing_partners", market.marketing_partners)
        ) / 2,
        "funding_required": _score_or_default(
            "need_for_additional_funding_rounds", market.need_for_additional_funding_rounds
        ),
    }

    criteria_results = {}
    total = 0.0
    for key, weight in SCORECARD_CRITERIA_WEIGHTS.items():
        score = scores[key]
        amount = benchmark_pre_money_valuation * weight * score
        criteria_results[key] = ScorecardCriterionResult(weight=weight, score=score, amount_assigned=amount)
        total += amount

    pre_money_valuation = total * risk_multiplier

    return ScorecardResult(
        criteria=criteria_results,
        benchmark_pre_money_valuation=benchmark_pre_money_valuation,
        risk_multiplier=risk_multiplier,
        pre_money_valuation=pre_money_valuation,
    )


# ============================================================================
# SECTION 6 — Venture Capital method
#
# Exit value (revenue at the exit year x EBITDA margin x EV/EBITDA
# multiple) is discounted back to today using (1 + hurdle_rate)^time_to_exit
# to get post-money, then pre-money = post-money - investment.
# ============================================================================


@dataclass
class VCMethodResult:
    exit_year_revenue: float
    exit_year_ebitda: float
    ev_ebitda_multiple: float
    exit_value: float
    time_to_exit: int
    hurdle_rate: float
    investment_amount: float
    number_of_existing_shares: float
    post_money_valuation: float
    pre_money_valuation: float
    ownership_fraction_investors: float
    ownership_fraction_entrepreneurs: float
    number_of_new_shares: float
    price_per_share: float
    final_wealth_investors: float
    final_wealth_entrepreneurs: float


def compute_venture_capital(
    company: CompanyProfile,
    projections: FinancialProjections,
    funding: FundingRequirement,
    vc_assumptions: VCMethodAssumptions,
) -> VCMethodResult:
    stage_params = get_stage_params(company.company_stage)
    hurdle_rate = stage_params["hurdle_rate"]

    time_to_exit = company.planned_time_to_exit_years
    exit_year_index = time_to_exit - 1  # Y1 = index 0
    exit_year = projections.years[exit_year_index]

    ev_ebitda_multiple = get_industry_metric(
        company.industry, "ev_ebitda_multiple", company.business_territory_region
    )
    exit_value = exit_year.ebitda * ev_ebitda_multiple

    investment_amount = funding.capital_needed
    number_of_existing_shares = vc_assumptions.number_of_existing_shares

    post_money_valuation = exit_value / ((1 + hurdle_rate) ** time_to_exit)
    pre_money_valuation = post_money_valuation - investment_amount

    ownership_fraction_investors = investment_amount / post_money_valuation
    ownership_fraction_entrepreneurs = 1 - ownership_fraction_investors

    number_of_new_shares = number_of_existing_shares * (
        ownership_fraction_investors / (1 - ownership_fraction_investors)
    )
    price_per_share = investment_amount / number_of_new_shares

    final_wealth_investors = exit_value * ownership_fraction_investors
    final_wealth_entrepreneurs = exit_value * ownership_fraction_entrepreneurs

    return VCMethodResult(
        exit_year_revenue=exit_year.revenue,
        exit_year_ebitda=exit_year.ebitda,
        ev_ebitda_multiple=ev_ebitda_multiple,
        exit_value=exit_value,
        time_to_exit=time_to_exit,
        hurdle_rate=hurdle_rate,
        investment_amount=investment_amount,
        number_of_existing_shares=number_of_existing_shares,
        post_money_valuation=post_money_valuation,
        pre_money_valuation=pre_money_valuation,
        ownership_fraction_investors=ownership_fraction_investors,
        ownership_fraction_entrepreneurs=ownership_fraction_entrepreneurs,
        number_of_new_shares=number_of_new_shares,
        price_per_share=price_per_share,
        final_wealth_investors=final_wealth_investors,
        final_wealth_entrepreneurs=final_wealth_entrepreneurs,
    )


# ============================================================================
# SECTION 7 — DCF Multiples method (a.k.a. Comparables method)
#
# Uses Year-1 revenue and EBITDA (not a future exit year) x the industry
# EV/EBITDA multiple, then applies the stage risk multiplier as a haircut
# (no time discounting - this method is a snapshot, not a multi-year DCF).
# ============================================================================


@dataclass
class DCFMultiplesResult:
    revenue: float
    ebitda: float
    ev_ebitda_multiple: float
    exit_value: float
    risk_multiplier: float
    pre_money_valuation: float


def compute_dcf_multiples(
    company: CompanyProfile,
    projections: FinancialProjections,
) -> DCFMultiplesResult:
    year1 = projections.years[0]

    ev_ebitda_multiple = get_industry_metric(
        company.industry, "ev_ebitda_multiple", company.business_territory_region
    )
    exit_value = year1.ebitda * ev_ebitda_multiple

    risk_multiplier = get_stage_params(company.company_stage)["risk_multiplier"]
    pre_money_valuation = exit_value * risk_multiplier

    return DCFMultiplesResult(
        revenue=year1.revenue,
        ebitda=year1.ebitda,
        ev_ebitda_multiple=ev_ebitda_multiple,
        exit_value=exit_value,
        risk_multiplier=risk_multiplier,
        pre_money_valuation=pre_money_valuation,
    )


# ============================================================================
# SECTION 8 — DCF Method
#
# 5-year unlevered free cash flow + Gordon Growth terminal value.
#
# This is the CORRECTED version: the terminal value is discounted back to
# present value (both for the base discount-rate-only column and the
# hurdle-adjusted column). The original workbook computed the terminal
# value as of the exit date but never divided by (1+r)^5, overstating the
# DCF Method's enterprise value by roughly 4-8x - see docs/ for the full
# writeup.
# ============================================================================

PERPETUAL_GROWTH_RATE = 0.02


@dataclass
class DCFColumnResult:
    pv_of_fcf: float
    pv_of_terminal_value: float
    enterprise_value: float


@dataclass
class DCFResult:
    tax_rate: float
    discount_rate: float
    perpetual_growth_rate: float
    hurdle_rate: float
    unlevered_fcf_by_year: list[float]
    base: DCFColumnResult          # discounted at `discount_rate` only
    hurdle_adjusted: DCFColumnResult  # discounted at `discount_rate + hurdle_rate` - this feeds the blended valuation


def _npv(rate: float, cashflows: list[float]) -> float:
    """Excel NPV() equivalent: discounts cashflows[0] as period 1, cashflows[1] as period 2, etc."""
    return sum(cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cashflows))


def compute_dcf(
    company: CompanyProfile,
    projections: FinancialProjections,
    wacc_result: WaccResult,
) -> DCFResult:
    tax_rate = company.dcf_tax_rate_override
    discount_rate = wacc_result.wacc
    hurdle_rate = get_stage_params(company.company_stage)["hurdle_rate"]
    g = PERPETUAL_GROWTH_RATE
    n = len(projections.years)  # 5

    unlevered_fcf = []
    for y in projections.years:
        taxes = y.ebit * tax_rate
        fcf = y.ebit - taxes + y.da - y.capex - y.change_in_working_capital
        unlevered_fcf.append(fcf)

    terminal_year_fcf = unlevered_fcf[-1]

    # --- base column: discount rate only ---
    pv_fcf_base = _npv(discount_rate, unlevered_fcf)
    tv_base_at_exit = terminal_year_fcf * (1 + g) / (discount_rate - g)
    pv_tv_base = tv_base_at_exit / (1 + discount_rate) ** n
    ev_base = pv_fcf_base + pv_tv_base

    # --- hurdle-adjusted column: discount rate + hurdle rate ---
    r_adj = discount_rate + hurdle_rate
    pv_fcf_adj = _npv(r_adj, unlevered_fcf)
    tv_adj_at_exit = terminal_year_fcf * (1 + g) / (r_adj - g)
    pv_tv_adj = tv_adj_at_exit / (1 + r_adj) ** n
    ev_adj = pv_fcf_adj + pv_tv_adj

    return DCFResult(
        tax_rate=tax_rate,
        discount_rate=discount_rate,
        perpetual_growth_rate=g,
        hurdle_rate=hurdle_rate,
        unlevered_fcf_by_year=unlevered_fcf,
        base=DCFColumnResult(pv_of_fcf=pv_fcf_base, pv_of_terminal_value=pv_tv_base, enterprise_value=ev_base),
        hurdle_adjusted=DCFColumnResult(pv_of_fcf=pv_fcf_adj, pv_of_terminal_value=pv_tv_adj, enterprise_value=ev_adj),
    )


# ============================================================================
# SECTION 9 — Orchestration
#
# Builds financial projections, computes WACC, runs all four valuation
# methods, and blends them by stage-based weights into a final pre-money /
# post-money valuation.
# ============================================================================


@dataclass
class MethodValue:
    pre_money_value: float
    weight: float
    weighted_value: float


@dataclass
class ValuationOutput:
    projections: FinancialProjections
    wacc: WaccResult
    scorecard: ScorecardResult
    venture_capital: VCMethodResult
    dcf_multiples: DCFMultiplesResult
    dcf: DCFResult

    method_values: dict[str, MethodValue]
    simple_average_valuation: float
    blended_pre_money_valuation: float
    capital_needed: float
    post_money_valuation: float


def run_valuation(inputs: ValuationInput) -> ValuationOutput:
    company = inputs.company_profile

    projections = build_projections(company, inputs.financial_assumptions)
    wacc_result = compute_wacc(company)

    scorecard_result = compute_scorecard(company, inputs.market_and_team_assessment)
    vc_result = compute_venture_capital(company, projections, inputs.funding, inputs.vc_assumptions)
    dcf_multiples_result = compute_dcf_multiples(company, projections)
    dcf_result = compute_dcf(company, projections, wacc_result)

    weights = get_stage_params(company.company_stage)["method_weights"]

    raw_values = {
        "scorecard": scorecard_result.pre_money_valuation,
        "venture_capital": vc_result.pre_money_valuation,
        "dcf_multiples": dcf_multiples_result.pre_money_valuation,
        "dcf": dcf_result.hurdle_adjusted.enterprise_value,
    }

    method_values = {}
    weighted_sum = 0.0
    weight_sum = 0.0
    for key, value in raw_values.items():
        w = weights[key]
        weighted = value * w
        method_values[key] = MethodValue(pre_money_value=value, weight=w, weighted_value=weighted)
        weighted_sum += weighted
        weight_sum += w

    simple_average_valuation = sum(raw_values.values()) / len(raw_values)
    blended_pre_money_valuation = weighted_sum / weight_sum

    capital_needed = inputs.funding.capital_needed
    post_money_valuation = blended_pre_money_valuation + capital_needed

    return ValuationOutput(
        projections=projections,
        wacc=wacc_result,
        scorecard=scorecard_result,
        venture_capital=vc_result,
        dcf_multiples=dcf_multiples_result,
        dcf=dcf_result,
        method_values=method_values,
        simple_average_valuation=simple_average_valuation,
        blended_pre_money_valuation=blended_pre_money_valuation,
        capital_needed=capital_needed,
        post_money_valuation=post_money_valuation,
    )


# ============================================================================
# SECTION 10 — Scenario / sensitivity analysis
#
# Re-runs the full valuation (all four methods, blended) at scaled Year-1
# revenue levels, so the effect of a revenue scenario is reflected exactly
# the way each method actually responds to it - Scorecard doesn't move at
# all (it never looks at revenue), Venture Capital and DCF move through the
# whole 5-year projection (growth rates, cost ratios, working capital all
# scale together), and DCF Multiples moves off Year 1 alone. This reuses
# run_valuation() itself rather than approximating each method separately,
# so scenario numbers are exactly as accurate as the base valuation.
# ============================================================================

SCENARIO_REVENUE_MULTIPLIERS = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3]


def scenario_label(multiplier: float) -> str:
    return f"{round(multiplier * 100)}%"


def run_valuation_scenarios(
    inputs: ValuationInput,
    multipliers: Optional[list[float]] = None,
) -> dict[str, ValuationOutput]:
    """
    Returns an ordered dict: scenario label (e.g. "80%") -> full ValuationOutput,
    for each revenue multiplier applied to Year 1 revenue (growth rates from
    Year 1 onward are kept as given, so the whole trajectory scales with it).
    """
    multipliers = multipliers if multipliers is not None else SCENARIO_REVENUE_MULTIPLIERS
    base_revenue = inputs.financial_assumptions.revenue_year1

    results: dict[str, ValuationOutput] = {}
    for m in multipliers:
        scaled_inputs = inputs.model_copy(deep=True)
        scaled_inputs.financial_assumptions.revenue_year1 = base_revenue * m
        results[scenario_label(m)] = run_valuation(scaled_inputs)
    return results
