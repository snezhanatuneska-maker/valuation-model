"""
Refresh country_data in valuation_engine/reference_data.json from Damodaran's
country risk premium workbook (ctryprem.xlsx):
https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html

Offline maintenance tool - not used by the app at runtime.
Needs: pip install openpyxl

Usage:
    python refresh_country_data.py ctryprem.xlsx valuation_engine/reference_data.json [--write]

Without --write it only prints a report: the update date and mature-market
premium in the file, every value that would change, and countries that are
new or missing. Columns are found by header text, never by position, and the
script stops (listing the headers it saw) if a required one is missing.

What it reads:
- "Regional breakdown" sheet: every country with a sovereign rating, with its
  rating, default spread, country and equity risk premiums, corporate tax
  rate and region. This is the main source.
- "ERPs by country" sheet: the update date, the mature-market premium, and the
  "Frontier Markets (no sovereign rating)" table, whose premiums come from the
  PRS composite risk score. That table has no tax rates, so frontier
  countries are only updated if the app already lists them (keeping their
  tax rate); others are reported, not added.

Rules:
- Rated countries in the file but not in the app are added (and offered in
  the wizard). Countries in the app but not in the file are kept unchanged
  and listed, so saved valuations that use them keep working.
- Name differences between the file and the app are handled by NAME_ALIASES.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import openpyxl

# file name -> app name, where Damodaran's spelling differs from the app's.
NAME_ALIASES = {
    "Macedonia": "North Macedonia",
    "Cote d'Ivoire": "Côte d'Ivoire",
    "Ivory Coast": "Côte d'Ivoire",
    "Swaziland": "Eswatini",
    "Turkey": "Türkiye",
    "Turkiye": "Türkiye",
    "Korea": "South Korea",
    "Korea, Republic of": "South Korea",
    "Czechia": "Czech Republic",
    "Abu Dhabi": "Abu Dhabi (UAE)",
    "Sharjah": "Sharjah (UAE)",
    "Ras Al Khaimah (Emirate of)": "Ras Al Khaimah (UAE)",
    "Andorra (Principality of)": "Andorra",
    "Guernsey (States of)": "Guernsey",
    "Jersey (States of)": "Jersey",
    "Congo (Democratic Republic of)": "Congo (Democratic Republic)",
    "Congo (Republic of)": "Congo (Republic)",
}


def name_sort_key(name: str) -> str:
    """Alphabetical order ignoring accents (same as the app's country list)."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c)).casefold()

# field in the app -> (header pattern, required?), for the rated-countries table
RATED_COLUMNS = {
    "country": (r"^country$", True),
    "gdp_millions": (r"^gdp", False),
    "moodys_rating": (r"moody", True),
    "sovereign_cds": (r"sovereign cds", False),
    "adjusted_default_spread": (r"default spread", True),
    "equity_risk_premium": (r"equity risk premium", True),
    "country_risk_premium": (r"country risk premium", True),
    "corporate_tax_rate": (r"corporate tax", True),
    "region_grouping": (r"^region$", True),
}
FRONTIER_COLUMNS = {
    "country": (r"^country$", True),
    "prs_score": (r"prs", True),
    "equity_risk_premium": (r"^erp$|equity risk premium", True),
    "country_risk_premium": (r"^crp$|country risk premium", True),
    "adjusted_default_spread": (r"default spread", True),
}
TEXT_FIELDS = {"country", "moodys_rating", "region_grouping"}
COMPARE_TOLERANCE = 5e-5


def norm(s):
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip().lower()


def as_number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
        return float(v)
    return None


def find_columns(headers, spec, where):
    cols = {}
    for field, (pattern, required) in spec.items():
        hits = [i for i, h in enumerate(headers) if re.search(pattern, h)]
        if hits:
            cols[field] = hits[0]
        elif required:
            raise SystemExit(f"{where}: no column matching {pattern!r} for {field}. Headers: {headers}")
    return cols


def read_table(rows, start, spec, where):
    """Reads rows after the header at `start` until the first row without a country name."""
    cols = find_columns([norm(c) for c in rows[start]], spec, where)
    out = {}
    for row in rows[start + 1:]:
        name = str(row[cols["country"]] or "").strip() if row else ""
        if not name or name.lower() == "none":
            break
        rec = {}
        for field, i in cols.items():
            if field == "country":
                continue
            v = row[i] if i < len(row) else None
            if field in TEXT_FIELDS:
                rec[field] = str(v).strip() if v not in (None, "") else "NA"
            else:
                n = as_number(v)
                rec[field] = n if n is not None else "NA"
        out[NAME_ALIASES.get(name, name)] = rec
    return out


def read_workbook(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    names = {ws.title.lower(): ws.title for ws in wb.worksheets}
    if "regional breakdown" not in names or "erps by country" not in names:
        raise SystemExit(f"{path.name}: expected sheets 'Regional breakdown' and 'ERPs by country'; "
                         f"found {list(names.values())}")

    rb = [list(r) for r in wb[names["regional breakdown"]].iter_rows(values_only=True)]
    header = next((i for i, r in enumerate(rb[:20]) if norm(r[0]) == "country"), None)
    if header is None:
        raise SystemExit("'Regional breakdown': no 'Country' header row")
    rated = read_table(rb, header, RATED_COLUMNS, "Regional breakdown")

    erp = [list(r) for r in wb[names["erps by country"]].iter_rows(values_only=True)]
    update_date, mature_premium = None, None
    for r in erp[:12]:
        label = norm(r[0])
        values = [c for c in r[1:] if c is not None]
        if label.startswith("date of update") and values:
            update_date = values[0].date() if isinstance(values[0], dt.datetime) else values[0]
        numbers = [as_number(c) for c in values if as_number(c) is not None]
        if "mature equity market" in label and numbers:
            mature_premium = numbers[0]
    if mature_premium is None:  # fall back to the premium of Aaa-rated countries (no country premium)
        aaa = [c["equity_risk_premium"] for c in rated.values()
               if c.get("moodys_rating") == "Aaa" and isinstance(c["equity_risk_premium"], float)]
        mature_premium = min(aaa) if aaa else None
    frontier = {}
    header = next((i for i, r in enumerate(erp) if r and norm(r[0]) == "country"
                   and any("prs" in norm(c) for c in r)), None)
    if header is not None:
        frontier = read_table(erp, header, FRONTIER_COLUMNS, "ERPs by country (frontier markets)")
    return {"rated": rated, "frontier": frontier, "update_date": update_date, "mature_premium": mature_premium}


def _same(a, b):
    if isinstance(a, float) and isinstance(b, (int, float)):
        return abs(a - b) < COMPARE_TOLERANCE
    return a == b


def plan_changes(current: dict, data: dict) -> dict:
    """Returns {country: new record} for every country to write, plus a report."""
    new_records, report = {}, []
    for country, rec in sorted(data["rated"].items()):
        old = current.get(country)
        merged = dict(old or {})
        merged.pop("gdp_millions_2022", None)  # replaced by gdp_millions (year stated in sources)
        merged.update(rec)
        merged.pop("prs_score", None)
        new_records[country] = merged
        if old is None:
            report.append(f"NEW      {country}: {rec}")
        else:
            for field, value in rec.items():
                if field == "gdp_millions":
                    continue
                if not _same(value, old.get(field)):
                    report.append(f"CHANGE   {country:32s} {field:26s} {old.get(field)!r:>14} -> {value!r}")

    for country, rec in sorted(data["frontier"].items()):
        if country in data["rated"]:
            continue
        old = current.get(country)
        if old is None:
            report.append(f"FRONTIER {country}: not added (unrated; the file has no tax rate)")
            continue
        merged = dict(old)
        merged.pop("gdp_millions_2022", None)
        merged.update({k: rec[k] for k in ("equity_risk_premium", "country_risk_premium", "adjusted_default_spread")})
        merged["moodys_rating"] = "NR"
        merged["prs_score"] = rec["prs_score"]
        new_records[country] = merged
        for field in ("equity_risk_premium", "country_risk_premium", "adjusted_default_spread"):
            if not _same(rec[field], old.get(field)):
                report.append(f"CHANGE   {country:32s} {field:26s} {old.get(field)!r:>14} -> {rec[field]!r} (frontier)")

    for country in sorted(set(current) - set(new_records)):
        report.append(f"MISSING  {country} (not in the file; kept unchanged)")
    return {"records": new_records, "report": report}


def main():
    src, ref_path = Path(sys.argv[1]), Path(sys.argv[2])
    write = "--write" in sys.argv
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    current = ref["country_data"]
    data = read_workbook(src)
    plan = plan_changes(current, data)

    print(f"File: {src.name}; update date {data['update_date']}; mature-market premium {data['mature_premium']}")
    print(f"{len(data['rated'])} rated countries, {len(data['frontier'])} frontier (unrated); "
          f"{len(current)} countries in the app\n")
    for line in plan["report"]:
        print(line)
    changes = sum(line.startswith("CHANGE") for line in plan["report"])
    new = sum(line.startswith("NEW") for line in plan["report"])
    missing = sum(line.startswith("MISSING") for line in plan["report"])
    print(f"\n{changes} values differ, {new} new countries, {missing} missing")

    if write:
        merged = dict(current)
        merged.update(plan["records"])
        ref["country_data"] = dict(sorted(merged.items(), key=lambda kv: name_sort_key(kv[0])))
        ref["categorical_options"]["country"] = sorted(merged, key=name_sort_key)
        mp = ref.setdefault("market_parameters", {})
        if data["mature_premium"] is not None:
            mp["mature_market_premium"] = data["mature_premium"]
        if data["update_date"] is not None:
            ref.setdefault("sources", {})["country_data_as_of"] = str(data["update_date"])
        ref_path.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {ref_path}")


if __name__ == "__main__":
    main()
