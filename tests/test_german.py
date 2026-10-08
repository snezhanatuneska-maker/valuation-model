"""German version (?lang=de), the revenue/ARR multiple in the Comparables method, and the round-logic cross-check."""
import copy
import io
import re

import pdfplumber
import pytest
from fastapi.testclient import TestClient

import app as api
import valuation_engine as ve
from recompute import REFERENCE_CASE, variant
from werkpuls import WERKPULS


@pytest.fixture(scope="module")
def client():
    with TestClient(api.app) as c:
        yield c


def pdf_text(content):
    return " ".join("\n".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(content)).pages).split())


# --- German version ---------------------------------------------------------------------------------------------

def test_language_only_changes_words_not_figures(client):
    en = client.post("/valuations/preview", json=WERKPULS).json()
    de = client.post("/valuations/preview?lang=de", json=WERKPULS).json()
    assert de["blended_pre_money_valuation"] == en["blended_pre_money_valuation"]
    assert [w["code"] for w in de["warnings"]] == [w["code"] for w in en["warnings"]]
    assert all(w["message"] != e["message"] for w, e in zip(de["warnings"], en["warnings"]))
    assert ve.current_language() == "en"  # the request's language doesn't leak into the next one


def test_german_validation_messages(client):
    bad = copy.deepcopy(WERKPULS)
    bad["operating_performance"]["current_ebitda"] = 9_000_000
    detail = client.post("/valuations/preview?lang=de", json=bad).json()["detail"]
    assert detail.startswith("Bitte prüfen Sie diese Angaben: EBITDA der letzten 12 Monate:")
    assert "can't" not in detail
    assert "Please check" in client.post("/valuations/preview", json=bad).json()["detail"]


def test_labels_endpoint(client):
    de = client.get("/reference-data/labels?lang=de").json()
    assert de["stages"]["Startup stage"] != "Startup stage"
    assert de["countries"]["Germany"] == "Deutschland"
    assert de["countries"]["Netherlands"] == "Niederlande"
    assert de["countries_in_text"]["Netherlands"] == "den Niederlanden"
    assert de["regions"]["Global"] == "Weltweit"
    # Every country has a German name or one that is spelled the same in German.
    assert len(de["countries"]) == len(client.get("/reference-data/countries").json())
    en = client.get("/reference-data/labels").json()
    assert all(k == v for k, v in en["countries"].items())


def test_german_pdf(client):
    r = client.post("/valuations/preview/report?lang=de", json=WERKPULS)
    assert r.status_code == 200 and "Bewertungsbericht" in r.headers["content-disposition"]
    text = pdf_text(r.content)
    pages = len(pdfplumber.open(io.BytesIO(r.content)).pages)
    en_pages = len(pdfplumber.open(io.BytesIO(client.post("/valuations/preview/report", json=WERKPULS).content)).pages)
    assert pages <= en_pages + 1
    value = client.post("/valuations/preview", json=WERKPULS).json()["blended_pre_money_valuation"]
    assert f"{value:,.0f}".replace(",", ".") + " €" in text or f"{value:,.0f}".replace(",", ".") + " €" in text
    assert "Rundenlogik" in text and "IDW S1" in text
    # Leave out what the founder typed and a cited book title; everything else is German.
    for own in [WERKPULS["company_profile"][k] for k in ("business_activity", "business_model", "exit_strategy")] + [
            "'Valuing Young, Start-up and Growth Companies'"]:
        text = text.replace(" ".join(own.split()), "")
    assert not re.search(r"\b(the|and|your|revenue|Valuation report)\b", text)
    assert not re.search(r"€\d", text)  # German puts the € after the number


# --- Comparables: revenue or EBITDA multiple ----------------------------------------------------------------------

def comparables(case):
    return ve.run_valuation(ve.ValuationInput(**case)).method_values["comparables"]


def test_comparables_uses_the_higher_of_revenue_and_ebitda_based_value():
    r = ve.run_valuation(ve.ValuationInput(**REFERENCE_CASE))
    c = r.comparables
    assert c.basis_used in ("revenue", "ebitda")
    assert max(c.ebitda_based_ev or 0, c.revenue_based_ev or 0) == pytest.approx(
        c.ebitda_based_ev if c.basis_used == "ebitda" else c.revenue_based_ev)


def test_comparables_never_drops_as_ebitda_crosses_zero():
    """Open question 13: before, a company at -€1 EBITDA lost the method entirely; now the revenue multiple
    takes over, so the value moves smoothly and never rises as EBITDA falls."""
    values = [comparables(variant(operating_performance__current_ebitda=e)).pre_money_value
              for e in range(60_000, -60_001, -10_000)]
    assert all(b <= a + 1 for a, b in zip(values, values[1:]))
    assert values[-1] > 0


def test_saas_arr_uses_the_public_saas_multiple():
    case = variant(operating_performance__current_ebitda=-150_000,
                   operating_performance__annual_recurring_revenue=450_000)
    r = ve.run_valuation(ve.ValuationInput(**case))
    c = r.comparables
    assert c.revenue_basis == "arr" and c.revenue_amount == 450_000
    assert c.revenue_multiple == pytest.approx(ve.saas_arr_multiple()["value"])
    without_arr = ve.run_valuation(ve.ValuationInput(**variant(operating_performance__current_ebitda=-150_000)))
    assert without_arr.comparables.revenue_basis == "revenue"


def test_negative_arr_is_refused(client):
    case = variant(operating_performance__annual_recurring_revenue=-1)
    assert client.post("/valuations/preview", json=case).status_code == 422


# --- Round logic -----------------------------------------------------------------------------------------------------

def test_implied_pre_money_formula():
    # €1m buying 20% after the round means €4m before it.
    assert ve.implied_pre_money(1_000_000, 0.20) == pytest.approx(4_000_000)


def test_round_logic_for_werkpuls(client):
    out = client.post("/valuations/preview", json=WERKPULS).json()
    rl = out["round_logic"]
    row = ve.round_benchmarks()["stages"][WERKPULS["company_profile"]["company_stage"]]
    raise_ = WERKPULS["funding"]["capital_needed"]
    assert rl["implied_pre_money_low"] == pytest.approx(ve.implied_pre_money(raise_, row["dilution_high"]))
    assert rl["implied_pre_money_high"] == pytest.approx(ve.implied_pre_money(raise_, row["dilution_low"]))
    assert rl["dilution_at_blend"] == pytest.approx(raise_ / (raise_ + out["blended_pre_money_valuation"]))
    assert rl["raise_position"] == ("below" if raise_ < row["round_eur_low"] else
                                    "above" if raise_ > row["round_eur_high"] else "within")


def test_round_logic_is_a_cross_check_not_part_of_the_blend(monkeypatch):
    before = ve.run_valuation(ve.ValuationInput(**WERKPULS)).blended_pre_money_valuation
    data = copy.deepcopy(ve._all_reference_data())
    for row in data["round_benchmarks"]["stages"].values():
        row["dilution_low"], row["dilution_median"], row["dilution_high"] = 0.05, 0.06, 0.07
    monkeypatch.setattr(ve, "_all_reference_data", lambda: data)
    assert ve.run_valuation(ve.ValuationInput(**WERKPULS)).blended_pre_money_valuation == pytest.approx(before)


def test_round_benchmarks_are_ordered():
    for stage, row in ve.round_benchmarks()["stages"].items():
        assert 0 < row["dilution_low"] <= row["dilution_median"] <= row["dilution_high"] < 1, stage
        assert 0 < row["round_eur_low"] < row["round_eur_high"], stage
