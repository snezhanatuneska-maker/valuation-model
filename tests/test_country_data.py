"""Country risk data: internal consistency with Damodaran's method, the refresh
tool's parser, and (when DAMODARAN_DIR holds ctryprem.xlsx) every value."""
import json
import os
from pathlib import Path

import pytest

import refresh_country_data as rcd
import valuation_engine as ve

MATURE_ERP = 0.0423  # Damodaran's implied US premium, January 2026


def test_premiums_follow_damodarans_method():
    """ERP = mature-market premium + CRP, and CRP = default spread x one common
    equity/bond volatility ratio (within rounding to 0.01%)."""
    ratios = []
    for country, c in ve.country_data().items():
        assert c["equity_risk_premium"] == pytest.approx(MATURE_ERP + c["country_risk_premium"], abs=1.5e-4), country
        if c["adjusted_default_spread"] > 0.005:
            ratios.append(c["country_risk_premium"] / c["adjusted_default_spread"])
    assert max(ratios) - min(ratios) < 0.02


def test_aaa_countries_have_no_country_premium():
    for country, c in ve.country_data().items():
        if c["moodys_rating"] == "Aaa":
            assert c["country_risk_premium"] == 0, country


def _write_damodaran_like_xlsx(path: Path, data: dict):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ERPs by country"
    ws.append(["Country risk premiums, January 2026"])
    ws.append([])
    ws.append(["Country", "Region", "Moody's rating", "Adj. Default Spread", "Equity Risk Premium",
               "Country Risk Premium", "Corporate Tax Rate", "Sovereign CDS, net of US"])
    ws.append(["Africa", None, None, None, None, None, None, None])  # a section label, no numbers
    for name, c in data.items():
        file_name = "North Macedonia" if name == "Macedonia" else name
        ws.append([file_name, c["region_grouping"], c["moodys_rating"], c["adjusted_default_spread"],
                   c["equity_risk_premium"], c["country_risk_premium"], c["corporate_tax_rate"],
                   c["sovereign_cds"] if isinstance(c["sovereign_cds"], float) else "NA"])
    wb.save(path)


def test_refresh_tool_reads_damodaran_layout(tmp_path, capsys):
    data = ve.country_data()
    xlsx = tmp_path / "ctryprem.xlsx"
    _write_damodaran_like_xlsx(xlsx, data)
    parsed = rcd.read_countries(xlsx)
    assert set(parsed) == set(data)  # "North Macedonia" mapped back to "Macedonia"
    assert parsed["Tanzania"]["equity_risk_premium"] == pytest.approx(0.1006)

    ref = tmp_path / "reference_data.json"
    ref.write_text(json.dumps(ve._all_reference_data()), encoding="utf-8")
    rcd.sys.argv = ["refresh_country_data.py", str(xlsx), str(ref)]
    rcd.main()
    assert "0 values differ, 0 new countries, 0 missing" in capsys.readouterr().out


def test_refresh_tool_writes_changes(tmp_path):
    data = json.loads(json.dumps(ve.country_data()))
    data["Tanzania"]["equity_risk_premium"] = 0.11
    data["Atlantis"] = dict(data["Tanzania"], region_grouping="Africa")
    xlsx = tmp_path / "ctryprem.xlsx"
    _write_damodaran_like_xlsx(xlsx, data)
    ref = tmp_path / "reference_data.json"
    ref.write_text(json.dumps(ve._all_reference_data()), encoding="utf-8")
    rcd.sys.argv = ["refresh_country_data.py", str(xlsx), str(ref), "--write"]
    rcd.main()
    out = json.loads(ref.read_text(encoding="utf-8"))
    assert out["country_data"]["Tanzania"]["equity_risk_premium"] == 0.11
    assert "Atlantis" in out["categorical_options"]["country"]


DAMODARAN_DIR = os.environ.get("DAMODARAN_DIR")


@pytest.mark.skipif(not DAMODARAN_DIR or not list(Path(DAMODARAN_DIR or ".").glob("ctryprem*.xls*")),
                    reason="put Damodaran's ctryprem.xlsx in DAMODARAN_DIR to check every country")
def test_country_data_matches_damodaran_file(capsys):
    xlsx = sorted(Path(DAMODARAN_DIR).glob("ctryprem*.xls*"))[0]
    root = Path(__file__).resolve().parent.parent
    rcd.sys.argv = ["refresh_country_data.py", str(xlsx), str(root / "valuation_engine" / "reference_data.json")]
    rcd.main()
    assert "\n0 values differ" in capsys.readouterr().out
