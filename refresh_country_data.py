"""
Refresh country_data in valuation_engine/reference_data.json from Damodaran's
country risk premium spreadsheet (ctryprem.xlsx, "ERPs by country" sheet):
https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html

Offline maintenance tool - not used by the app at runtime.
Needs: pip install openpyxl xlrd

Usage:
    python refresh_country_data.py ctryprem.xlsx valuation_engine/reference_data.json [--write]

Without --write it only prints a report: the mature-market premium the file
implies, every value that would change, and countries that are new or
missing. Columns are found by header text, never by position, and the
script stops (listing the headers it saw) if a required one is missing.

Rules:
- Countries in the file but not in the app are added (and offered in the
  wizard). Countries in the app but not in the file are kept unchanged and
  listed, so saved valuations that use them keep working.
- Name differences between the file and the app (e.g. "North Macedonia")
  are handled by NAME_ALIASES below.
"""
import json
import re
import sys
from pathlib import Path

# file name -> app name, where Damodaran's spelling differs from the app's.
NAME_ALIASES = {
    "North Macedonia": "Macedonia",
    "Côte d'Ivoire": "Côte d'Ivoire",
    "Cote d'Ivoire": "Côte d'Ivoire",
    "Ivory Coast": "Côte d'Ivoire",
    "Eswatini": "Swaziland",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "Korea, Republic of": "Korea",
    "South Korea": "Korea",
    "Czechia": "Czech Republic",
}

# field in the app -> (header pattern, required?)
COLUMNS = {
    "country": (r"^country", True),
    "region_grouping": (r"^region", False),
    "moodys_rating": (r"moody", True),
    "adjusted_default_spread": (r"default spread", True),
    "equity_risk_premium": (r"equity risk premium", True),
    "country_risk_premium": (r"country risk premium", True),
    "corporate_tax_rate": (r"corporate tax", False),
    "sovereign_cds": (r"sovereign cds", False),
}
NUMERIC = {"adjusted_default_spread", "equity_risk_premium", "country_risk_premium",
           "corporate_tax_rate", "sovereign_cds"}


def norm(s):
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip().lower()


def sheet_rows(path: Path):
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in book.worksheets}
    else:
        import xlrd
        book = xlrd.open_workbook(str(path))
        sheets = {s.name: [s.row_values(i) for i in range(s.nrows)] for s in book.sheets()}
    # Prefer the "ERPs by country" sheet; otherwise the first sheet with the needed headers.
    ordered = sorted(sheets.items(), key=lambda kv: 0 if "erps by country" in kv[0].lower() else 1)
    for name, rows in ordered:
        for r, row in enumerate(rows[:40]):
            headers = [norm(c) for c in row]
            if any(h.startswith("country") for h in headers) and any("equity risk premium" in h for h in headers):
                return name, headers, rows[r + 1:]
    raise SystemExit(f"{path.name}: no sheet with 'Country' and 'Equity Risk Premium' headers. "
                     f"Sheets: {list(sheets)}")


def find_columns(headers):
    cols = {}
    for field, (pattern, required) in COLUMNS.items():
        hits = [i for i, h in enumerate(headers) if re.search(pattern, h)]
        if not hits:
            if required:
                raise SystemExit(f"No column matching {pattern!r} for {field}. Headers: {headers}")
            continue
        cols[field] = hits[0]  # first occurrence = the rating-based figures
    return cols


def as_number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
        return float(v)
    if isinstance(v, str):
        t = v.strip().replace("%", "")
        try:
            x = float(t)
            return x / 100 if "%" in v else x
        except ValueError:
            return None
    return None


def read_countries(path: Path) -> dict:
    sheet, headers, body = sheet_rows(path)
    cols = find_columns(headers)
    out = {}
    for row in body:
        name = str(row[cols["country"]] or "").strip()
        erp = as_number(row[cols["equity_risk_premium"]]) if cols["equity_risk_premium"] < len(row) else None
        if not name or erp is None:
            continue  # blank lines, region subtotals, notes
        rec = {}
        for field, i in cols.items():
            if field == "country" or i >= len(row):
                continue
            v = row[i]
            if field in NUMERIC:
                n = as_number(v)
                rec[field] = n if n is not None else "NA"
            else:
                rec[field] = str(v).strip() if v not in (None, "") else "NA"
        out[NAME_ALIASES.get(name, name)] = rec
    return out


def main():
    src, ref_path = Path(sys.argv[1]), Path(sys.argv[2])
    write = "--write" in sys.argv
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    current = ref["country_data"]
    new = read_countries(src)

    aaa = [c["equity_risk_premium"] for c in new.values()
           if str(c.get("moodys_rating")).strip().lower() == "aaa" and isinstance(c["equity_risk_premium"], float)]
    if aaa:
        print(f"Mature-market premium implied by Aaa countries: {min(aaa):.4f}")
    print(f"{len(new)} countries in {src.name}; {len(current)} in the app\n")

    changes = 0
    for country, rec in sorted(new.items()):
        old = current.get(country)
        if old is None:
            print(f"NEW      {country}: {rec}")
            continue
        for field, value in rec.items():
            before = old.get(field)
            same = (isinstance(value, float) and isinstance(before, (int, float))
                    and abs(value - before) < 5e-5) or value == before
            if not same:
                changes += 1
                print(f"CHANGE   {country:32s} {field:26s} {before!r:>12} -> {value!r}")
    missing = sorted(set(current) - set(new))
    for country in missing:
        print(f"MISSING  {country} (not in the file; kept unchanged)")
    print(f"\n{changes} values differ, {len(set(new) - set(current))} new countries, {len(missing)} missing")

    if write:
        for country, rec in new.items():
            merged = dict(current.get(country, {}))
            merged.update({k: v for k, v in rec.items() if not (v == "NA" and k in ("corporate_tax_rate",))})
            merged.setdefault("region_grouping", "NA")
            current[country] = merged
        ref["country_data"] = dict(sorted(current.items()))
        ref["categorical_options"]["country"] = sorted(current)
        ref_path.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {ref_path}")


if __name__ == "__main__":
    main()
