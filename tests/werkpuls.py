"""The "golden case": Werkpuls GmbH, a made-up German SaaS company used only for testing.

WERKPULS is the wizard input. PINNED_BENCHMARKS are the fixed benchmark figures for the
exact-number test (the live Damodaran figures change over time); `pinned()` swaps them in.
"""
import contextlib
import copy
from datetime import date

import valuation_engine as ve

VALUATION_DATE = date(2026, 10, 7)

WERKPULS = {
    "company_profile": {
        "company_name": "Werkpuls GmbH",
        "contact_name": "Lena Hartmann",
        "contact_email": "lena@werkpuls.de",
        "address": "Fürther Straße 212, 90429 Nürnberg",
        "country": "Germany",
        "website": "www.werkpuls.de",
        "num_founders": 3,
        "num_employees": 9,
        "year_of_incorporation": 2023,
        "company_stage": "Startup stage",
        "committed_capital": 250_000,
        "business_activity": "Energy-monitoring software for manufacturers",
        "industry": "Software (System & Application)",
        "business_territory_region": ve.EUROPE_REGION,
        "business_model": "SaaS (annual subscriptions)",
        "exit_strategy": "Trade sale to an industrial automation company",
        "planned_time_to_exit_years": 4,
        "dcf_tax_rate_override": 0.30,
        "valuation_date": VALUATION_DATE.isoformat(),
    },
    "market_and_team_assessment": {
        "strength_of_competitors_in_market": "Dominated by several players",
        "strength_of_competitive_products": "Competitive products are excellent and superior",
        "target_market_size": "< $50 million",
        "revenue_potential_in_5_years": "< $20 Million",
        "sales_channels_partners": "Channels secure, customers placed trial orders",
        "marketing_partners": "No partners identified",
        "product_compelling_to_customers": "This product is novel and sought after",
        "management_team_experience": "Demonstrated experience as a COO, CFO or CTO",
        "management_team_completeness": "Team identified and on the sideline",
    },
    "operating_performance": {
        "current_revenue_last_12_months": 420_000,
        "current_ebitda": -80_000,
        "cash_available": 310_000,
        "current_ppe_value": 45_000,
    },
    "financial_assumptions": {
        "revenue_year1": 750_000,
        "revenue_growth_rates": [0.60, 0.45, 0.35, 0.25],
        "capex_by_year": [40_000, 50_000, 60_000, 70_000, 80_000],
        "existing_debt_balance": 100_000,
        "target_ebitda_margin_override": None,
    },
    "ownership": [
        {"name": "Lena Hartmann", "ownership_pct": 0.30},
        {"name": "Jonas Weber", "ownership_pct": 0.30},
        {"name": "Aylin Demir", "ownership_pct": 0.25},
        {"name": "Business angel (Frankenland Angels)", "ownership_pct": 0.10},
        {"name": "Employee option pool", "ownership_pct": 0.05},
    ],
    "funding": {
        "capital_needed": 800_000,
        "use_of_funds": {
            "Product & R&D": 300_000,
            "Sales & marketing": 300_000,
            "Inventory": 0,
            "Operations": 100_000,
            "Capital expenditures": 50_000,
            "Others": 50_000,
        },
    },
    "vc_assumptions": {"number_of_existing_shares": 25_000},
}

# Owner's pinned figures. COGS 25% + SG&A 45% + other opex 3% leave a 27% EBITDA margin.
PINNED_BENCHMARKS = {
    "ev_ebitda_multiple": 18.0,
    "ebitda_margin": 1 - 0.25 - 0.45 - 0.03,
    "da_pct_revenue": 0.04,
    "acc_receivable_pct_revenue": 0.12,
    "inventory_pct_revenue": 0.0,
    "acc_payable_pct_revenue": 0.06,
    # Not in the owner's figures: the EV/Sales that the pinned multiple and margin imply (18 x 27%), the same
    # identity the app uses to derive EV/Sales from Damodaran's data.
    "ev_sales_multiple": 18.0 * (1 - 0.25 - 0.45 - 0.03),
}
PINNED_DISCOUNT_RATE = 0.14
PINNED_GROWTH = 0.02
PINNED_RISK_MULTIPLIER = 0.55


def case(**changes):
    """A deep copy of WERKPULS with `section__field=value` changes."""
    c = copy.deepcopy(WERKPULS)
    for path, value in changes.items():
        section, key = path.split("__", 1)
        c[section][key] = value
    return c


@contextlib.contextmanager
def pinned(monkeypatch):
    """Benchmarks, discount rate and growth fixed to the owner's pinned figures."""
    real = ve.get_industry_metric_with_source

    def lookup(industry, metric, region):
        if metric in PINNED_BENCHMARKS:
            return PINNED_BENCHMARKS[metric], "region"
        return real(industry, metric, region)

    real_wacc = ve.compute_wacc

    def wacc(company, bench=None):
        w = real_wacc(company, bench)
        w.wacc = PINNED_DISCOUNT_RATE
        return w

    monkeypatch.setattr(ve, "get_industry_metric_with_source", lookup)
    monkeypatch.setattr(ve, "compute_wacc", wacc)
    params = copy.deepcopy(ve.market_parameters())
    params["perpetual_growth_rate"] = PINNED_GROWTH
    monkeypatch.setattr(ve, "market_parameters", lambda: params)
    yield
