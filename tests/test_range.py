"""The result shows how far apart the methods are, and how much a 20% revenue miss moves it."""
import io

import pdfplumber
import pytest
from fastapi.testclient import TestClient

import app as app_module
import report
import valuation_engine as ve
from werkpuls import WERKPULS, case


def run(c):
    return ve.run_valuation(ve.ValuationInput(**c))


def test_method_range_lists_lowest_and_highest_method():
    r = run(WERKPULS)
    used = {k: mv.pre_money_value for k, mv in r.method_values.items() if mv.status == "ok" and mv.weight_used > 0}
    assert r.method_range.low == pytest.approx(min(used.values()))
    assert r.method_range.high == pytest.approx(max(used.values()))
    assert r.method_range.low_method == "venture_capital" and r.method_range.high_method == "dcf"
    assert r.method_range.low <= r.blended_pre_money_valuation <= r.method_range.high


def test_methods_far_apart_get_a_note_naming_the_cause():
    r = run(WERKPULS)  # VC about €145k, DCF about €1.76M
    note = next(w for w in r.warnings if w.code == "methods_disagree")
    assert note.severity == "warning"
    assert "€145,499" in note.message and "€1,764,863" in note.message and "12×" in note.message
    assert "amount raised" in note.message  # the VC method is the low one: say what drives it
    assert "vc_low" not in {w.code for w in r.warnings}  # one note, not two saying the same


def test_methods_close_together_get_no_note():
    r = run(case(funding__capital_needed=150_000, funding__use_of_funds={"Others": 150_000},
                 financial_assumptions__capex_by_year=[0, 50_000, 60_000, 70_000, 80_000]))
    assert r.method_range.high <= 3 * r.method_range.low
    assert "methods_disagree" not in {w.code for w in r.warnings}


def test_one_method_has_no_range():
    c = case(company_profile__company_stage="Maturity stage", operating_performance__current_ebitda=-10_000)
    r = run(c)  # Maturity: Comparables + DCF; Comparables left out -> only DCF
    assert r.method_range is None
    assert "methods_disagree" not in {w.code for w in r.warnings}


def _pdf_text(c):
    pdf = TestClient(app_module.app).post("/valuations/preview/report", json=c)
    return " ".join("\n".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(pdf.content)).pages).split())


def test_cover_page_shows_value_range_and_revenue_scenarios():
    client = TestClient(app_module.app)
    out = client.post("/valuations/preview", json=WERKPULS).json()
    scen = client.post("/valuations/preview/scenarios", json=WERKPULS).json()
    pdf = client.post("/valuations/preview/report", json=WERKPULS)
    cover = " ".join(pdfplumber.open(io.BytesIO(pdf.content)).pages[0].extract_text().split())
    rng = out["method_range"]
    assert "Pre-money valuation" in cover and report.money(out["blended_pre_money_valuation"]) in cover
    assert f"{report.money(rng['low'])} (Venture Capital) to {report.money(rng['high'])} (DCF)" in cover
    assert (f"{report.money(scen['80%']['blended_pre_money_valuation'])} to "
            f"{report.money(scen['120%']['blended_pre_money_valuation'])}") in cover
    assert "Post-money valuation" in cover
