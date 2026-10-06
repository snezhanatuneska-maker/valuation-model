"""Engine tests: every figure is checked against an independent recomputation
(audit/recompute.py), plus the rules the methodology promises."""
import copy

import pytest
from pydantic import ValidationError

import valuation_engine as ve
from recompute import EDGE_CASES, REFERENCE_CASE, compare, sweep, variant


def run(case):
    return ve.run_valuation(ve.ValuationInput(**case))


def test_reference_case_matches_independent_recompute():
    bad = [(name, a, b) for name, a, b, ok in compare(REFERENCE_CASE) if not ok]
    assert not bad


@pytest.mark.parametrize("name", list(EDGE_CASES))
def test_edge_case_matches_independent_recompute(name):
    case = EDGE_CASES[name]
    try:
        rows = compare(case)
    except ve.ValuationError:
        return  # a clear "no method applies" message is an accepted outcome
    assert not [(n, a, b) for n, a, b, ok in rows if not ok]


def test_every_stage_industry_region_runs():
    assert sweep() == []


def test_reference_case_regression_values():
    """Pinned values (README "Verified numbers"); update deliberately if the method changes."""
    r = run(REFERENCE_CASE)
    got = {k: round(v.pre_money_value) for k, v in r.method_values.items()}
    assert got == {"scorecard": 2_202_240, "venture_capital": 1_242_335, "comparables": 697_925, "dcf": 841_159}
    assert round(r.blended_pre_money_valuation) == 911_380
    assert round(r.post_money_valuation) == 1_211_380


def test_scorecard_rows_add_up_to_total():
    sc = run(REFERENCE_CASE).scorecard
    assert sum(c.amount_assigned for c in sc.criteria.values()) == pytest.approx(sc.pre_money_valuation)


def test_weights_used_add_up_to_one():
    for case in [REFERENCE_CASE, *EDGE_CASES.values()]:
        try:
            r = run(case)
        except ve.ValuationError:
            continue
        assert sum(mv.weight_used for mv in r.method_values.values()) == pytest.approx(1.0)


def test_vc_target_return_falls_as_company_matures():
    returns = [p["vc_target_return"] for p in ve.stage_parameters().values()]
    assert returns == sorted(returns, reverse=True)
    survival = [p["survival_probability"] for p in ve.stage_parameters().values()]
    assert survival == sorted(survival)


def test_dcf_discounts_at_wacc_only():
    r = run(REFERENCE_CASE)
    assert r.dcf.discount_rate == r.wacc.wacc
    assert r.dcf.risk_adjusted_enterprise_value == pytest.approx(r.dcf.enterprise_value * r.dcf.survival_probability)


def test_comparables_uses_trailing_ebitda():
    r = run(REFERENCE_CASE)
    assert r.comparables.trailing_ebitda == REFERENCE_CASE["operating_performance"]["current_ebitda"]


def test_debt_and_cash_bridge():
    base = run(REFERENCE_CASE)
    indebted = run(variant(financial_assumptions__existing_debt_balance=100_000))
    for key in ("comparables", "dcf"):
        assert base.method_values[key].pre_money_value - indebted.method_values[key].pre_money_value == pytest.approx(100_000)
    assert indebted.method_values["venture_capital"].pre_money_value < base.method_values["venture_capital"].pre_money_value


def test_equity_never_negative_when_debt_is_huge():
    r = run(variant(financial_assumptions__existing_debt_balance=50_000_000))
    for mv in r.method_values.values():
        assert mv.pre_money_value is None or mv.pre_money_value >= 0
    assert any(w.code.startswith("zero_") for w in r.warnings)


def test_loss_making_company_drops_comparables_and_reweights():
    r = run(variant(operating_performance__current_ebitda=-80_000))
    assert r.method_values["comparables"].status == "not_meaningful"
    assert r.method_values["comparables"].weight_used == 0
    assert any(w.code == "nm_comparables" for w in r.warnings)


def test_projection_starts_from_company_margin_and_reaches_target():
    r = run(REFERENCE_CASE)
    p = r.projections
    assert p.starting_ebitda_margin == pytest.approx(50_000 / 300_000)
    assert p.years[-1].ebitda_margin == pytest.approx(p.target_ebitda_margin)
    override = run(variant(financial_assumptions__target_ebitda_margin_override=0.30))
    assert override.projections.years[-1].ebitda_margin == pytest.approx(0.30)


def test_industry_margin_includes_rnd():
    """The target margin is Damodaran's EBITDA/Sales, not 1 - COGS - SG&A (which leaves out R&D)."""
    ind, reg = "Software (Entertainment)", "US"
    b = ve.industry_benchmarks()[ind]
    r = run(variant(company_profile__industry=ind, company_profile__country="United States",
                    company_profile__business_territory_region=reg))
    assert r.projections.target_ebitda_margin == pytest.approx(b["ebitda_margin"][reg])
    assert r.projections.target_ebitda_margin < 1 - b["cogs_pct_revenue"][reg] - b["sga_pct_revenue"][reg] - 0.1


def test_single_tax_rate_defaults_to_country():
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["dcf_tax_rate_override"] = None
    r = run(case)
    assert r.projections.tax_rate == ve.get_country("Tanzania")["corporate_tax_rate"]
    assert r.wacc.tax_rate == r.projections.tax_rate


def test_losses_are_carried_forward_not_refunded():
    r = run(variant(operating_performance__current_ebitda=-200_000))
    assert all(y.tax_on_ebit >= 0 for y in r.projections.years)


def test_valuation_date_drives_year_labels():
    r = run(REFERENCE_CASE)
    assert r.valuation_date == "2026-10-06"
    assert r.projections.years[0].period_label == "to Oct 2027"


def test_old_answer_wording_still_scores():
    case = copy.deepcopy(REFERENCE_CASE)
    case["market_and_team_assessment"]["willingness_to_step_aside_for_ceo"] = "Unwilling (*Deal braker)"
    case["market_and_team_assessment"]["target_market_size"] = "< $50 million "
    assert run(case).blended_pre_money_valuation == pytest.approx(run(REFERENCE_CASE).blended_pre_money_valuation)


def test_unknown_answer_is_rejected():
    case = copy.deepcopy(REFERENCE_CASE)
    case["market_and_team_assessment"]["marketing_partners"] = "Something else"
    with pytest.raises(KeyError):
        run(case)


@pytest.mark.parametrize("path,value", [
    ("financial_assumptions__revenue_year1", 0),
    ("funding__capital_needed", 0),
    ("company_profile__planned_time_to_exit_years", 6),
    ("company_profile__dcf_tax_rate_override", 0.9),
    ("financial_assumptions__capex_by_year", [0, -1, 0, 0, 0]),
    ("financial_assumptions__revenue_growth_rates", [-1.0, 0.1, 0.1, 0.1]),
    ("operating_performance__cash_available", -5),
])
def test_invalid_inputs_are_rejected(path, value):
    with pytest.raises(ValidationError):
        ve.ValuationInput(**variant(**{path: value}))


def test_banks_use_scorecard_only():
    r = run(variant(company_profile__industry="Bank (Money Center)", company_profile__company_stage="Development stage"))
    assert [k for k, mv in r.method_values.items() if mv.status == "ok"] == ["scorecard"]
    assert any(w.code == "financial_sector" for w in r.warnings)
    with pytest.raises(ve.ValuationError, match="Banks and insurers"):
        run(EDGE_CASES["Bank (Money Center) / US"])  # Startup stage: no method applies


def test_scorecard_not_used_once_company_has_revenue():
    r = run(REFERENCE_CASE)  # Startup stage
    assert r.method_values["scorecard"].status == "not_used"
    assert r.method_values["scorecard"].weight_used == 0


def test_scorecard_uses_regional_equidam_benchmark():
    r = run(variant(company_profile__company_stage="Idea stage"))
    assert r.scorecard.benchmark_pre_money_valuation == ve.scorecard_benchmarks()[
        REFERENCE_CASE["company_profile"]["business_territory_region"]]["eur"]
    assert r.scorecard.benchmark_basis.startswith("Average of the Equidam H1 2026")


def test_no_applicable_method_gives_clear_error():
    case = variant(operating_performance__current_ebitda=-500_000,            # Comparables: no positive EBITDA
                   funding__capital_needed=50_000_000,                        # VC: raise exceeds what the exit supports
                   financial_assumptions__target_ebitda_margin_override=-0.5)  # DCF: negative cash flows
    with pytest.raises(ve.ValuationError, match="None of the methods"):
        run(case)


def test_reference_case_warnings():
    codes = {w.code for w in run(REFERENCE_CASE).warnings}
    assert {"revenue_jump", "ppe_scale", "ownership_sum", "use_of_funds_sum", "capex_mismatch"} <= codes


def test_scenarios_scale_revenue():
    s = ve.run_valuation_scenarios(ve.ValuationInput(**REFERENCE_CASE))
    assert list(s) == ["80%", "90%", "100%", "110%", "120%", "130%"]
    vc = [s[k].method_values["venture_capital"].pre_money_value for k in s]
    assert vc == sorted(vc)
