# Damodaran source files

Original spreadsheets from Aswath Damodaran's data page (NYU Stern,
https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html) that
`valuation_engine/reference_data.json` is built from. The tests check the app's
data against these files on every push.

| Files | Contents | Date | Loaded with |
|---|---|---|---|
| `vebitda*.xls`, `wacc*.xls`, `margin*.xls`, `wcdata*.xls`, `dbtfund*.xls` | Industry averages by region (US = no suffix, Europe, Japan, emerg, China, India, Global) | 5 January 2026 | `refresh_industry_benchmarks.py` |
| `ctrypremJuly26.xlsx` | Country risk premiums, ratings, tax rates | 1 July 2026 (spreads corrected 9 July 2026) | `refresh_country_data.py` |

To update: replace the files with Damodaran's newer versions, run both
refresh scripts with `--write`, and run `python -m pytest -q`.
