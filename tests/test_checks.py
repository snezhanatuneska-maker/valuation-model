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
