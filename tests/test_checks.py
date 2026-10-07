"""Checks from the owner's check-and-fix loop: edge inputs, API consistency, PDF formatting."""
import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
from werkpuls import case

PREVIEW_ROUTES = ["/valuations/preview", "/valuations/preview/scenarios", "/valuations/preview/report"]


@pytest.fixture()
def client():
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.mark.parametrize("section,field", [
    ("operating_performance", "current_ebitda"),
    ("operating_performance", "cash_available"),
    ("financial_assumptions", "existing_debt_balance"),
    ("funding", "capital_needed"),
    ("company_profile", "committed_capital"),
])
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_not_a_number_is_rejected_with_422(client, section, field, bad):
    c = case()
    c[section][field] = 123456789
    body = json.dumps(c).replace("123456789", bad)
    for route in PREVIEW_ROUTES:
        r = client.post(route, content=body, headers={"Content-Type": "application/json"})
        assert r.status_code == 422, (route, r.status_code)
        assert field.replace("_", " ") in r.json()["detail"]


@pytest.mark.parametrize("changes", [
    {"financial_assumptions__revenue_year1": 1e200},
    {"operating_performance__current_revenue_last_12_months": 1e300, "operating_performance__current_ebitda": 1e299},
    {"operating_performance__cash_available": 1e300},
    {"financial_assumptions__capex_by_year": [1e300, 0, 0, 0, 0]},
    {"funding__use_of_funds": {"Others": 1e300}},
])
def test_absurdly_large_amounts_get_422_not_500(client, changes):
    for route in PREVIEW_ROUTES:
        r = client.post(route, json=case(**changes))
        assert r.status_code == 422, (route, r.status_code)
        assert "at most 10,000,000,000,000" in r.json()["detail"] or "equal to 10,000,000,000,000" in r.json()["detail"]


def test_ten_billion_still_values_and_prints(client):
    big = case(operating_performance__current_revenue_last_12_months=1e10, operating_performance__current_ebitda=2e9,
               financial_assumptions__revenue_year1=1e10, funding__capital_needed=1e10)
    for route in PREVIEW_ROUTES:
        assert client.post(route, json=big).status_code == 200, route
