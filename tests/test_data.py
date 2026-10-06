"""Reference-data tests: structure, sources, and (when the spreadsheets are
available) every value against Damodaran's January 2026 files."""
import os
from pathlib import Path

import pytest

import valuation_engine as ve

REGIONS = list(ve.stage_region_pre_money_benchmarks()["Startup stage"].keys())


def test_every_industry_has_every_metric_and_region():
    metrics = set(next(iter(ve.industry_benchmarks().values())))
    assert {"ebitda_margin", "rd_pct_revenue", "ev_ebitda_multiple", "beta", "cost_of_debt"} <= metrics
    for industry, table in ve.industry_benchmarks().items():
        assert set(table) == metrics, industry
        for metric in metrics:
            assert set(REGIONS) <= set(table[metric]), (industry, metric)


def test_every_benchmark_resolves():
    for industry in ve.industry_benchmarks():
        for region in REGIONS:
            for metric in ("ebitda_margin", "ev_ebitda_multiple", "beta", "cost_of_debt", "da_pct_revenue"):
                ve.get_industry_metric(industry, metric, region)


def test_stage_parameters_are_complete():
    for stage, p in ve.stage_parameters().items():
        assert set(p) == {"vc_target_return", "private_company_discount", "survival_probability", "method_weights"}
        assert sum(p["method_weights"].values()) == pytest.approx(1.0), stage
        assert set(p["method_weights"]) == {"scorecard", "venture_capital", "comparables", "dcf"}


def test_every_assumption_has_a_source_note():
    sources = ve.data_sources()
    for key in ("industry_benchmarks", "country_data", "risk_free_rate", "vc_target_return",
                "private_company_discount", "survival_probability", "stage_region_pre_money_benchmarks"):
        assert sources.get(key), key


def test_scorecard_aliases_point_to_current_options():
    lookup = ve.scorecard_qualitative_lookup()
    for criterion, mapping in ve.scorecard_option_aliases().items():
        for new in mapping.values():
            assert new in lookup[criterion]


def test_country_data_is_usable():
    for country, c in ve.country_data().items():
        assert 0 <= c["corporate_tax_rate"] <= 0.6, country
        assert 0.02 <= c["equity_risk_premium"] <= 0.4, country


DAMODARAN_DIR = os.environ.get("DAMODARAN_DIR") or str(
    Path(__file__).resolve().parent.parent / "source_data" / "damodaran")


def test_benchmarks_match_damodaran_files(capsys):
    import check_benchmarks
    check_benchmarks.sys.argv = ["check_benchmarks.py", DAMODARAN_DIR]
    check_benchmarks.main()
    assert ", 0 mismatches" in capsys.readouterr().out
