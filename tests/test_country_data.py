"""Country risk data: consistency with Damodaran's method, the refresh tool's
parser, and every value against the workbook in source_data/damodaran."""
import json
from pathlib import Path

import pytest

import refresh_country_data as rcd
import valuation_engine as ve

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source_data" / "damodaran" / "ctrypremJuly26.xlsx"


def test_premiums_follow_damodarans_method():
    """ERP = mature-market premium + CRP, and CRP = default spread x one common
    equity/bond volatility ratio."""
    mature = ve.market_parameters()["mature_market_premium"]
    ratios = []
    for country, c in ve.country_data().items():
        if country == "United States":
            # Damodaran's anchor: the US premium is the S&P 500's implied premium (4.42% in
            # July 2026); the mature-market premium is that minus the US default spread.
            assert c["equity_risk_premium"] == pytest.approx(mature + c["adjusted_default_spread"], abs=1e-4)
            continue
        assert c["equity_risk_premium"] == pytest.approx(mature + c["country_risk_premium"], abs=1e-6), country
        if c["adjusted_default_spread"] > 0.005:
            ratios.append(c["country_risk_premium"] / c["adjusted_default_spread"])
    assert max(ratios) - min(ratios) < 1e-6


def test_aaa_countries_have_no_country_premium():
    for country, c in ve.country_data().items():
        if c["moodys_rating"] == "Aaa":
            assert c["country_risk_premium"] == 0, country


def test_every_wizard_country_has_data():
    assert sorted(ve.country_data()) == ve.categorical_options()["country"]
    for country, c in ve.country_data().items():
        assert isinstance(c["corporate_tax_rate"], float) and 0 <= c["corporate_tax_rate"] <= 0.6, country


def test_country_data_matches_damodaran_workbook(capsys):
    rcd.sys.argv = ["refresh_country_data.py", str(SOURCE), str(ROOT / "valuation_engine" / "reference_data.json")]
    rcd.main()
    out = capsys.readouterr().out
    assert "update date 2026-07-01" in out and "mature-market premium 0.042" in out
    assert "\n0 values differ, 0 new countries, 0 missing" in out


def _write_workbook(path: Path, rated: dict, frontier: dict, mature=0.042):
    import datetime as dt

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ERPs by country"
    ws.append(["Country and Equity Risk Premiums"])
    ws.append(["Date of update:", None, dt.datetime(2026, 7, 1)])
    ws.append(["Enter the current risk premium for a mature equity market", None, None, mature])
    ws.append([])
    ws.append(["Frontier Markets (no sovereign ratings)"])
    ws.append(["Country", "PRS Composite Risk Score", "ERP", "CRP", "Default Spread"])
    for name, c in frontier.items():
        ws.append([name, 70.5, c["equity_risk_premium"], c["country_risk_premium"], c["adjusted_default_spread"]])
    rb = wb.create_sheet("Regional breakdown")
    rb.append(["Country", "GDP (in millions) in 2024", "Moody's rating", "Sovereign CDS", "Adj. Default Spread",
               "Equity Risk Premium", "Country Risk Premium", "Corporate Tax Rate", "Region"])
    for name, c in rated.items():
        rb.append([name, 1000.0, c["moodys_rating"], "NA", c["adjusted_default_spread"],
                   c["equity_risk_premium"], c["country_risk_premium"], c["corporate_tax_rate"], c["region_grouping"]])
    wb.save(path)


def test_refresh_tool_updates_adds_and_keeps(tmp_path):
    current = ve.country_data()
    rated = {"Tanzania": dict(current["Tanzania"], equity_risk_premium=0.11, country_risk_premium=0.068),
             "North Macedonia": current["Macedonia"],
             "Atlantis": dict(current["Tanzania"], region_grouping="Africa")}
    frontier = {"Russia": dict(current["Russia"], equity_risk_premium=0.09), "Iran": current["Russia"]}
    xlsx = tmp_path / "ctryprem.xlsx"
    _write_workbook(xlsx, rated, frontier)
    data = rcd.read_workbook(xlsx)
    assert data["mature_premium"] == 0.042 and str(data["update_date"]) == "2026-07-01"
    assert "Macedonia" in data["rated"]  # name alias applied

    ref = tmp_path / "reference_data.json"
    ref.write_text(json.dumps(ve._all_reference_data()), encoding="utf-8")
    rcd.sys.argv = ["refresh_country_data.py", str(xlsx), str(ref), "--write"]
    rcd.main()
    out = json.loads(ref.read_text(encoding="utf-8"))["country_data"]
    assert out["Tanzania"]["equity_risk_premium"] == 0.11
    assert "Atlantis" in out  # new rated country added
    assert out["Russia"]["equity_risk_premium"] == 0.09  # frontier country already in the app: updated
    assert out["Russia"]["corporate_tax_rate"] == current["Russia"]["corporate_tax_rate"]  # tax kept
    assert "Iran" not in out  # frontier country with no tax rate: not added
    assert out["Germany"] == current["Germany"]  # not in the file: kept unchanged
