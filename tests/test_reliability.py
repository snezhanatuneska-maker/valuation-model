"""Reliability checks from the 8 October 2026 audit (AUDIT_REPORT.md): a worse plan never gives a higher value,
contradictory inputs are refused, implausible results are flagged, and a saved valuation keeps its figures."""
import copy

import pytest
from fastapi.testclient import TestClient

import app as api
import valuation_engine as ve
from recompute import REFERENCE_CASE, variant
from werkpuls import WERKPULS


def run(case):
    return ve.run_valuation(ve.ValuationInput(**case))


def value(case):
    """The blended value, or 0 when the tool says the business can't be valued."""
    try:
        return run(case).blended_pre_money_valuation
    except ve.ValuationError:
        return 0.0


def changed(case, path, v):
    c = copy.deepcopy(case)
    section, key = path.split("__")
    c[section][key] = v
    return c


# Each input stepped from better to worse. (Last-12-month EBITDA is not here: crossing zero leaves the
# Comparables method out, an owner decision; see QUESTIONS.md 13.)
WORSENING = {
    "Year-1 revenue": ("financial_assumptions__revenue_year1", [1_200_000 - 50_000 * i for i in range(24)]),
    "Year-1 capex": ("financial_assumptions__capex_by_year",
                     [[100_000 * i, 50_000, 60_000, 70_000, 80_000] for i in range(30)]),
    "growth": ("financial_assumptions__revenue_growth_rates", [[g / 100] * 4 for g in range(40, -60, -5)]),
    "target margin": ("financial_assumptions__target_ebitda_margin_override", [m / 100 for m in range(30, -60, -3)]),
    "debt": ("financial_assumptions__existing_debt_balance", [250_000 * i for i in range(30)]),
    "amount raised": ("funding__capital_needed", [200_000 * i for i in range(1, 30)]),
}


@pytest.mark.parametrize("base", [REFERENCE_CASE, WERKPULS], ids=["reference", "werkpuls"])
@pytest.mark.parametrize("name", list(WORSENING))
def test_a_worse_input_never_gives_a_higher_value(base, name):
    """Before the fix, a method that stopped giving a value was dropped and its weight handed to the others,
    so e.g. a €80k revenue plan was worth more than a €200k one."""
    path, steps = WORSENING[name]
    values = [value(changed(base, path, v)) for v in steps]
    rises = [(steps[i], values[i], steps[i + 1], values[i + 1])
             for i in range(len(values) - 1) if values[i + 1] > values[i] * 1.0001 + 1]
    assert not rises


def test_a_method_that_applies_but_finds_no_value_counts_as_zero():
    r = run(variant(financial_assumptions__capex_by_year=[2_000_000, 30_000, 30_000, 30_000, 30_000]))
    dcf = r.method_values["dcf"]
    assert dcf.status == "ok" and dcf.weight_used == pytest.approx(0.35)  # not left out, not re-weighted
    assert dcf.pre_money_value == pytest.approx(REFERENCE_CASE["operating_performance"]["cash_available"])
    assert "counts as €20,000 in the blend" in dcf.note
    assert r.blended_pre_money_valuation < run(REFERENCE_CASE).blended_pre_money_valuation
    vc = run(variant(funding__capital_needed=5_000_000)).method_values["venture_capital"]
    assert vc.status == "ok" and vc.pre_money_value == 0 and "counts as €0" in vc.note


def test_no_value_for_the_business_is_a_message_not_a_valuation():
    with pytest.raises(ve.ValuationError, match="finds any value for the business"):
        run(variant(financial_assumptions__target_ebitda_margin_override=-0.6,
                    operating_performance__current_ebitda=-50_000))


@pytest.mark.parametrize("revenue,ebitda", [(300_000, 400_000), (0, 50_000)])
def test_ebitda_above_revenue_is_refused(revenue, ebitda):
    case = variant(operating_performance__current_revenue_last_12_months=revenue,
                   operating_performance__current_ebitda=ebitda)
    with TestClient(api.app) as client:
        r = client.post("/valuations/preview", json=case)
    assert r.status_code == 422 and "current ebitda" in r.json()["detail"]


def test_a_loss_before_revenue_is_allowed_and_carried_into_year_1():
    """Without revenue yet, the projection starts from today's loss measured against Year-1 revenue,
    not from the industry margin (and leaving revenue at 0 no longer raises the value)."""
    case = variant(operating_performance__current_revenue_last_12_months=0, operating_performance__current_ebitda=-200_000)
    p = run(case).projections
    assert p.starting_margin_source == "company_costs"
    assert p.starting_ebitda_margin == pytest.approx(-200_000 / 1_000_000)
    assert p.years[0].ebitda_margin < 0 < p.years[-1].ebitda_margin
    zero_revenue = variant(operating_performance__current_revenue_last_12_months=0,
                           operating_performance__current_ebitda=0)
    assert value(zero_revenue) < value(REFERENCE_CASE)


def test_terminal_year_is_a_business_growing_at_the_long_run_rate():
    r = run(WERKPULS)
    y5 = r.projections.years[-1]
    g, tax = r.dcf.perpetual_growth_rate, r.projections.tax_rate
    assert r.dcf.terminal_fcf == pytest.approx(
        y5.ebit - max(y5.ebit, 0) * tax + y5.da - max(y5.capex, y5.da) - y5.working_capital * g)
    # A bigger past loss can't raise the value through a tax saving that lasts forever.
    assert value(changed(WERKPULS, "operating_performance__current_ebitda", -100_000)) < value(WERKPULS)


def test_implausible_value_is_flagged_on_the_cover():
    case = variant(financial_assumptions__revenue_growth_rates=[10, 10, 10, 10])
    r = run(case)
    note = next(w for w in r.warnings if w.code == "implausible_value")
    assert note.severity == "warning" and "× the last 12 months' revenue" in note.message
    assert "implausible_value" not in {w.code for w in run(REFERENCE_CASE).warnings}
    import io

    import pdfplumber
    with TestClient(api.app) as client:
        pdf = client.post("/valuations/preview/report", json=case)
    cover = pdfplumber.open(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "Before sharing this report" in cover and "Startups are rarely valued" in " ".join(cover.split())


def test_working_capital_release_gets_a_note():
    codes = {w.code for w in run(REFERENCE_CASE).warnings}
    assert "wc_release" in codes  # €59,969 freed in Year 1 against €185,145 EBITDA


def test_methodology_text_states_the_weights_used():
    text = ve.data_sources()["method_weights"]
    for stage in ("Startup stage", "Expansion stage"):
        w = ve.stage_parameters()[stage]["method_weights"]
        shown = "/".join(f"{w[k] * 100:g}" for k in ("venture_capital", "comparables", "dcf")) + "%"
        assert shown in text


def test_saved_valuation_keeps_its_figures_after_a_data_change(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(api, "STORE_VALUATIONS", True)
    import io

    import pdfplumber
    with TestClient(api.app) as client:
        saved = client.post("/valuations", json=REFERENCE_CASE).json()
        before = saved["output"]["blended_pre_money_valuation"]
        scenarios_before = client.get(f"/valuations/{saved['id']}/scenarios").json()
        # A later data refresh: every EV/EBITDA multiple 10% higher.
        original = ve.get_industry_metric_with_source
        monkeypatch.setattr(ve, "get_industry_metric_with_source", lambda i, m, r: (
            (original(i, m, r)[0] * 1.1, original(i, m, r)[1]) if m == "ev_ebitda_multiple" else original(i, m, r)))
        assert client.post(f"/valuations/{saved['id']}/rerun").json()["output"]["blended_pre_money_valuation"] > before
        assert client.get(f"/valuations/{saved['id']}/scenarios").json() == scenarios_before
        pdf = client.get(f"/valuations/{saved['id']}/report")
    text = " ".join("\n".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(pdf.content)).pages).split())
    assert f"Blended pre-money valuation €{before:,.0f}" in text
