"""Test the model against real financing rounds with known prices.

Each row of the CSV is one closed round: what the company looked like when the round closed (country, industry,
stage, last-12-month figures, plan, amount raised) and the pre-money valuation the investors actually paid. The
script values every company with the engine and reports how far the model is from the actual price.

    python audit/backtest.py rounds.csv                 # table and summary in the terminal
    python audit/backtest.py rounds.csv --out report.md  # also writes the result as Markdown

Start from audit/backtest_template.csv. Only real rounds belong in the file, each with its source (press release,
Handelsregister filing, Dealroom/PitchBook entry). Columns (empty = not known):

  name, source, round_date (YYYY-MM-DD), actual_pre_money (EUR)          required
  country, industry, stage                                              required, as in the wizard
  ltm_revenue, ltm_ebitda, capital_needed                               required (EUR)
  revenue_y1 ... revenue_y5   or   growth_rate                          one of the two: the plan, or one yearly
                                                                        growth rate applied to ltm_revenue
  cash, debt, arr, exit_years, target_ebitda_margin                     optional
  any questionnaire key (e.g. management_team_experience)               optional, the wizard's answer text;
                                                                        unanswered questions count as typical

Revenue is planned as given; capex is 0 unless the plan says otherwise (rounds rarely disclose it), ownership and
use of funds are filled in so they don't raise input warnings.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import valuation_engine as ve  # noqa: E402

REQUIRED = ["name", "source", "round_date", "actual_pre_money", "country", "industry", "stage", "ltm_revenue",
            "ltm_ebitda", "capital_needed"]
QUESTIONNAIRE_KEYS = list(ve.MarketAndTeamAssessment.model_fields)


@dataclass
class CaseResult:
    name: str
    stage: str
    country: str
    actual: float
    model: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    error: Optional[str] = None  # why the case couldn't be valued

    @property
    def ratio(self) -> Optional[float]:
        return self.model / self.actual if self.model else None

    @property
    def in_range(self) -> Optional[bool]:
        if self.model is None:
            return None
        low, high = (self.low, self.high) if self.low is not None else (self.model, self.model)
        return low <= self.actual <= high


def _num(row: dict, key: str) -> Optional[float]:
    v = (row.get(key) or "").strip().replace(",", "")
    return float(v) if v else None


def case_inputs(row: dict) -> ve.ValuationInput:
    missing = [k for k in REQUIRED if not (row.get(k) or "").strip()]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    ltm = _num(row, "ltm_revenue")
    plan = [_num(row, f"revenue_y{i}") for i in range(1, 6)]
    growth = _num(row, "growth_rate")
    if all(p is not None for p in plan):
        revenue = {"revenue_by_year": plan}
    elif growth is not None:
        if ltm <= 0:
            raise ValueError("growth_rate needs ltm_revenue above 0; give revenue_y1 ... revenue_y5 instead")
        revenue = {"revenue_by_year": [ltm * (1 + growth) ** i for i in range(1, 6)]}
    else:
        raise ValueError("give revenue_y1 ... revenue_y5 or growth_rate")
    raise_amount = _num(row, "capital_needed")
    profile = {
        "company_name": row["name"].strip(),
        "valuation_date": row["round_date"].strip(),
        "country": row["country"].strip(),
        "industry": row["industry"].strip(),
        "company_stage": row["stage"].strip(),
        "business_territory_region": ve.default_region_for_country(ve.country_aliases().get(
            row["country"].strip(), row["country"].strip())),
    }
    if _num(row, "exit_years") is not None:
        profile["planned_time_to_exit_years"] = int(_num(row, "exit_years"))
    financials = {**revenue, "capex_by_year": [0, 0, 0, 0, 0], "existing_debt_balance": _num(row, "debt") or 0.0}
    if _num(row, "target_ebitda_margin") is not None:
        financials["target_ebitda_margin_override"] = _num(row, "target_ebitda_margin")
    return ve.ValuationInput(
        company_profile=profile,
        market_and_team_assessment={k: row[k].strip() for k in QUESTIONNAIRE_KEYS if (row.get(k) or "").strip()},
        operating_performance={"current_revenue_last_12_months": ltm, "current_ebitda": _num(row, "ltm_ebitda"),
                               "cash_available": _num(row, "cash") or 0.0,
                               "annual_recurring_revenue": _num(row, "arr")},
        financial_assumptions=financials,
        ownership=[{"name": "Shareholders", "ownership_pct": 1.0}],
        funding={"capital_needed": raise_amount, "use_of_funds": {"Round": raise_amount}},
    )


def run_case(row: dict) -> CaseResult:
    result = CaseResult(name=(row.get("name") or "?").strip(), stage=(row.get("stage") or "").strip(),
                        country=(row.get("country") or "").strip(), actual=_num(row, "actual_pre_money") or 0.0)
    try:
        if result.actual <= 0:
            raise ValueError("actual_pre_money must be above 0")
        out = ve.run_valuation(case_inputs(row))
    except (ValueError, KeyError, ve.ValuationError) as e:  # pydantic's ValidationError is a ValueError
        result.error = " ".join(str(e).split())[:200]
        return result
    result.model = out.blended_pre_money_valuation
    if out.value_range:
        result.low, result.high = out.value_range.low, out.value_range.high
    return result


@dataclass
class Summary:
    cases: int
    valued: int
    median_ratio: Optional[float]  # model / actual; below 1 = the model is lower than the price paid
    median_abs_error: Optional[float]  # median of |model / actual - 1|
    within_25: Optional[float]  # share of valued cases within ±25% of the price paid
    within_50: Optional[float]
    in_range: Optional[float]  # share whose price paid lies inside the model's range


def summarize(results: list[CaseResult]) -> Summary:
    valued = [r for r in results if r.ratio is not None]
    if not valued:
        return Summary(len(results), 0, None, None, None, None, None)
    ratios = [r.ratio for r in valued]
    errors = [abs(x - 1) for x in ratios]
    share = lambda xs: sum(xs) / len(valued)  # noqa: E731
    return Summary(
        cases=len(results), valued=len(valued),
        median_ratio=statistics.median(ratios),
        median_abs_error=statistics.median(errors),
        within_25=share([e <= 0.25 for e in errors]),
        within_50=share([e <= 0.50 for e in errors]),
        in_range=share([bool(r.in_range) for r in valued]),
    )


def load(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.DictReader(f) if any((v or "").strip() for v in row.values())]


def _eur(x: Optional[float]) -> str:
    return "—" if x is None else f"€{x:,.0f}"


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def report(results: list[CaseResult], title: str) -> str:
    s = summarize(results)
    lines = [f"# Backtest: {title}", "",
             f"{s.valued} of {s.cases} rounds valued.", ""]
    if s.valued:
        lines += [
            "| Measure | Result |", "|---|---|",
            f"| Median model value ÷ price paid | {s.median_ratio:.2f} |",
            f"| Median deviation from the price paid | {_pct(s.median_abs_error)} |",
            f"| Within ±25% of the price paid | {_pct(s.within_25)} |",
            f"| Within ±50% of the price paid | {_pct(s.within_50)} |",
            f"| Price paid inside the model's range | {_pct(s.in_range)} |", "",
        ]
        by_stage = {}
        for r in results:
            if r.ratio is not None:
                by_stage.setdefault(r.stage, []).append(r.ratio)
        lines += ["| Stage | Rounds | Median model ÷ price |", "|---|---|---|"]
        lines += [f"| {k} | {len(v)} | {statistics.median(v):.2f} |" for k, v in sorted(by_stage.items())]
        lines.append("")
    lines += ["| Company | Stage | Country | Price paid | Model (central) | Model range | Model ÷ price |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        if r.error:
            lines.append(f"| {r.name} | {r.stage} | {r.country} | {_eur(r.actual)} | not valued: {r.error} | | |")
        else:
            rng = f"{_eur(r.low)}–{_eur(r.high)}" if r.low is not None else "—"
            lines.append(f"| {r.name} | {r.stage} | {r.country} | {_eur(r.actual)} | {_eur(r.model)} | {rng} | "
                         f"{r.ratio:.2f} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path, help="also write the result as Markdown")
    args = parser.parse_args(argv)
    results = [run_case(row) for row in load(args.csv)]
    text = report(results, args.csv.name)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0 if all(math.isfinite(r.ratio) for r in results if r.ratio is not None) else 1


if __name__ == "__main__":
    sys.exit(main())
