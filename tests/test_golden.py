"""The golden case (Werkpuls GmbH, see werkpuls.py), run with pinned benchmarks and with live data.

The owner's expected values were worked out by hand from the original workbook's conventions.
Where the app follows a documented different convention (AUDIT_REPORT.md), the test checks the
same arithmetic on the same inputs instead, and QUESTIONS.md lists the difference.
"""
import math
from types import SimpleNamespace

import pytest

import valuation_engine as ve
from recompute import compare
from werkpuls import PINNED_RISK_MULTIPLIER, WERKPULS, case, pinned

EXPECTED_REVENUE = [750_000, 1_200_000, 1_740_000, 2_349_000, 2_936_250]
EXPECTED_EBITDA = [202_500, 324_000, 469_800, 634_230, 792_787.50]
EXPECTED_EBIT = [172_500, 276_000, 400_200, 540_270, 675_337.50]
EXPECTED_NWC = [45_000, 72_000, 104_400, 140_940, 176_175]
EXPECTED_DELTA_NWC_Y2_Y5 = [27_000, 32_400, 36_540, 35_235]
EXPECTED_FCF = [110_750, 164_200, 257_340, 365_609, 474_951.25]


def run(c):
    return ve.run_valuation(ve.ValuationInput(**c))


@pytest.fixture()
def pinned_run(monkeypatch):
    with pinned(monkeypatch):
        yield run


def test_pinned_projection_matches_hand_calculation(pinned_run):
    p = pinned_run(WERKPULS).projections
    assert [y.revenue for y in p.years] == pytest.approx(EXPECTED_REVENUE)
    assert [y.working_capital for y in p.years] == pytest.approx(EXPECTED_NWC)
    assert [y.change_in_working_capital for y in p.years][1:] == pytest.approx(EXPECTED_DELTA_NWC_Y2_Y5)
    # EBIT = EBITDA - D&A (4% of revenue), whatever the margin path.
    assert [y.ebit for y in p.years] == pytest.approx([y.ebitda - 0.04 * y.revenue for y in p.years])
    # Year 5 reaches the 27% benchmark margin.
    assert p.years[-1].ebitda == pytest.approx(EXPECTED_EBITDA[-1])
    assert p.years[-1].unlevered_fcf == pytest.approx(EXPECTED_FCF[-1])


def test_flat_benchmark_margin_reproduces_every_hand_figure(pinned_run):
    """With last-12-month EBITDA at the 27% benchmark margin, the margin is flat and every
    figure matches the hand calculation; only Year-1 change in working capital differs,
    because the app starts from last-12-month revenue instead of assuming zero."""
    p = pinned_run(case(operating_performance__current_ebitda=0.27 * 420_000)).projections
    assert [y.ebitda for y in p.years] == pytest.approx(EXPECTED_EBITDA)
    assert [y.ebit for y in p.years] == pytest.approx(EXPECTED_EBIT)
    assert p.years[0].change_in_working_capital == pytest.approx(45_000 - 0.06 * 420_000)
    assert [y.unlevered_fcf for y in p.years][1:] == pytest.approx(EXPECTED_FCF[1:])
    assert p.years[0].unlevered_fcf == pytest.approx(EXPECTED_FCF[0] - 19_800)


def test_dcf_arithmetic_with_hand_cash_flows():
    projections = SimpleNamespace(fcf=lambda: list(EXPECTED_FCF), terminal_fcf=EXPECTED_FCF[-1], tax_rate=0.3)
    company = ve.CompanyProfile(**WERKPULS["company_profile"])
    op = ve.OperatingPerformance(**WERKPULS["operating_performance"])
    d = ve.compute_dcf(company, projections, SimpleNamespace(wacc=0.14), op, debt=0)
    assert d.pv_of_fcf == pytest.approx(860_337.54, abs=0.01)
    assert d.terminal_value == pytest.approx(4_037_085.62, abs=0.01)
    # The terminal value is discounted to today (the original workbook didn't).
    assert d.pv_of_terminal_value == pytest.approx(2_096_735.77, abs=0.01)
    assert d.enterprise_value == pytest.approx(2_957_073.30, abs=0.01)


def test_pinned_dcf_discounts_terminal_value(pinned_run):
    d = pinned_run(WERKPULS).dcf
    assert d.terminal_value == pytest.approx(4_037_085.62, abs=0.01)
    assert d.pv_of_terminal_value == pytest.approx(d.terminal_value / 1.14 ** 5)
    assert d.enterprise_value == pytest.approx(d.pv_of_fcf + d.pv_of_terminal_value)


def test_vc_arithmetic_with_hand_inputs(monkeypatch):
    """Exit EBITDA 634,230 x 18, a 55% annual return, €800,000 raised, 25,000 existing shares."""
    projections = SimpleNamespace(years=[SimpleNamespace(year_label=f"Y{i + 1}", revenue=r, ebitda=e)
                                         for i, (r, e) in enumerate(zip(EXPECTED_REVENUE, EXPECTED_EBITDA))])
    stage = dict(ve.get_stage_params("Startup stage"), vc_target_return=PINNED_RISK_MULTIPLIER)
    monkeypatch.setattr(ve, "get_stage_params", lambda s: stage)
    bench = SimpleNamespace(get=lambda m: 18.0, used={"ev_ebitda_multiple": SimpleNamespace(source="region")})
    vc = ve.compute_venture_capital(
        ve.CompanyProfile(**WERKPULS["company_profile"]), projections,
        ve.FundingRequirement(**WERKPULS["funding"]), ve.VCMethodAssumptions(**WERKPULS["vc_assumptions"]),
        bench, debt=0)
    assert vc.exit_value == pytest.approx(11_416_140.00)
    assert vc.post_money_valuation == pytest.approx(1_977_846.09, abs=0.01)
    assert vc.pre_money_valuation == pytest.approx(1_177_846.09, abs=0.01)
    assert round(vc.ownership_fraction_investors, 4) == 0.4045
    assert round(vc.number_of_new_shares) == 16_980
    assert round(vc.price_per_share, 2) == 47.11


def test_pinned_vc_uses_the_entered_share_count(pinned_run):
    vc = pinned_run(WERKPULS).venture_capital
    assert vc.number_of_existing_shares == 25_000
    f = vc.ownership_fraction_investors
    assert vc.number_of_new_shares == pytest.approx(25_000 * f / (1 - f))
    assert vc.price_per_share == pytest.approx(800_000 / vc.number_of_new_shares)


def test_multiple_arithmetic_with_hand_inputs(monkeypatch):
    """EBITDA 202,500 x 18 x 0.55 = 2,004,750 (the app applies the multiple to trailing EBITDA)."""
    stage = dict(ve.get_stage_params("Startup stage"), private_company_discount=1 - PINNED_RISK_MULTIPLIER)
    monkeypatch.setattr(ve, "get_stage_params", lambda s: stage)
    bench = SimpleNamespace(get=lambda m: 18.0, used={"ev_ebitda_multiple": SimpleNamespace(source="region")})
    op = ve.OperatingPerformance(current_revenue_last_12_months=750_000, current_ebitda=202_500)
    cm = ve.compute_comparables(ve.CompanyProfile(**WERKPULS["company_profile"]), op, 0, bench)
    assert cm.public_company_ev == pytest.approx(3_645_000.00)
    assert cm.enterprise_value == pytest.approx(2_004_750.00)
    for share, expected in zip([0.8, 0.9, 1.1, 1.2, 1.3], [1_603_800, 1_804_275, 2_205_225, 2_405_700, 2_606_175]):
        op = ve.OperatingPerformance(current_revenue_last_12_months=750_000 * share, current_ebitda=202_500 * share)
        assert ve.compute_comparables(ve.CompanyProfile(**WERKPULS["company_profile"]), op, 0, bench) \
            .enterprise_value == pytest.approx(expected)


def test_scorecard_adds_up_with_german_benchmark():
    c = case(company_profile__benchmark_pre_money_override=6_550_000)
    sc = run(c).scorecard
    scores = {k: v.score for k, v in sc.criteria.items()}
    # Unanswered questions (step aside for a CEO, development stage, copying, more rounds) count as 1.0.
    assert scores == pytest.approx({
        "strength_of_the_team": (1.1 + 1.0 + 1.0) / 3,
        "size_of_the_opportunity": (0.8 + 0.8) / 2,
        "competitive_environment": (0.9 + 0.7) / 2,
        "strength_and_protection_of_product": (1.0 + 1.0 + 1.0) / 3,
        "strategic_relationships_with_partners": (1.0 + 0.7) / 2,
        "funding_required": 1.0,
    })
    total = sum(ve.SCORECARD_CRITERIA_WEIGHTS[k] * s for k, s in scores.items())
    assert sc.pre_money_valuation == pytest.approx(total * 6_550_000)
    assert sum(v.amount_assigned for v in sc.criteria.values()) == pytest.approx(sc.pre_money_valuation)


@pytest.mark.parametrize("mode", ["pinned", "live"])
def test_blend_is_weighted_sum(mode, monkeypatch):
    if mode == "pinned":
        with pinned(monkeypatch):
            r = run(WERKPULS)
    else:
        r = run(WERKPULS)
    blend = sum(mv.pre_money_value * mv.weight_used for mv in r.method_values.values() if mv.status == "ok")
    assert r.blended_pre_money_valuation == pytest.approx(blend)
    assert r.post_money_valuation == pytest.approx(blend + 800_000)
    assert sum(mv.weight_used for mv in r.method_values.values()) == pytest.approx(1.0)


def test_live_run_matches_independent_recompute_and_is_plausible():
    assert not [row for row in compare(WERKPULS) if not row[3]]
    r = run(WERKPULS)
    for mv in r.method_values.values():
        if mv.pre_money_value is not None:
            assert math.isfinite(mv.pre_money_value) and mv.pre_money_value >= 0
    assert 0 < r.blended_pre_money_valuation < 100_000_000
    # Negative last-12-month EBITDA: the multiple method is left out, never negative.
    assert r.method_values["comparables"].status == "not_meaningful"
    codes = {w.code for w in r.warnings}
    # The inputs balance, so none of the input-check warnings appear.
    assert not codes & {"ownership_sum", "use_of_funds_sum", "revenue_jump", "high_growth"}


def test_pinned_werkpuls_final_values(pinned_run):
    """The owner's accepted results for Werkpuls (QUESTIONS.md 1-6 decided 7 October 2026: keep the app's
    method). Worked by hand: DCF 2,340,541.08 x 50% survival - 100,000 debt + 310,000 cash; VC exit
    417,898.29 x 18 - 100,000 debt, / 1.5^4, - 800,000; blend 6/13 VC + 7/13 DCF (Comparables left out)."""
    r = pinned_run(WERKPULS)
    assert r.dcf.enterprise_value == pytest.approx(2_340_541.08, abs=0.01)
    assert r.dcf.pre_money_valuation == pytest.approx(1_380_270.54, abs=0.01)
    assert r.venture_capital.exit_value == pytest.approx(7_522_169.14, abs=0.01)
    assert r.venture_capital.pre_money_valuation == pytest.approx(666_107.49, abs=0.01)
    assert r.method_values["comparables"].status == "not_meaningful"
    assert r.method_values["scorecard"].status == "not_used"
    assert r.method_values["venture_capital"].weight_used == pytest.approx(6 / 13)
    assert r.blended_pre_money_valuation == pytest.approx(1_050_656.82, abs=0.01)
    assert r.post_money_valuation == pytest.approx(1_850_656.82, abs=0.01)
