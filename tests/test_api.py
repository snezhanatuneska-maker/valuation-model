"""API tests: every route responds, bad input gets a readable 4xx (never a 500),
the PDF builds, and valuations saved before the methodology change still open."""
import copy

import pytest
from fastapi.testclient import TestClient

import app as api
from recompute import REFERENCE_CASE


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


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
    assert round(r.json()["blended_pre_money_valuation"]) == 1_131_786
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
    assert r.status_code == 422 and "None of the methods" in r.json()["detail"]


def test_pdf_report(client):
    r = client.post("/valuations/preview/report", json=REFERENCE_CASE)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_pdf_report_for_edge_cases(client):
    from recompute import EDGE_CASES
    for case in EDGE_CASES.values():
        r = client.post("/valuations/preview/report", json=case)
        assert r.status_code in (200, 422), r.text
        if r.status_code == 200:
            assert r.content[:4] == b"%PDF"


def test_save_list_rerun_report_delete(client):
    case = copy.deepcopy(REFERENCE_CASE)
    case["company_profile"]["valuation_date"] = None
    r = client.post("/valuations?user_email=a@b.com", json=case)
    assert r.status_code == 201
    vid = r.json()["id"]
    saved = client.get(f"/valuations/{vid}").json()
    assert saved["input"]["company_profile"]["valuation_date"]  # date pinned when saved
    assert client.get("/valuations?user_email=a@b.com").json()[0]["id"] == vid
    assert client.post(f"/valuations/{vid}/rerun").status_code == 200
    assert client.get(f"/valuations/{vid}/scenarios").status_code == 200
    assert client.get(f"/valuations/{vid}/report").content[:4] == b"%PDF"
    assert client.delete(f"/valuations/{vid}").status_code == 204


def test_valuation_saved_by_the_old_version_still_opens(client):
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
