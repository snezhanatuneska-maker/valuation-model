"""audit/backtest.py: values each round in a CSV and compares the model with the price paid.

The rows below are made up to test the tool; they are not real rounds."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audit"))
import backtest  # noqa: E402

HEADER = (Path(__file__).resolve().parent.parent / "audit" / "backtest_template.csv").read_text().strip().split(",")
SAMPLE = {"source": "made up for this test", "round_date": "2026-06-30", "country": "Germany",
          "industry": "Software (System & Application)", "stage": "Startup stage", "ltm_revenue": "300000",
          "ltm_ebitda": "-100000", "capital_needed": "1500000", "growth_rate": "0.8"}


def _write(tmp_path, rows):
    path = tmp_path / "rounds.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_template_has_the_required_and_questionnaire_columns():
    assert set(backtest.REQUIRED) <= set(HEADER) and set(backtest.QUESTIONNAIRE_KEYS) <= set(HEADER)


def test_backtest_values_each_round_and_summarizes(tmp_path):
    exact = backtest.run_case({**SAMPLE, "name": "probe", "actual_pre_money": "1"})
    rows = [
        {**SAMPLE, "name": "A (price = model)", "actual_pre_money": f"{exact.model:.2f}"},
        {**SAMPLE, "name": "B (price = 2x model)", "actual_pre_money": f"{2 * exact.model:.2f}",
         "management_team_experience": "Demonstrated experience as a CEO"},
        {**SAMPLE, "name": "C (plan per year)", "actual_pre_money": "3000000", "growth_rate": "",
         **{f"revenue_y{i}": str(400_000 * i) for i in range(1, 6)}},
        {**SAMPLE, "name": "D (no plan)", "actual_pre_money": "3000000", "growth_rate": ""},
    ]
    results = [backtest.run_case(r) for r in backtest.load(_write(tmp_path, rows))]
    assert results[0].ratio == pytest.approx(1.0) and results[0].in_range
    assert results[1].ratio < 0.6
    assert results[2].model > 0 and results[2].error is None
    assert results[3].model is None and "revenue_y1" in results[3].error
    s = backtest.summarize(results)
    assert (s.cases, s.valued) == (4, 3)
    assert s.within_25 == pytest.approx(1 / 3 + (1 / 3 if abs(results[2].ratio - 1) <= 0.25 else 0))
    text = backtest.report(results, "test")
    assert "3 of 4 rounds valued" in text and "not valued: give revenue_y1" in text


def test_command_line_writes_markdown(tmp_path):
    path = _write(tmp_path, [{**SAMPLE, "name": "A", "actual_pre_money": "2500000"}])
    out = tmp_path / "report.md"
    assert backtest.main([str(path), "--out", str(out)]) == 0
    assert "| A | Startup stage | Germany | €2,500,000 |" in out.read_text()
