"""Engine tests: every figure is checked against an independent recomputation
(audit/recompute.py), plus the rules the methodology promises."""
import copy
import re

import pytest
from pydantic import ValidationError

import valuation_engine as ve
from recompute import EDGE_CASES, GERMAN_CASE, REFERENCE_CASE, compare, sweep, variant


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
    assert got == {"scorecard": 2_202_240, "venture_capital": 1_242_335, "comparables": 697_925, "dcf": 828_766}
    assert round(r.blended_pre_money_valuation) == 907_043
    assert round(r.post_money_valuation) == 1_207_043


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


def test_equity_never_negative_when_debt_is_large():
    r = run(variant(financial_assumptions__existing_debt_balance=2_000_000))
    for mv in r.method_values.values():
        assert mv.pre_money_value is None or mv.pre_money_value >= 0
    assert any(w.code.startswith("zero_") for w in r.warnings)


def test_debt_above_every_value_is_a_clear_message_not_a_zero_valuation():
    with pytest.raises(ve.ValuationError, match="debt"):
        run(variant(financial_assumptions__existing_debt_balance=50_000_000))


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
    with pytest.raises(ve.ValuationError, match="None of the methods for this stage"):
        run(case)


def test_reference_case_warnings():
    codes = {w.code for w in run(REFERENCE_CASE).warnings}
    assert {"revenue_jump", "ppe_scale", "ownership_sum", "use_of_funds_sum", "capex_mismatch"} <= codes


def test_scenarios_scale_revenue():
    s = ve.run_valuation_scenarios(ve.ValuationInput(**REFERENCE_CASE))
    assert list(s) == ["80%", "90%", "100%", "110%", "120%", "130%"]
    vc = [s[k].method_values["venture_capital"].pre_money_value for k in s]
    assert vc == sorted(vc)


# ---------------------------------------------------------------------------
# Germany: Bund rate, stepped tax schedule, zero country premium, own Scorecard table
# ---------------------------------------------------------------------------
GERMAN_TAX = ve.country_specific("Germany")["tax"]


def german(**changes):
    case = copy.deepcopy(GERMAN_CASE)
    for path, v in changes.items():
        section, key = path.split("__")
        case[section][key] = v
    return case


def test_german_example_matches_independent_recompute():
    assert not [(n, a, b) for n, a, b, ok in compare(GERMAN_CASE) if not ok]


def test_german_example_regression_values():
    """Pinned values for the German example (README); update deliberately if data or method change."""
    r = run(GERMAN_CASE)
    got = {k: round(v.pre_money_value) for k, v in r.method_values.items()}
    assert got == {"scorecard": 2_696_070, "venture_capital": 971_446, "comparables": 749_919, "dcf": 1_327_405}
    assert round(r.blended_pre_money_valuation) == 1_018_497
    assert round(r.post_money_valuation) == 1_318_497


def test_germany_has_no_country_risk_premium():
    de = ve.get_country("Germany")
    assert de["moodys_rating"] == "Aaa" and de["country_risk_premium"] == 0
    assert de["equity_risk_premium"] == ve.market_parameters()["mature_market_premium"]
    assert run(GERMAN_CASE).wacc.country_equity_risk_premium == ve.market_parameters()["mature_market_premium"]


def test_germany_uses_bund_rate_others_keep_default():
    bund = ve.country_specific("Germany")["risk_free_rate"]
    w = run(GERMAN_CASE).wacc
    assert w.risk_free_rate == bund["value"] and "Bund" in w.risk_free_rate_source
    assert run(REFERENCE_CASE).wacc.risk_free_rate == ve.market_parameters()["risk_free_rate"]


def combined(kst, hebesatz=GERMAN_TAX["average_hebesatz"]):
    return kst * (1 + GERMAN_TAX["solidarity_surcharge"]) + GERMAN_TAX["trade_tax_base_rate"] * hebesatz


def test_german_tax_steps_down_year_by_year():
    r = run(GERMAN_CASE)  # valuation date 6 Oct 2026
    rates = [y.tax_rate for y in r.projections.years]
    assert rates[0] == pytest.approx(combined(0.15))  # Oct 2026 - Oct 2027: 15% throughout
    assert rates == sorted(rates, reverse=True) and rates[1] < rates[0]
    assert r.projections.tax_rate == pytest.approx(combined(0.10))  # terminal value and WACC use the 2032+ rate
    assert r.wacc.tax_rate == r.projections.tax_rate == r.dcf.tax_rate
    assert r.dcf.terminal_fcf > r.projections.years[-1].unlevered_fcf  # lower long-run tax


def test_german_tax_year_aligned_with_calendar_year():
    r = run(german(company_profile__valuation_date="2028-01-01"))
    assert [y.tax_rate for y in r.projections.years] == pytest.approx(
        [combined(k) for k in (0.14, 0.13, 0.12, 0.11, 0.10)])


def test_german_hebesatz_input():
    munich = run(german(company_profile__trade_tax_hebesatz=490))
    assert munich.projections.tax_rate == pytest.approx(combined(0.10, 4.90))
    assert munich.projections.tax_schedule.hebesatz_source == "user"
    with pytest.raises(ValidationError):  # legal minimum is 200%
        ve.ValuationInput(**german(company_profile__trade_tax_hebesatz=150))


def test_user_tax_rate_still_overrides_german_schedule():
    r = run(german(company_profile__dcf_tax_rate_override=0.25))
    assert {y.tax_rate for y in r.projections.years} == {0.25} and r.projections.tax_rate == 0.25
    y5 = r.projections.years[-1]
    # flat rate: Year-5 cash flow, with working capital growing at the long-run rate and capex at least D&A
    assert r.dcf.terminal_fcf == pytest.approx(y5.unlevered_fcf + y5.change_in_working_capital + y5.capex
                                               - max(y5.capex, y5.da) - y5.working_capital * r.dcf.perpetual_growth_rate)


def test_hebesatz_ignored_outside_germany():
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["trade_tax_hebesatz"] = 490
    assert run(case).blended_pre_money_valuation == run(REFERENCE_CASE).blended_pre_money_valuation


def test_german_scorecard_table_with_to_be_sourced_flag():
    r = run(german(company_profile__company_stage="Idea stage"))
    row = ve.country_specific("Germany")["stage_benchmarks"]["stages"]["Idea stage"]
    assert r.scorecard.benchmark_source == "country_table"
    assert r.scorecard.benchmark_pre_money_valuation == row["eur"]
    assert r.scorecard.benchmark_to_be_sourced == row["to_be_sourced"]
    if row["to_be_sourced"]:
        assert any(w.code == "benchmark_to_be_sourced" for w in r.warnings)
    # Other European countries keep the regional table
    fr = run(german(company_profile__company_stage="Idea stage", company_profile__country="France"))
    assert fr.scorecard.benchmark_source == "table"


def test_every_german_figure_has_a_source_and_date():
    de = ve.country_specific("Germany")
    assert de["risk_free_rate"]["source"] and de["risk_free_rate"]["as_of"]
    for key in ("corporate_tax_source", "solidarity_surcharge_source", "trade_tax_base_rate_source",
                "average_hebesatz_source", "average_hebesatz_as_of"):
        assert GERMAN_TAX[key], key
    assert de["stage_benchmarks"]["source"] and de["stage_benchmarks"]["as_of"]


def test_old_industry_and_answer_spellings_still_work():
    case = copy.deepcopy(GERMAN_CASE)
    case["company_profile"]["industry"] = "Heathcare Information and Technology"  # Damodaran's spelling
    case["market_and_team_assessment"]["product_can_be_duplicated"] = (
        "It's difficult to be copied (time and financials resources needed)")
    r = ve.ValuationInput(**case)
    assert r.company_profile.industry == "Healthcare Information and Technology"
    assert run(case).blended_pre_money_valuation > 0


def test_old_country_names_still_work():
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["country"] = "Swaziland"
    assert ve.ValuationInput(**case).company_profile.country == "Eswatini"
    assert run(case).blended_pre_money_valuation > 0


def test_old_stage_spelling_still_works():
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["company_stage"] = "Growth Stage"
    assert ve.ValuationInput(**case).company_profile.company_stage == "Growth stage"
    assert run(case).blended_pre_money_valuation > 0


def test_one_unanswered_scorecard_question_reads_correctly():
    case = german(company_profile__company_stage="Idea stage")
    case["market_and_team_assessment"].pop("target_market_size")
    msg = next(w.message for w in run(case).warnings if w.code == "scorecard_unanswered")
    assert msg.startswith("1 of the 13 Scorecard questions was not answered and is scored")


def codes(case):
    return {w.code for w in run(case).warnings}


def test_stage_and_revenue_mismatch_is_flagged():
    assert "stage_revenue" in codes(german(company_profile__company_stage="Idea stage"))  # €300k revenue
    assert "stage_no_revenue" in codes(german(operating_performance__current_revenue_last_12_months=0,
                                              operating_performance__current_ebitda=-50_000))
    assert not {"stage_revenue", "stage_no_revenue"} & codes(GERMAN_CASE)


def test_large_investor_stake_is_flagged():
    assert "investor_stake" in codes(german(funding__capital_needed=2_000_000))
    assert "investor_stake" not in codes(GERMAN_CASE)


def test_implausible_dates_are_flagged():
    assert "valuation_date" in codes(german(company_profile__valuation_date="1990-01-01"))
    assert "incorporation_year" in codes(german(company_profile__year_of_incorporation=2030))


def test_unanswered_scorecard_questions_count_as_typical():
    case = german(company_profile__company_stage="Idea stage")
    full = run(case).scorecard
    case["market_and_team_assessment"] = {"management_team_experience": "Demonstrated experience as a CEO"}
    r = run(case)
    assert len(r.scorecard.unanswered) == 12
    assert any(w.code == "scorecard_unanswered" for w in r.warnings)
    team = r.scorecard.criteria["strength_of_the_team"]
    assert team.score == pytest.approx((1.2 + 1 + 1) / 3)
    assert full.unanswered == []
    # Companies with revenue don't need to answer at all, and get no Scorecard warning.
    case = german()
    case["market_and_team_assessment"] = {}
    r = run(case)
    assert r.blended_pre_money_valuation == pytest.approx(run(GERMAN_CASE).blended_pre_money_valuation)
    assert "scorecard_unanswered" not in {w.code for w in r.warnings}



# A pre-revenue company whose VC value disappears at lower revenue (the case behind the
# "slider runs backwards" report).
SLIDER_CASE = german(company_profile__company_stage="Development stage",
                     company_profile__industry="Healthcare Products",
                     operating_performance__current_revenue_last_12_months=0,
                     operating_performance__current_ebitda=-120_000,
                     financial_assumptions__revenue_year1=200_000,
                     financial_assumptions__revenue_growth_rates=[1.2, 0.8, 0.5, 0.3],
                     funding__capital_needed=600_000)


def test_scenarios_keep_the_main_weights_so_value_rises_with_revenue():
    base = run(SLIDER_CASE)
    s = ve.run_valuation_scenarios(ve.ValuationInput(**SLIDER_CASE))
    blended = [s[k].blended_pre_money_valuation for k in s]
    assert blended == sorted(blended)  # never higher at lower revenue
    assert s["100%"].blended_pre_money_valuation == pytest.approx(base.blended_pre_money_valuation)
    for out in s.values():
        assert {k: mv.weight_used for k, mv in out.method_values.items()} == \
            {k: mv.weight_used for k, mv in base.method_values.items()}
    assert s["80%"].method_values["venture_capital"].pre_money_value == 0  # counts as zero, not dropped


def test_low_vc_value_is_explained():
    """By its own note, or (when it is the low end of a wide range) by the methods-disagree note."""
    notes = {w.code: w.message for w in run(SLIDER_CASE).warnings}
    assert "vc_low" in notes or "zero_venture_capital" in notes or (
        "Venture Capital method only €" in notes.get("methods_disagree", "") and "amount raised" in notes["methods_disagree"])
    assert "vc_low" not in {w.code for w in run(GERMAN_CASE).warnings}


def test_revenue_drop_is_flagged():
    assert "revenue_drop" in codes(german(operating_performance__current_revenue_last_12_months=350_000,
                                          operating_performance__current_ebitda=40_000,
                                          financial_assumptions__revenue_year1=200_000))
    assert "revenue_drop" not in codes(GERMAN_CASE)


def test_no_duplicate_comparables_note_without_revenue():
    found = codes(SLIDER_CASE)
    assert "no_revenue_history" in found and "nm_comparables" not in found


def test_german_scorecard_uses_equidam_country_average():
    """Equidam Parameters Update P6.3 (July 2026): Scorecard average for Germany EUR 6,550,000."""
    for stage in ("Idea stage", "Development stage"):
        r = run(german(company_profile__company_stage=stage))
        assert r.scorecard.benchmark_pre_money_valuation == 6_550_000
        assert r.scorecard.benchmark_source == "country_table" and "Equidam" in r.scorecard.benchmark_basis
        assert not r.scorecard.benchmark_to_be_sourced
        assert "benchmark_to_be_sourced" not in {w.code for w in r.warnings}


def messages(case):
    return {w.code: w.message for w in run(case).warnings}


def test_wording_follows_the_inputs():
    no_rev = dict(operating_performance__current_revenue_last_12_months=0, operating_performance__current_ebitda=-50_000,
                  company_profile__company_stage="Idea stage")
    m = messages(german(**no_rev, financial_assumptions__target_ebitda_margin_override=-0.1))
    assert "your own Year-5 target margin" in m["no_revenue_history"]
    assert "industry average margin" in messages(german(**no_rev))["no_revenue_history"]
    # growth above 100% is normal before revenue; no German tax note when losses mean no tax
    m = messages(german(**no_rev, financial_assumptions__revenue_growth_rates=[2.0, 1.0, 0.5, 0.5],
                        financial_assumptions__target_ebitda_margin_override=-0.1))
    assert "high_growth" not in m and "german_tax_schedule" not in m
    # region wording with "the" and adjectives; nothing about a "Global" benchmark
    m = messages(german(company_profile__country="United States", company_profile__business_territory_region="Global"))
    assert m["region_mismatch"].startswith("Companies in the United States are usually compared with US industry")
    assert "global figures" in m["region_mismatch"]
    assert "benchmark_global" not in messages(german(company_profile__company_stage="Idea stage",
                                                     company_profile__business_territory_region="Global"))


def test_banks_get_one_message_not_four():
    m = messages(german(company_profile__industry="Banks (Regional)", company_profile__company_stage="Idea stage"))
    assert "financial_sector" in m
    assert not [k for k in m if k.startswith("nm_") or k.startswith("fallback_")]


def test_no_method_message_lists_reasons_and_advice():
    case = german(company_profile__company_stage="Startup stage", operating_performance__current_revenue_last_12_months=0,
                  operating_performance__current_ebitda=-200_000, financial_assumptions__target_ebitda_margin_override=-0.5)
    with pytest.raises(ve.ValuationError) as e:
        run(case)
    text = str(e.value)
    assert "\n• " in text and ".;" not in text
    assert "choose the Idea or Development stage" in text


def test_every_scenario_is_kept_even_when_no_method_works_there():
    s = ve.run_valuation_scenarios(ve.ValuationInput(**SLIDER_CASE))
    assert list(s) == ["80%", "90%", "100%", "110%", "120%", "130%"]


def test_industry_names_are_spelled_out_and_old_names_still_work():
    names = ve.categorical_options()["industry"]
    assert not [n for n in names if re.search(r"Svcs|Prop/Cas|R\.E\.I\.T|Telecom\. |Furn/|Equip\b|Rubber& ", n)]
    case = copy.deepcopy(GERMAN_CASE)
    case["company_profile"]["industry"] = "Telecom. Services"  # Damodaran's spelling
    assert ve.ValuationInput(**case).company_profile.industry == "Telecom Services"
    assert "Insurance (Property & Casualty)" in ve.FINANCIAL_SECTOR_INDUSTRIES
