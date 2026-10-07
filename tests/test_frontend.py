"""Browser checks of the wizard (index.html) against a local API, at desktop and phone width.

Needs Playwright with Chromium (`pip install playwright pdfplumber`); skipped when it isn't installed,
so the GitHub Actions run (which doesn't install a browser) is unaffected.
"""
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
pdfplumber = pytest.importorskip("pdfplumber")

from werkpuls import WERKPULS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# The API only accepts calls from these origins (app.py CORS list), so the page is served on 8000.
PAGE_PORT = 8000
VIEWPORTS = {"desktop": {"width": 1280, "height": 900}, "phone": {"width": 390, "height": 844}}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not start")


@pytest.fixture(scope="module")
def servers():
    api_port = _free_port()
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(api_port)], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    page = subprocess.Popen([sys.executable, "-m", "http.server", str(PAGE_PORT), "--bind", "127.0.0.1"], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait(f"http://127.0.0.1:{api_port}/health")
        _wait(f"http://127.0.0.1:{PAGE_PORT}/index.html")
        yield f"http://127.0.0.1:{PAGE_PORT}/index.html", f"http://127.0.0.1:{api_port}"
    finally:
        api.terminate()
        page.terminate()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        kwargs = {}
        if os.path.exists("/opt/pw-browsers/chromium"):
            kwargs["executable_path"] = "/opt/pw-browsers/chromium"
        try:
            b = p.chromium.launch(**kwargs)
        except Exception as e:  # no browser on this machine
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


class Page:
    """A wizard page with console errors and failed requests recorded."""

    def __init__(self, browser, servers, viewport):
        url, api = servers
        self.api = api
        self.ctx = browser.new_context(viewport=viewport, accept_downloads=True)
        self.ctx.add_init_script(f"window.VALUATION_API_BASE = {json.dumps(api)};")
        self.page = self.ctx.new_page()
        self.errors, self.failed = [], []
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("requestfailed", lambda r: self.failed.append(f"{r.url} {r.failure}"))
        self.page.on("response", lambda r: r.status >= 400 and r.request.resource_type != "fetch"
                     and self.failed.append(f"{r.url} {r.status}"))
        self.page.on("response", lambda r: r.status >= 500 and self.failed.append(f"{r.url} {r.status}"))
        self.page.goto(url)
        self.page.wait_for_function("document.getElementById('industry').options.length > 10")
        self.page.click("#btn-start")

    def fill(self, field_id, value):
        self.page.fill(f"#{field_id}", str(value))

    def select(self, field_id, value):
        self.page.select_option(f"#{field_id}", value)

    def next(self):
        self.page.click("#btn-next")

    def step(self):
        return int(self.page.text_content("#step-current"))

    def api_post(self, path, payload):
        req = urllib.request.Request(self.api + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    def close(self):
        self.ctx.close()


def fill_werkpuls(w: Page, c=WERKPULS, stop_after=5):
    cp, mk, op, fa = (c["company_profile"], c["market_and_team_assessment"], c["operating_performance"],
                      c["financial_assumptions"])
    for f in ("company_name", "contact_name", "contact_email", "address", "website", "business_activity",
              "business_model", "exit_strategy"):
        w.page.type(f"#{f}", cp[f])  # typed key by key, umlauts included
    w.select("country", cp["country"])
    w.select("business_territory_region", cp["business_territory_region"])
    w.select("industry", cp["industry"])
    w.select("company_stage", cp["company_stage"])
    for f in ("num_founders", "num_employees", "year_of_incorporation", "committed_capital",
              "planned_time_to_exit_years", "valuation_date"):
        w.fill(f, cp[f])
    w.next()
    assert w.step() == 2, w.page.text_content("#form-error")
    if stop_after == 1:
        return
    for k, v in mk.items():
        w.select(f"sc_{k}", v)
    w.next()
    for k, v in op.items():
        w.fill(k, v)
    w.next()
    assert w.step() == 4, w.page.text_content("#form-error")
    w.fill("revenue_year1", fa["revenue_year1"])
    for i, g in enumerate(fa["revenue_growth_rates"]):
        w.fill(f"growth_y{i + 2}", round(g * 100, 4))
    for i, x in enumerate(fa["capex_by_year"]):
        w.fill(f"capex_y{i + 1}", x)
    w.fill("existing_debt_balance", fa["existing_debt_balance"])
    if stop_after == 4:
        return
    w.next()
    assert w.step() == 5, w.page.text_content("#form-error")
    w.fill("capital_needed", c["funding"]["capital_needed"])
    w.fill("number_of_existing_shares", c["vc_assumptions"]["number_of_existing_shares"])
    for i, s in enumerate(c["ownership"], start=1):
        w.fill(f"shareholder_name_{i}", s["name"])
        w.fill(f"shareholder_pct_{i}", round(s["ownership_pct"] * 100, 4))
    ids = {"Product & R&D": "use_rd", "Sales & marketing": "use_sales", "Inventory": "use_inventory",
           "Operations": "use_ops", "Capital expenditures": "use_capex", "Others": "use_other"}
    for k, v in c["funding"]["use_of_funds"].items():
        w.fill(ids[k], v)


def calculate(w: Page):
    with w.page.expect_response(lambda r: r.url.endswith("/valuations/preview")) as resp:
        w.page.click("#btn-calculate")
    result = resp.value.json()
    w.page.wait_for_selector(".headline-value")
    w.page.wait_for_timeout(300)
    return result


def money(n):
    n = 0 if abs(n) < 0.5 else n
    return ("-" if n < 0 else "") + "€" + f"{abs(n):,.0f}"


def truncated_inputs(page):
    """Number and date inputs whose value is wider than the visible box."""
    return page.evaluate("""() => {
      const ctx = document.createElement('canvas').getContext('2d');
      const out = [];
      for (const el of document.querySelectorAll('.wizard-step.is-active input')) {
        if (!el.value || !['number', 'date'].includes(el.type) || el.offsetParent === null) continue;
        const cs = getComputedStyle(el);
        ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        const text = el.type === 'date' ? '00/00/0000' : el.value;
        const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 18;
        if (ctx.measureText(text).width > room + 0.5) out.push(`${el.id}=${el.value}`);
      }
      return out;
    }""")


@pytest.fixture(params=list(VIEWPORTS))
def wizard(request, browser, servers):
    w = Page(browser, servers, VIEWPORTS[request.param])
    yield w
    w.close()


def test_werkpuls_walkthrough(wizard):
    w = wizard
    fill_werkpuls(w)
    assert w.page.is_hidden("#form-warning"), w.page.text_content("#form-warning")
    assert truncated_inputs(w.page) == []
    result = calculate(w)

    # The screen shows the API's numbers.
    shown = w.page.text_content(".headline-value").strip()
    assert shown == money(result["blended_pre_money_valuation"])
    assert money(result["post_money_valuation"]) in w.page.text_content(".ledger-postmoney-value")
    # The engine received exactly what was typed (umlauts included).
    payload = w.page.evaluate("lastResult.payload")
    assert payload["company_profile"]["address"] == "Fürther Straße 212, 90429 Nürnberg"
    assert payload["vc_assumptions"]["number_of_existing_shares"] == 25_000
    assert len(payload["ownership"]) == 5
    codes = {x["code"] for x in result["warnings"]}
    assert not codes & {"ownership_sum", "use_of_funds_sum", "revenue_jump"}

    # The PDF downloads, with umlauts intact and the same headline number.
    with w.page.expect_download() as dl:
        w.page.click("#btn-pdf")
    pdf = pdfplumber.open(io.BytesIO(Path(dl.value.path()).read_bytes()))
    text = "\n".join(p.extract_text() for p in pdf.pages)
    assert "Fürther Straße 212, 90429" in text and "Nürnberg" in text
    assert money(result["blended_pre_money_valuation"]) in text
    assert dl.value.suggested_filename == "Werkpuls-GmbH-Valuation-Report.pdf"

    assert w.failed == []
    assert w.errors == []


def test_projection_preview_matches_api(wizard):
    w = wizard
    fill_werkpuls(w, stop_after=4)
    with w.page.expect_response(lambda r: r.url.endswith("/preview/projections")):
        w.page.fill("#capex_y5", "80000")
    w.page.wait_for_selector(".suggested-table")
    w.page.wait_for_timeout(500)
    payload = w.page.evaluate("buildPayload()")
    api = w.api_post("/valuations/preview/projections", {k: payload[k] for k in (
        "company_profile", "operating_performance", "financial_assumptions")})
    rows = w.page.eval_on_selector_all(".suggested-table tbody tr", "rs => rs.map(r => r.innerText)")
    fcf_row = next(r for r in rows if r.startswith("Free cash flow"))
    assert fcf_row.split("\t")[1:] == [money(y["unlevered_fcf"]) for y in api["projections"]["years"]]
    assert truncated_inputs(w.page) == []


def test_scenario_slider_matches_api(wizard):
    w = wizard
    fill_werkpuls(w)
    calculate(w)
    payload = w.page.evaluate("lastResult.payload")
    scenarios = w.api_post("/valuations/preview/scenarios", payload)
    for label, s in scenarios.items():
        w.page.eval_on_selector("#scenario-slider", "(el, v) => { el.value = v; el.dispatchEvent(new Event('input')); }",
                                label.rstrip("%"))
        assert w.page.text_content("#scenario-slider-out") == label
        assert w.page.text_content("#scenario-blended") == money(s["blended_pre_money_valuation"])
        cells = w.page.eval_on_selector_all("#scenario-table-body tr td:last-child", "tds => tds.map(t => t.innerText)")
        for cell, (key, mv) in zip(cells, s["method_values"].items()):
            if mv["status"] == "ok":
                assert cell == money(mv["pre_money_value"]), key
    assert w.errors == []


@pytest.mark.parametrize("problem", ["ownership_80", "use_of_funds", "revenue_3x"])
def test_validation_messages(wizard, problem):
    w = wizard
    fill_werkpuls(w)
    if problem == "ownership_80":
        w.fill("shareholder_pct_1", 10)  # 80% in total
    elif problem == "use_of_funds":
        w.fill("use_other", 150_000)
    if problem in ("ownership_80", "use_of_funds"):
        with w.page.expect_response(lambda r: r.url.endswith("/valuations/preview")):
            w.page.click("#btn-calculate")
        w.page.wait_for_selector(".headline-value")
        text = w.page.text_content(".checks-list")
        assert ("Ownership adds up to 80.0%" if problem == "ownership_80" else "Use of funds adds up to") in text
        return
    # Year-1 revenue more than 3x the last 12 months.
    w.page.go_back()
    w.page.go_back()
    assert w.step() == 3
    w.fill("current_revenue_last_12_months", 200_000)
    w.fill("current_ebitda", -40_000)
    w.next()
    w.next()
    result = calculate(w)
    assert "revenue_jump" in {x["code"] for x in result["warnings"]}
    assert "275% above the last 12 months" in w.page.text_content(".checks-list")


def test_empty_required_field_is_named(wizard):
    w = wizard
    w.next()
    assert w.step() == 1
    assert "Company name is missing" in w.page.text_content("#form-error")


def test_back_and_forward_keep_data(wizard):
    w = wizard
    fill_werkpuls(w)
    calculate(w)
    w.page.go_back()
    assert w.step() == 5
    w.page.go_back()
    w.page.go_back()
    assert w.step() == 3
    assert w.page.input_value("#current_ebitda") == "-80000"
    w.page.go_forward()
    w.page.go_forward()
    assert w.step() == 5
    assert w.page.input_value("#shareholder_name_5") == "Employee option pool"
    w.page.go_forward()
    assert w.step() == 6
    assert w.page.is_visible(".headline-value")
    assert w.errors == []


def test_sample_company_and_result_buttons(wizard):
    w = wizard
    with w.page.expect_response(lambda r: r.url.endswith("/valuations/preview"), timeout=60_000):
        w.page.click("#btn-sample")
    w.page.wait_for_selector(".headline-value")
    assert w.page.text_content(".headline-value").startswith("€")
    w.page.click("#btn-edit")
    assert w.step() == 1 and w.page.input_value("#company_name")
    w.page.go_forward()  # back to the results
    w.page.wait_for_timeout(200)
    w.page.goto(w.page.url)  # a reload starts a clean form
    w.page.wait_for_function("document.getElementById('industry').options.length > 10")
    assert w.page.input_value("#company_name") == ""
    assert w.errors == [] and w.failed == []


def test_start_button_does_not_steal_the_cursor(wizard):
    """Typing in another box right after "Start" must stay in that box."""
    w = wizard
    w.page.goto(w.page.url)
    w.page.wait_for_function("document.getElementById('industry').options.length > 10")
    w.page.click("#btn-start")
    w.page.focus("#contact_name")
    w.page.keyboard.type("Lena Hartmann", delay=60)  # takes longer than the page's 0.4 s focus delay
    assert w.page.input_value("#contact_name") == "Lena Hartmann"
    assert w.page.input_value("#company_name") == ""


def test_start_button_puts_the_cursor_in_company_name(wizard):
    w = wizard
    w.page.wait_for_timeout(600)
    assert w.page.evaluate("document.activeElement.id") == "company_name"
