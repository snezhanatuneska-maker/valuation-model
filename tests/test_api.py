"""API tests: every route responds, bad input gets a readable 4xx (never a 500),
the PDF builds, and valuations saved before the methodology change still open."""
import copy

import pytest
from fastapi.testclient import TestClient

import app as api
from recompute import GERMAN_CASE, REFERENCE_CASE


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The API as deployed: demo mode, nothing stored."""
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def storing_client(tmp_path, monkeypatch):
    """The API with saving switched on (VALUATION_MODEL_STORE_VALUATIONS=1)."""
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(api, "STORE_VALUATIONS", True)
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


def test_demo_flow_stores_nothing(client, tmp_path):
    """Every call the wizard makes, then check that no database was created."""
    body = {k: GERMAN_CASE[k] for k in ("company_profile", "operating_performance", "financial_assumptions")}
    assert client.get("/health").status_code == 200
    assert client.post("/valuations/preview/projections", json=body).status_code == 200
    assert client.post("/valuations/preview", json=GERMAN_CASE).status_code == 200
    assert client.post("/valuations/preview/scenarios", json=GERMAN_CASE).status_code == 200
    assert client.post("/valuations/preview/report", json=GERMAN_CASE).status_code == 200
    assert not (tmp_path / "test.db").exists()


def test_saving_routes_are_off_in_demo(client, tmp_path):
    assert client.post("/valuations?owner_id=a", json=GERMAN_CASE).status_code == 404
    for path in ("/valuations?owner_id=a", "/valuations/x", "/valuations/x/scenarios", "/valuations/x/report"):
        assert client.get(path).status_code == 404, path
    assert client.post("/valuations/x/rerun").status_code == 404
    assert client.delete("/valuations/x").status_code == 404
    assert not (tmp_path / "test.db").exists()


def test_wizard_only_calls_preview_routes():
    """The page itself never calls a route that stores data."""
    from pathlib import Path
    import re
    html = (Path(api.__file__).parent / "index.html").read_text(encoding="utf-8")
    called = set(re.findall(r"/valuations[\w/]*", html))
    assert called and called <= {"/valuations/preview", "/valuations/preview/projections",
                                 "/valuations/preview/scenarios", "/valuations/preview/report"}, called


@pytest.mark.parametrize("path", [
    "/health", "/reference-data/options", "/reference-data/industries", "/reference-data/countries",
    "/reference-data/stages", "/reference-data/scorecard-lookup", "/reference-data/sources",
    "/reference-data/industries/Software (Entertainment)", "/reference-data/countries/Tanzania",
    "/reference-data/stages/Startup stage", "/reference-data/default-region/Germany",
])
def test_reference_routes(client, path):
    assert client.get(path).status_code == 200


def test_default_region(client):
    assert client.get("/reference-data/default-region/Germany").json()["region"].startswith("Europe")
    assert client.get("/reference-data/default-region/Tanzania").json()["region"].startswith("Emerging")


def test_preview_and_scenarios(client):
    r = client.post("/valuations/preview", json=REFERENCE_CASE)
    assert r.status_code == 200
    assert round(r.json()["blended_pre_money_valuation"]) == 911_380
    assert len(client.post("/valuations/preview/scenarios", json=REFERENCE_CASE).json()) == 6


def test_projection_preview(client):
    body = {k: REFERENCE_CASE[k] for k in ("company_profile", "operating_performance", "financial_assumptions")}
    r = client.post("/valuations/preview/projections", json=body)
    assert r.status_code == 200
    assert len(r.json()["projections"]["years"]) == 5


@pytest.mark.parametrize("section,field,value,expected", [
    ("financial_assumptions", "revenue_year1", 0, "revenue year1"),
    ("funding", "capital_needed", 0, "capital needed"),
    ("company_profile", "planned_time_to_exit_years", 6, "planned time to exit years"),
])
def test_bad_input_gets_readable_422(client, section, field, value, expected):
    case = copy.deepcopy(REFERENCE_CASE)
    case[section][field] = value
    r = client.post("/valuations/preview", json=case)
    assert r.status_code == 422
    assert expected in r.json()["detail"]


def test_unknown_reference_value_is_400(client):
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["industry"] = "Nope"
    r = client.post("/valuations/preview", json=case)
    assert r.status_code == 400 and "Nope" in r.json()["detail"]


def test_no_applicable_method_is_422(client):
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["industry"] = "Bank (Money Center)"
    case["company_profile"]["company_stage"] = "Maturity stage"
    r = client.post("/valuations/preview", json=case)
    assert r.status_code == 422 and "Banks and insurers" in r.json()["detail"]


def test_pdf_report(client):
    r = client.post("/valuations/preview/report", json=REFERENCE_CASE)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


@pytest.mark.parametrize("stage", ["Startup stage", "Idea stage"])
def test_german_pdf_report(client, stage):
    case = copy.deepcopy(GERMAN_CASE)
    case["company_profile"]["company_stage"] = stage
    r = client.post("/valuations/preview/report", json=case)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_german_country_detail_includes_specific_inputs(client):
    body = client.get("/reference-data/countries/Germany").json()
    assert body["country_risk_premium"] == 0
    assert body["country_specific"]["tax"]["average_hebesatz"] > 0
    assert "country_specific" not in client.get("/reference-data/countries/Tanzania").json()


def test_pdf_report_for_edge_cases(client):
    from recompute import EDGE_CASES
    for case in EDGE_CASES.values():
        r = client.post("/valuations/preview/report", json=case)
        assert r.status_code in (200, 422), r.text
        if r.status_code == 200:
            assert r.content[:4] == b"%PDF"


def test_save_list_rerun_report_delete(storing_client):
    client = storing_client
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["valuation_date"] = None
    r = client.post("/valuations?owner_id=browser-a", json=case)
    assert r.status_code == 201
    vid = r.json()["id"]
    saved = client.get(f"/valuations/{vid}").json()
    assert saved["input"]["company_profile"]["valuation_date"]  # date pinned when saved
    assert client.get("/valuations?owner_id=browser-a").json()[0]["id"] == vid
    assert client.post(f"/valuations/{vid}/rerun").status_code == 200
    assert client.get(f"/valuations/{vid}/scenarios").status_code == 200
    assert client.get(f"/valuations/{vid}/report").content[:4] == b"%PDF"
    assert client.delete(f"/valuations/{vid}?owner_id=browser-a").status_code == 204


def test_valuations_are_private_to_their_browser(storing_client):
    client = storing_client
    vid = client.post("/valuations?owner_id=browser-a", json=REFERENCE_CASE).json()["id"]
    assert client.get("/valuations").json() == []  # no owner: nothing listed
    assert client.get("/valuations?owner_id=browser-b").json() == []
    assert client.delete(f"/valuations/{vid}?owner_id=browser-b").status_code == 404
    assert client.get("/valuations?owner_id=browser-a").json()[0]["id"] == vid


def test_login_and_payment_endpoints_are_gone(client):
    assert client.post("/auth/demo-login", json={"email": "a@b.com"}).status_code == 404


def test_valuation_saved_by_the_old_version_still_opens(storing_client):
    client = storing_client
    """Input saved before this release: old answer spellings, no valuation date."""
    old_input = copy.deepcopy(REFERENCE_CASE)
    del old_input["company_profile"]["valuation_date"]
    old_input["market_and_team_assessment"]["willingness_to_step_aside_for_ceo"] = "Unwilling (*Deal braker)"
    old_input["market_and_team_assessment"]["target_market_size"] = "< $50 million "
    vid = api.save_valuation("Old Co", old_input, {"method_values": {}}, None)
    r = client.post(f"/valuations/{vid}/rerun")
    assert r.status_code == 200, r.text
    assert r.json()["input"]["market_and_team_assessment"]["target_market_size"] == "< $50 million"
    assert client.get(f"/valuations/{vid}/report").status_code == 200


# ---------------------------------------------------------------------------
# Sweep: many input combinations through the result, the scenarios and the PDF,
# checking for the kinds of mistakes found in user walkthroughs.
# ---------------------------------------------------------------------------
def _sweep_cases(n=60, seed=42):
    import random

    import valuation_engine as ve
    rng = random.Random(seed)
    lookup = ve.scorecard_qualitative_lookup()
    for i in range(n):
        c = copy.deepcopy(GERMAN_CASE)
        cp, op, fa = c["company_profile"], c["operating_performance"], c["financial_assumptions"]
        cp["country"] = rng.choice(["Germany", "Germany", "United States", "Austria", "Tanzania", "Côte d'Ivoire"])
        cp["business_territory_region"] = (ve.default_region_for_country(cp["country"]) if rng.random() < 0.8
                                           else rng.choice(ve.business_regions()))
        cp["industry"] = rng.choice(["Software (System & Application)", "Machinery", "Banks (Regional)",
                                     "Drugs (Biotechnology)", "Retail (Online)", "Green & Renewable Energy"])
        cp["company_stage"] = rng.choice(list(ve.stage_parameters()))
        cp["planned_time_to_exit_years"] = rng.choice([1, 3, 5])
        cp["dcf_tax_rate_override"] = rng.choice([None, 0.2])
        cp["benchmark_pre_money_override"] = rng.choice([None, 1_500_000])
        rev = rng.choice([0, 300_000, 5_000_000])
        op["current_revenue_last_12_months"] = rev
        op["current_ebitda"] = -100_000 if rev == 0 else round(rev * rng.choice([-0.2, 0.1, 0.25]))
        fa["revenue_year1"] = rng.choice([60_000, 1_000_000, 6_000_000])
        fa["revenue_growth_rates"] = rng.choice([[0.1] * 4, [2.0, 1.0, 0.6, 0.4], [-0.3, 0.1, 0.1, 0.1]])
        fa["existing_debt_balance"] = rng.choice([0, 10_000_000])
        fa["target_ebitda_margin_override"] = rng.choice([None, -0.1, 0.25])
        c["funding"]["capital_needed"] = rng.choice([100_000, 20_000_000])
        if rng.random() < 0.5:
            c["market_and_team_assessment"] = {q: rng.choice(list(o)) for q, o in lookup.items()}
        yield f"sweep {i}", c


BAD_TEXT = [r"\bnan\b", r"NaN", r"undefined", r"\bnull\b", r"&#39;", r"&amp;", r"€-", r"\b1 years\b",
            r"in United States", r"\.;", r"\(\(", r"company in (Global|US) "]


def test_sweep_results_scenarios_and_pdfs(client):
    import io
    import re

    from pypdf import PdfReader
    for name, case in _sweep_cases():
        r = client.post("/valuations/preview", json=case)
        assert r.status_code in (200, 422), (name, r.text)
        if r.status_code == 422:
            assert "\n" in r.json()["detail"] or "Banks and insurers" in r.json()["detail"], name
            continue
        out = r.json()
        scenarios = client.post("/valuations/preview/scenarios", json=case).json()
        assert list(scenarios) == ["80%", "90%", "100%", "110%", "120%", "130%"], name
        assert scenarios["100%"]["blended_pre_money_valuation"] == pytest.approx(out["blended_pre_money_valuation"])
        messages = [w["message"] for w in out["warnings"]]
        assert len(messages) == len(set(messages)), name
        pdf = client.post("/valuations/preview/report", json=case)
        assert pdf.status_code == 200, name
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
        for pattern in BAD_TEXT:
            assert not re.search(pattern, text), (name, pattern)
        assert not re.search(r"€[\d,]*,\d{0,2}\s*\n\s*\d", text), (name, "amount wrapped across lines")
        blended = re.search(r"Blended pre-money valuation\s*€([\d,]+)", text)
        assert blended and float(blended.group(1).replace(",", "")) == pytest.approx(
            out["blended_pre_money_valuation"], abs=1), name
        if case["company_profile"]["country"] != "Russia":
            assert "Russia" not in text, name
        if case["company_profile"]["industry"] != "Retail (Online)":
            assert "Retail (Online)" not in text, name
