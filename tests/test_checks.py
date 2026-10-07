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


# ---------------------------------------------------------------------------
# PDF report: numbers equal the API, formatting, layout
# ---------------------------------------------------------------------------
import io  # noqa: E402
import re  # noqa: E402

import pdfplumber  # noqa: E402

import report  # noqa: E402
from werkpuls import WERKPULS  # noqa: E402

PDF_CASES = {
    "werkpuls": WERKPULS,
    "werkpuls_idea": case(company_profile__company_stage="Idea stage",
                          operating_performance__current_revenue_last_12_months=0,
                          operating_performance__current_ebitda=-60_000,
                          market_and_team_assessment__willingness_to_step_aside_for_ceo="Willing"),
    "werkpuls_profitable": case(operating_performance__current_ebitda=90_000, company_profile__dcf_tax_rate_override=None),
}


def _pdf(client, c):
    r = client.post("/valuations/preview/report", json=c)
    assert r.status_code == 200
    return pdfplumber.open(io.BytesIO(r.content))


def _text(pdf):
    return "\n".join(p.extract_text() or "" for p in pdf.pages)


@pytest.mark.parametrize("name", list(PDF_CASES))
def test_pdf_numbers_equal_api(client, name):
    c = PDF_CASES[name]
    out = client.post("/valuations/preview", json=c).json()
    flat = " ".join(_text(_pdf(client, c)).split())
    expected = [out["blended_pre_money_valuation"], out["post_money_valuation"], out["capital_needed"]]
    for mv in out["method_values"].values():
        if mv["status"] == "ok":
            expected += [mv["pre_money_value"], mv["weighted_value"]]
    for y in out["projections"]["years"]:
        expected += [y["revenue"], y["ebitda"], y["unlevered_fcf"], y["capex"]]
    if out["dcf"]["pre_money_valuation"] is not None:
        expected += [out["dcf"]["pv_of_fcf"], out["dcf"]["pv_of_terminal_value"], out["dcf"]["enterprise_value"]]
    vc = out["venture_capital"]
    if vc["pre_money_valuation"] is not None:
        expected += [vc["exit_value"], vc["post_money_valuation"]]
        assert f"{vc['number_of_new_shares']:,.0f}" in flat
        assert f"€{vc['price_per_share']:,.2f}" in flat
    missing = [x for x in expected if report.money(x) not in flat]
    assert not missing


@pytest.mark.parametrize("name", list(PDF_CASES))
def test_pdf_formatting(client, name):
    pdf = _pdf(client, PDF_CASES[name])
    text = _text(pdf)
    flat = " ".join(text.split())
    assert "Existing shares (count) 25,000" in flat or "Venture Capital method" not in flat
    assert not re.search(r"shares[^\n]*€", text, re.I) or "Price per new share" in text
    assert not re.search(r"\b2,0[2-4]\d\b", text)  # a year printed as "2,027"
    assert not re.search(r"Tax rate 0\.\d", flat)  # a rate printed as a fraction
    assert not re.search(r"\bNaN\b|\bnan\b|\binf\b|\bNA\b|None", text)
    for typo in ("Strenght", "Perpetural", "Mulltiple", "Avalible", "Ammount", "Commited", "requirments",
                 "Dicount", "Shell ", "braker"):
        assert typo.lower() not in text.lower()
    assert "Fürther Straße 212" in text


@pytest.mark.parametrize("name", list(PDF_CASES))
def test_pdf_layout(client, name):
    pdf = _pdf(client, PDF_CASES[name])
    header_footer = re.compile(r"COMPANY VALUATION REPORT|Valuation Model by Snezhana Tuneska.*|WERKPULS GMBH")
    for i, page in enumerate(pdf.pages, start=1):
        body = header_footer.sub("", page.extract_text() or "").strip()
        assert len(body) > 40, f"page {i} is (almost) empty"
        right = page.width - 15 * 72 / 25.4 + 1
        overflow = [c["text"] for c in page.chars if c["x1"] > right or c["x0"] < 15 * 72 / 25.4 - 1]
        assert not overflow, f"page {i}: text outside the margins: {''.join(overflow)[:40]}"
    # Every chart is drawn (bars, pies and the blend chart are vector shapes).
    assert sum(len(p.rects) + len(p.curves) for p in pdf.pages) > 20


# ---------------------------------------------------------------------------
# API: every endpoint in /openapi.json answers, and every route gives the same numbers
# ---------------------------------------------------------------------------
@pytest.fixture()
def storing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(app_module, "STORE_VALUATIONS", True)
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


def _key_numbers(out):
    nums = {"blend": out["blended_pre_money_valuation"], "post": out["post_money_valuation"]}
    for k, mv in out["method_values"].items():
        if mv["status"] == "ok":
            nums[k] = mv["pre_money_value"]
            nums[k + "_weighted"] = mv["weighted_value"]
    return nums


def test_every_openapi_endpoint_answers(storing):
    c = storing
    paths = c.get("/openapi.json").json()["paths"]
    vid = c.post("/valuations?owner_id=lena", json=WERKPULS).json()["id"]
    seen = set()
    for path, ops in paths.items():
        for method in ops:
            url = path.replace("{valuation_id}", vid).replace("{stage}", "Startup stage") \
                .replace("{industry}", "Software (System & Application)").replace("{country}", "Germany")
            if method == "delete":
                continue  # last, below
            body = None
            if method == "post" and "{valuation_id}" not in path:
                body = WERKPULS if not path.endswith("/projections") else {
                    k: WERKPULS[k] for k in ("company_profile", "operating_performance", "financial_assumptions")}
            r = c.request(method.upper(), url + ("?owner_id=lena" if path == "/valuations" else ""), json=body)
            assert r.status_code in (200, 201), (method, path, r.status_code, r.text[:200])
            assert r.content, (method, path)
            seen.add((method, path))
    assert c.delete(f"/valuations/{vid}?owner_id=someone-else").status_code == 404
    assert c.delete(f"/valuations/{vid}?owner_id=lena").status_code == 204
    assert c.get(f"/valuations/{vid}").status_code == 404
    assert len(seen) >= 17


def test_same_numbers_on_every_route(storing):
    c = storing
    preview = c.post("/valuations/preview", json=WERKPULS).json()
    saved = c.post("/valuations?owner_id=lena", json=WERKPULS).json()
    vid = saved["id"]
    rerun = c.post(f"/valuations/{vid}/rerun").json()["output"]
    scen = c.post("/valuations/preview/scenarios", json=WERKPULS).json()["100%"]
    saved_scen = c.get(f"/valuations/{vid}/scenarios").json()["100%"]
    expected = _key_numbers(preview)
    for out in (saved["output"], rerun, c.get(f"/valuations/{vid}").json()["output"], scen, saved_scen):
        assert _key_numbers(out) == pytest.approx(expected)
    for r in (c.post("/valuations/preview/report", json=WERKPULS), c.get(f"/valuations/{vid}/report")):
        flat = " ".join(_text(pdfplumber.open(io.BytesIO(r.content))).split())
        assert all(report.money(v) in flat for v in expected.values())


def test_other_people_do_not_see_saved_valuations(storing):
    c = storing
    c.post("/valuations?owner_id=lena", json=WERKPULS)
    assert c.get("/valuations?owner_id=jonas").json() == []
    assert c.get("/valuations").json() == []
    assert [v["company_name"] for v in c.get("/valuations?owner_id=lena").json()] == ["Werkpuls GmbH"]


# ---------------------------------------------------------------------------
# Engine: every industry x region x stage, and edge inputs
# ---------------------------------------------------------------------------
import dataclasses  # noqa: E402
import statistics  # noqa: E402
import warnings  # noqa: E402

import valuation_engine as ve  # noqa: E402


def test_sweep_every_industry_region_stage():
    """A profitable standard company: no crash, no NaN/"NA", a positive value, every fallback listed.
    Results more than 10x or under 0.1x their stage's median are reported as warnings, not failures."""
    values, problems = {}, []
    for stage in ve.stage_parameters():
        for industry in ve.industry_benchmarks():
            for region in ve.business_regions():
                c = case(company_profile__company_stage=stage, company_profile__industry=industry,
                         company_profile__business_territory_region=region, operating_performance__current_ebitda=80_000)
                try:
                    r = ve.run_valuation(ve.ValuationInput(**c))
                except ve.ValuationError:
                    if industry not in ve.FINANCIAL_SECTOR_INDUSTRIES:
                        problems.append((stage, industry, region, "no method applies"))
                    continue
                text = json.dumps(dataclasses.asdict(r))
                if re.search(r"NaN|Infinity|\"NA\"", text) or not r.blended_pre_money_valuation > 0:
                    problems.append((stage, industry, region, r.blended_pre_money_valuation))
                codes = {w.code for w in r.warnings}
                if industry not in ve.FINANCIAL_SECTOR_INDUSTRIES:
                    problems += [(stage, industry, region, f"fallback {m} not listed") for m, b in r.benchmarks_used.items()
                                 if b.source != "region" and f"fallback_{m}" not in codes]
                values[(stage, industry, region)] = r.blended_pre_money_valuation
    assert not problems
    for stage in ve.stage_parameters():
        median = statistics.median(v for k, v in values.items() if k[0] == stage)
        for (s, industry, region), v in values.items():
            if s == stage and not 0.1 * median <= v <= 10 * median:
                warnings.warn(f"{stage} / {industry} / {region}: €{v:,.0f} vs stage median €{median:,.0f}")


EDGE_INPUTS = {
    "no revenue": dict(operating_performance__current_revenue_last_12_months=0, operating_performance__current_ebitda=0),
    "no revenue, losses": dict(operating_performance__current_revenue_last_12_months=0,
                               operating_performance__current_ebitda=-50_000),
    "negative EBITDA": dict(operating_performance__current_ebitda=-400_000),
    "zero growth": dict(financial_assumptions__revenue_growth_rates=[0, 0, 0, 0]),
    "100% growth": dict(financial_assumptions__revenue_growth_rates=[1, 1, 1, 1]),
    "nothing raised": dict(funding__capital_needed=0),
    "exit in 1 year": dict(company_profile__planned_time_to_exit_years=1),
    "exit in 5 years": dict(company_profile__planned_time_to_exit_years=5),
    "1e10": dict(operating_performance__current_revenue_last_12_months=1e10, operating_performance__current_ebitda=2e9,
                 financial_assumptions__revenue_year1=1e10, funding__capital_needed=1e10),
    "Year-1 revenue 0": dict(financial_assumptions__revenue_year1=0),
    "never profitable": dict(financial_assumptions__target_ebitda_margin_override=-0.5),
}


@pytest.mark.parametrize("name", list(EDGE_INPUTS))
def test_edge_inputs_give_a_result_or_a_clear_message(client, name):
    c = case(**EDGE_INPUTS[name])
    for route in PREVIEW_ROUTES + ["/valuations/preview/projections"]:
        body = c if not route.endswith("projections") else {
            k: c[k] for k in ("company_profile", "operating_performance", "financial_assumptions")}
        r = client.post(route, json=body)
        assert r.status_code in (200, 422), (route, r.status_code)
        if r.status_code == 422:
            assert len(r.json()["detail"]) > 20
        elif route == "/valuations/preview":
            assert r.json()["blended_pre_money_valuation"] > 0


def test_discount_rate_close_to_growth_is_handled(monkeypatch):
    """WACC almost equal to long-run growth: the terminal value uses a minimum gap and says so."""
    real = ve.compute_wacc

    def wacc(company, bench=None):
        w = real(company, bench)
        w.wacc = 0.0201
        return w
    monkeypatch.setattr(ve, "compute_wacc", wacc)
    r = ve.run_valuation(ve.ValuationInput(**WERKPULS))
    assert r.dcf.terminal_value_floor_applied and "tv_floor" in {w.code for w in r.warnings}
    assert r.dcf.terminal_value == pytest.approx(r.dcf.terminal_fcf * 1.02 / 0.02)


def test_api_description_uses_the_method_names_shown_everywhere_else(client):
    description = client.get("/openapi.json").json()["info"]["description"]
    assert "DCF Multiples" not in description
    assert "Comparables (EV/EBITDA multiple)" in description


@pytest.mark.parametrize("name,most", [("werkpuls", 10), ("werkpuls_profitable", 10), ("werkpuls_idea", 11),
                                       ("reference", 10), ("german", 10)])
def test_pdf_is_about_ten_pages(client, name, most):
    from recompute import GERMAN_CASE, REFERENCE_CASE
    c = {"reference": REFERENCE_CASE, "german": GERMAN_CASE}.get(name) or PDF_CASES[name]
    assert len(_pdf(client, c).pages) <= most


def test_unused_scorecard_is_one_line_not_a_page(client):
    text = " ".join(_text(_pdf(client, WERKPULS)).split())
    assert "Scorecard method Not used for this company" not in text
    assert "Scorecard: not used at this stage" in text
