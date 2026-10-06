"""
Refresh industry_benchmarks in valuation_engine/reference_data.json from
Damodaran's regional industry datasets (pages.stern.nyu.edu/~adamodar/pc/datasets/).

Offline maintenance tool - not used by the app at runtime.
Needs: pip install xlrd openpyxl

Usage:
    python refresh_industry_benchmarks.py <folder with the files> valuation_engine/reference_data.json [--write]

Without --write it only prints a report (coverage, unmatched industries, and
old-vs-new medians per metric/region) so the column mapping can be checked
before anything changes.

Expected files (one set per region; Damodaran's own filenames):
    US: vebitda.xls  wacc.xls  margin.xls  wcdata.xls  dbtfund.xls
    Europe: vebitdaEurope.xls ...   Japan: ...Japan.xls   Emerging: ...emerg.xls
    China: ...China.xls   India: ...India.xls   Global: ...Global.xls

Columns are located by header text, never by position, and the script stops
with the list of headers it saw if a required one is missing. Non-numeric
cells ("NA", "#DIV/0!", blanks) are stored as "NA", which the engine's
fallback chain already handles. An industry missing from a file keeps its
prior values and is listed as UNMATCHED.
"""
import json
import re
import statistics
import sys
from pathlib import Path

import openpyxl  # .xlsx
import xlrd  # Damodaran ships legacy .xls

REGIONS = {
    "US": "",
    "Europe (EU, UK, Switzerland & Scandinavia)": "Europe",
    "Japan": "Japan",
    "Emerging Markets (Asia, Latin America, Eastern Europe, Mid East and Africa)": "emerg",
    "China": "China",
    "India": "India",
    "Global": "Global",
}

# A regional industry average built from fewer firms than this is treated as
# missing ("NA"), so the engine falls back to that industry's Global figure
# (every Global average has 30+ firms). Jan 2026 examples it removes: India
# Precious Metals (1 firm, SG&A = 70x sales), Japan Aerospace/Defense (5 firms,
# EBITDA margin -170%) - both produced large negative valuations.
MIN_FIRMS = 10

# metric -> (file stem, header matcher, which occurrence if the header repeats)
# vebitda.xls repeats its headers: first block = positive-EBITDA firms only,
# second block = all firms. The first block is the one the prior data used
# (checked Jan 2026: new/old median ratio ~1.0 vs ~1.2 for all firms).
METRICS = {
    "ev_ebitda_multiple": ("vebitda", r"^ev/ebitda$", 0),
    "beta": ("wacc", r"^beta$", 0),
    "cost_of_equity": ("wacc", r"^cost of equity$", 0),
    "equity_pct_capital": ("wacc", r"^e/\(d\+e\)$", 0),
    "cost_of_debt": ("wacc", r"^cost of debt$", 0),
    "debt_pct_capital": ("wacc", r"^d/\(d\+e\)$", 0),
    "cogs_pct_revenue": ("margin", r"^cogs/sales$", 0),
    "sga_pct_revenue": ("margin", r"^sg&a/sales$", 0),
    "acc_receivable_pct_revenue": ("wcdata", r"^acc rec/sales$", 0),
    "inventory_pct_revenue": ("wcdata", r"^inventory/sales$", 0),
    "acc_payable_pct_revenue": ("wcdata", r"^acc pay/sales$", 0),
    # The engine projects EBITDA from this margin directly: COGS% + SG&A% alone
    # leave out R&D, which overstated EBITDA by 10-18 points for software/pharma.
    "ebitda_margin": ("margin", r"^ebitda/sales$", 0),
    "rd_pct_revenue": ("margin", r"^r&d/sales$", 0),
}

# Industries the app lists that Damodaran no longer publishes under that name:
# app industry -> the Damodaran industry whose figures are used instead.
# Damodaran's current files class online retailers under Retail (General).
SOURCE_ALIASES = {"Retail (Online)": "Retail (General)"}

# Metrics Damodaran doesn't publish directly, derived from published ratios.
# Results outside a plausible range (e.g. negative D&A for insurers, where the
# operating margin includes items EBITDA doesn't) become "NA" so the engine's
# fallback chain supplies a value instead.
EBITDA_MARGIN = ("margin", r"^ebitda/sales$")
EBIT_MARGIN = ("margin", r"^pre-tax unadjusted operating margin$")
COVERAGE = ("dbtfund", r"^interest coverage ratio$")  # EBIT / interest
DEBT_TO_EBITDA = ("dbtfund", r"^debt to ebitda$")


def derive_da(v):
    """D&A / sales = EBITDA/sales - EBIT/sales."""
    return v(EBITDA_MARGIN) - v(EBIT_MARGIN), (0.0, 0.35)


def derive_book_interest_rate(v):
    """Interest / debt = (EBIT / coverage) / (Debt/EBITDA * EBITDA)
    = (EBIT margin / EBITDA margin) / (coverage * Debt/EBITDA)."""
    return (v(EBIT_MARGIN) / v(EBITDA_MARGIN)) / (v(COVERAGE) * v(DEBT_TO_EBITDA)), (0.0, 0.25)


DERIVED = {"da_pct_revenue": derive_da, "book_interest_rate": derive_book_interest_rate}


def norm(s):
    return re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(s))).strip().lower()


def sheet_rows(path):
    if path.suffix.lower() == ".xlsx":
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = next((ws for ws in book.worksheets if "industry averages" in ws.title.lower()), book.worksheets[0])
        return [["" if c is None else c for c in row] for row in ws.iter_rows(values_only=True)]
    book = xlrd.open_workbook(str(path))
    sheet = next((s for s in book.sheets() if "industry averages" in s.name.lower()), book.sheet_by_index(0))
    return [sheet.row_values(i) for i in range(sheet.nrows)]


def load_sheet(path):
    rows = sheet_rows(path)
    for r, row in enumerate(rows[:30]):
        headers = [norm(c) for c in row]
        if "industry name" in headers:
            return headers, rows[r + 1:]
    raise ValueError(f"{path.name}: no 'Industry Name' header row found")


def find_file(folder, stem, suffix):
    """US files are just <stem>.xls; regional ones are <stem><Region>.xls. Case-insensitive,
    .xls or .xlsx, and tolerant of browser-added suffixes like ' (1)'."""
    for p in sorted(folder.iterdir()):
        base = re.sub(r"\s*\(\d+\)$", "", p.stem).lower()
        if p.suffix.lower() in (".xls", ".xlsx") and base == f"{stem}{suffix}".lower():
            return p
    raise FileNotFoundError(f"missing {stem}{suffix}.xls in {folder}")


def column(headers, pattern, occurrence, fname):
    hits = [i for i, h in enumerate(headers) if re.match(pattern, h)]
    if len(hits) <= occurrence:
        raise ValueError(f"{fname}: no column matching {pattern!r}; headers are {headers}")
    return hits[occurrence]


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else "NA"


def too_few_firms(headers, row):
    n = num(row[headers.index("number of firms")])
    return n == "NA" or n < MIN_FIRMS


def read_file(folder, stem, suffix, cache={}):
    key = (stem, suffix)
    if key not in cache:
        path = find_file(folder, stem, suffix)
        headers, body = load_sheet(path)
        name_col = headers.index("industry name")
        rows = {}
        for row in body:
            name = str(row[name_col]).strip()
            if not name or name.lower().startswith("total market"):
                continue
            rows[norm(name)] = row
        cache[key] = (path.name, headers, rows)
    return cache[key]


def main():
    folder, ref_path = Path(sys.argv[1]), Path(sys.argv[2])
    write = "--write" in sys.argv
    ref = json.loads(ref_path.read_text())
    old = ref["industry_benchmarks"]
    new = {ind: {m: dict(v) for m, v in metrics.items()} for ind, metrics in old.items()}
    for ind in new:
        for metric in list(METRICS) + list(DERIVED):
            new[ind].setdefault(metric, {})
    unmatched = {}

    for region, suffix in REGIONS.items():
        for metric, (stem, pattern, occ) in METRICS.items():
            fname, headers, rows = read_file(folder, stem, suffix)
            col = column(headers, pattern, occ, fname)
            for ind in old:
                row = rows.get(norm(SOURCE_ALIASES.get(ind, ind)))
                if row is None:  # not in Damodaran's file: keep the prior value, report it
                    unmatched.setdefault(f"{stem}{suffix}", set()).add(ind)
                elif too_few_firms(headers, row):
                    new[ind][metric][region] = "NA"
                else:
                    new[ind][metric][region] = num(row[col])
        for ind in old:
            src = SOURCE_ALIASES.get(ind, ind)
            if norm(src) not in read_file(folder, EBITDA_MARGIN[0], suffix)[2]:
                continue  # already reported as unmatched; prior values kept
            def v(source, src=src):
                fname, headers, rows = read_file(folder, source[0], suffix)
                row = rows.get(norm(src))
                if row is None or too_few_firms(headers, row):
                    raise ValueError
                x = num(row[column(headers, source[1], 0, fname)])
                if x == "NA" or x == 0:
                    raise ValueError
                return x
            for metric, derive in DERIVED.items():
                try:
                    x, (lo, hi) = derive(v)
                    new[ind][metric][region] = x if lo < x < hi else "NA"
                except (ValueError, ZeroDivisionError):
                    new[ind][metric][region] = "NA"

    for f, inds in sorted(unmatched.items()):
        print(f"UNMATCHED in {f}: {sorted(inds)}")
    print(f"\n{'metric':28} {'region':10} {'old median':>11} {'new median':>11} {'NA old':>6} {'NA new':>6}")
    for metric in new[next(iter(new))]:
        for region in REGIONS:
            o = [old[i].get(metric, {}).get(region) for i in old]
            n = [new[i][metric].get(region) for i in new]
            on = [v for v in o if isinstance(v, (int, float))]
            nn = [v for v in n if isinstance(v, (int, float))]
            print(f"{metric:28} {region[:10]:10} "
                  f"{statistics.median(on) if on else float('nan'):11.4f} "
                  f"{statistics.median(nn) if nn else float('nan'):11.4f} "
                  f"{len(o) - len(on):6} {len(n) - len(nn):6}")

    if write:
        ref["industry_benchmarks"] = new
        ref_path.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n")
        print(f"\nWrote {ref_path}")


if __name__ == "__main__":
    main()
