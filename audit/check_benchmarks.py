"""
Audit-only: compares valuation_engine/reference_data.json against Damodaran's
January 2026 regional spreadsheets. Independent of refresh_industry_benchmarks.py
(its own parser), so it can catch mistakes in that script too.

Usage:  python audit/check_benchmarks.py <folder with Damodaran .xls files> [industry ...]
Needs:  pip install xlrd
Not used by the app.
"""
import json
import re
import sys
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parent.parent
REF = json.loads((ROOT / "valuation_engine" / "reference_data.json").read_text(encoding="utf-8"))
BENCH = REF["industry_benchmarks"]

REGION_SUFFIX = {
    "US": "",
    "Europe (EU, UK, Switzerland & Scandinavia)": "Europe",
    "Japan": "Japan",
    "Emerging Markets (Asia, Latin America, Eastern Europe, Mid East and Africa)": "emerg",
    "China": "China",
    "India": "India",
    "Global": "Global",
}
# metric -> (file stem, header text (lower-case, whitespace-collapsed), occurrence)
DIRECT = {
    "ev_ebitda_multiple": ("vebitda", "ev/ebitda", 0),
    "beta": ("wacc", "beta", 0),
    "cost_of_equity": ("wacc", "cost of equity", 0),
    "equity_pct_capital": ("wacc", "e/(d+e)", 0),
    "debt_pct_capital": ("wacc", "d/(d+e)", 0),
    "cost_of_debt": ("wacc", "cost of debt", 0),
    "cogs_pct_revenue": ("margin", None, 0),       # = 1 - gross margin (see below)
    "sga_pct_revenue": ("margin", None, 0),        # = EBITDASG&A/Sales - EBITDA/Sales
    "acc_receivable_pct_revenue": ("wcdata", "acc rec/ sales", 0),
    "inventory_pct_revenue": ("wcdata", "inventory/sales", 0),
    "acc_payable_pct_revenue": ("wcdata", "acc pay/ sales", 0),
    "ebitda_margin": ("margin", "ebitda/sales", 0),
    "rd_pct_revenue": ("margin", "r&d/sales", 0),
}
# App industries whose figures come from a differently named Damodaran industry.
SOURCE_ALIASES = {"Retail (Online)": "Retail (General)"}
MIN_FIRMS = 10
TOL = 1e-6


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def load(stem, suffix, folder):
    path = folder / f"{stem}{suffix}.xls"
    book = xlrd.open_workbook(str(path))
    sh = book.sheet_by_name("Industry Averages")
    hdr_row = next(r for r in range(sh.nrows) if norm(sh.cell_value(r, 0)) == "industry name")
    headers = [norm(h) for h in sh.row_values(hdr_row)]
    rows = {}
    for r in range(hdr_row + 1, sh.nrows):
        name = str(sh.cell_value(r, 0)).strip()
        if name:
            rows[name] = sh.row_values(r)
    return headers, rows


def col(headers, text, occurrence=0):
    hits = [i for i, h in enumerate(headers) if h == text]
    return hits[occurrence] if len(hits) > occurrence else None


def source_value(metric, industry, region, folder, cache):
    stem, header, occ = DIRECT[metric]
    key = (stem, region)
    if key not in cache:
        cache[key] = load(stem, REGION_SUFFIX[region], folder)
    headers, rows = cache[key]
    row = rows.get(SOURCE_ALIASES.get(industry, industry))
    if row is None:
        return "MISSING_ROW", None
    n_firms = row[1]
    try:
        if metric == "cogs_pct_revenue":
            v = 1 - float(row[col(headers, "gross margin")])
        elif metric == "sga_pct_revenue":
            v = float(row[col(headers, "ebitdasg&a/sales")]) - float(row[col(headers, "ebitda/sales")])
        else:
            v = float(row[col(headers, header, occ)])
    except (TypeError, ValueError):
        return "NA", n_firms
    return v, n_firms


def main():
    folder = Path(sys.argv[1])
    industries = sys.argv[2:] or [
        "Software (Entertainment)", "Software (System & Application)", "Retail (General)",
        "Restaurant/Dining", "Business & Consumer Services", "Drugs (Pharmaceutical)",
        "Bank (Money Center)", "Retail (Online)", "Drugs (Biotechnology)",
    ]
    cache = {}
    mismatches = 0
    checked = 0
    for ind in industries:
        for metric in DIRECT:
            for region in REGION_SUFFIX:
                app_v = BENCH[ind][metric].get(region)
                src_v, n = source_value(metric, ind, region, folder, cache)
                if isinstance(n, float) and n < MIN_FIRMS and region != "Global":
                    expected = "NA (<10 firms)"
                else:
                    expected = src_v
                checked += 1
                ok = (
                    (isinstance(expected, float) and isinstance(app_v, (int, float))
                     and abs(expected - app_v) <= TOL * max(1, abs(expected)))
                    or (not isinstance(expected, float) and app_v == "NA")
                )
                if not ok:
                    mismatches += 1
                    print(f"MISMATCH {ind} | {metric} | {region}: app={app_v!r} source={src_v!r} firms={n}")
    print(f"checked {checked} values, {mismatches} mismatches")


if __name__ == "__main__":
    main()
