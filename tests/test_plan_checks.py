"""The plan is checked against the founder's own answers and the industry's margin."""
import pytest

import valuation_engine as ve
from werkpuls import WERKPULS, case


def notes(c):
    return {w.code: w for w in ve.run_valuation(ve.ValuationInput(**c)).warnings}


def test_werkpuls_plan_is_consistent():
    assert not {"plan_above_revenue_potential", "plan_above_market", "target_margin_high"} & notes(WERKPULS).keys()


def test_year5_revenue_above_own_revenue_potential_answer():
    # Year 1 €10M growing 60/45/35/25% gives Year 5 about €39M, above "< $20 Million" (about €17.6M).
    n = notes(case(financial_assumptions__revenue_year1=10_000_000,
                   operating_performance__current_revenue_last_12_months=6_000_000,
                   market_and_team_assessment__target_market_size="> $100 million"))
    w = n["plan_above_revenue_potential"]
    assert w.severity == "warning"
    assert "€39,150,000" in w.message and "< $20 Million" in w.message and "about €17,570,000" in w.message


def test_year5_revenue_above_whole_market():
    n = notes(case(financial_assumptions__revenue_year1=15_000_000,
                   operating_performance__current_revenue_last_12_months=9_000_000,
                   market_and_team_assessment__revenue_potential_in_5_years=
                   "> $100 Million (may require significant additional funding)"))
    w = n["plan_above_market"]
    assert "< $50 million" in w.message and "more than the whole market" in w.message
    assert "plan_above_revenue_potential" not in n


def test_unanswered_questions_are_not_checked():
    c = case(financial_assumptions__revenue_year1=15_000_000,
             operating_performance__current_revenue_last_12_months=9_000_000)
    c["market_and_team_assessment"] = {}
    assert not {"plan_above_revenue_potential", "plan_above_market"} & notes(c).keys()


@pytest.mark.parametrize("target,flagged", [(0.40, True), (0.27, False)])
def test_own_target_margin_far_above_industry(target, flagged):
    n = notes(case(financial_assumptions__target_ebitda_margin_override=target))
    assert ("target_margin_high" in n) == flagged
    if flagged:
        assert "40.0%" in n["target_margin_high"].message and "17.2%" in n["target_margin_high"].message
