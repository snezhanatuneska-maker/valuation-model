# Valuation Model: startup valuation

## Live demo

https://snezhanatuneska-maker.github.io/valuation-model/ (demo mode: nothing you enter is stored; the
first calculation can take about 30 seconds while the free server wakes up).

A blended startup valuation tool (Scorecard, Venture Capital, Comparables,
and DCF methods) — originally an Excel workbook, rebuilt into a Python
calculation engine, a FastAPI backend, and a web frontend.

Given a company's industry, country, stage, last-12-month results, financial
projections, and a short qualitative questionnaire, it computes a blended
pre-money and post-money valuation, flags implausible inputs, and generates a
branded PDF report that explains how each number was reached.

The wizard and the PDF report are available in **German and English** (DE / EN switch at the top right;
the choice is kept only in the address, `?lang=de`, nothing is stored in the browser). The API answers in
German when called with `?lang=de`; figures are identical in both languages.

See `AUDIT_REPORT.md` for the October 2026 accuracy audit and what changed.

This version is deliberately consolidated to a handful of files to make
manual upload/download easy — each major piece lives in a single file
rather than being split across many modules.

## Structure

| File | Contents |
|---|---|
| `valuation_engine/__init__.py` | The calculation engine — reference data in `reference_data.json` next to it |
| `app.py` | FastAPI backend — persistence, calculation endpoints, and PDF report endpoints |
| `report.py` | Builds the branded PDF report (reportlab) from a valuation's input/output |
| `index.html` | The web wizard — one file with CSS and JS inlined |
| `refresh_industry_benchmarks.py` | Offline tool (not used by the app) that rebuilds the industry benchmarks in `reference_data.json` from Damodaran's regional spreadsheets |
| `tests/` | Automated tests (run on every push by GitHub Actions) |
| `audit/` | Independent recomputation of every figure (`recompute.py`), benchmark check against Damodaran's files (`check_benchmarks.py`), API smoke test |

## How to run it

`index.html` is just the form — it needs the backend (`app.py`) running
in the background to actually calculate anything. If you open the page
and see *"Could not reach the valuation API"*, that means the steps
below haven't been done yet.

**You'll need [Python](https://www.python.org/downloads/) installed**
(3.10 or newer). Everything else below is typed into a terminal
(Terminal on Mac, PowerShell on Windows).

### Step 1 — Get into the project folder

```bash
cd path/to/valuation-model
```
(Drag the `valuation-model` folder into the terminal window after typing
`cd ` with a trailing space, and it'll fill in the path for you.)

### Step 2 — Install and start the API

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

If `pip` says "command not found," use `pip3` instead. You'll see
output ending in:
```
Uvicorn running on http://127.0.0.1:8000
```

**Leave this terminal window open.** The server only runs while this
window stays open — closing it (or hitting Ctrl+C) stops the API, and
the frontend will go back to showing the "Could not reach" error.

### Step 3 — Open the frontend

In a **new/second** terminal window or Finder/Explorer, open
`index.html` directly in your browser (double-click it). Refresh the
page if it was already open — the "Could not reach the API" error
should be gone and the dropdowns should populate.

### Troubleshooting

| Problem | Fix |
|---|---|
| `pip: command not found` | Use `pip3` instead of `pip` |
| `Could not reach the valuation API at http://localhost:8000` | The API (Step 2) isn't running, or its terminal window was closed. Reopen it and re-run `uvicorn app:app --reload` |
| `uvicorn: command not found` | Step 2's `pip install` didn't finish — re-run `pip install -r requirements.txt` and check for errors above the "command not found" line |
| Port 8000 already in use | Something else is already running on that port. Run `uvicorn app:app --reload --port 8001` instead, then edit near the top of `index.html`'s `<script>` block: change `http://localhost:8000` to `http://localhost:8001` |
| PDF download fails or the logo doesn't show up in it | Make sure `reportlab` and `pillow` installed correctly in Step 2 (re-run `pip install -r requirements.txt` and check for errors) |

### Every time after the first setup

You don't need to repeat Step 2's `pip install` again unless you
re-download the project. Just:
1. Open a terminal, `cd` into the project folder, run `uvicorn app:app --reload`
2. Open `index.html` in your browser

## The PDF report

At the end of the wizard, "Download PDF" builds a full branded report
(about 10 pages: cover, company summary, projections, valuation breakdown,
input checks and scenarios, one page per method used, and methodology and
sources) on the server and
downloads it directly as a real `.pdf` file — no print dialog involved.

Optionally, upload a **company logo** near the top of Step 1 (PNG or
JPEG, under 2 MB) — if provided, it replaces the default mark on the
report's cover page and header.

"Try with a sample company" on the start page fills the whole wizard with an
imaginary, randomized German startup (marked "(sample)") and shows its
results and PDF. A sample is only shown if every method gives a meaningful,
roughly consistent value, so it works as a clean example.

## Demo mode (privacy)

The API stores nothing by default: the wizard only calls the
`/valuations/preview*` routes, which compute and return results without
saving them, and no database file is created. The older routes that save,
list, read and delete valuations answer 404 unless the server is started
with `VALUATION_MODEL_STORE_VALUATIONS=1`. The page loads nothing from other sites
(system fonts, no analytics, no cookies); its privacy note is in
`index.html` (`id="privacy"`), and the FAU / Nuremberg community line is the
one paragraph marked `COMMUNITY LINE`.

## How the valuation works

| Method | What it does | Stage assumption it uses (only here) |
|---|---|---|
| Scorecard (Payne) | Typical pre-revenue pre-money for the region (Equidam H1 2026 median) × your weighted questionnaire score. Idea and Development stages only | — |
| Venture Capital | Exit-year EBITDA × EV/EBITDA multiple, minus debt, discounted at the investor's target return; minus the raise | Target return: 65% (Idea) falling to 20% (Maturity) |
| Comparables | The higher of last-12-month EBITDA × EV/EBITDA and last-12-month revenue × EV/Sales (for SaaS: ARR × the public SaaS EV/ARR multiple, if ARR is entered), less a private-company discount, minus debt plus cash | Private-company discount: 40% → 20% |
| DCF | 5 years of free cash flow + Gordon terminal value at WACC, × probability of survival, minus debt plus cash | Survival: 30% → 95% |

- Projected EBITDA margin starts from the company's own last-12-month margin (before revenue: today's EBITDA, usually the operating loss, divided by Year-1 revenue) and moves in equal steps to the industry EBITDA margin (Damodaran EBITDA/Sales, which includes R&D) by Year 5, unless the user sets a target.
- The terminal value (years after Year 5) is a business growing at the long-run 2% forever: Year 5's profit taxed in full (no lasting loss carryforward), capex at least D&A, and working capital growing at 2%.
- One tax rate for everything: the user's, else the country's statutory rate. Losses are carried forward.
  Germany is the exception (see below).
- Comparables uses the revenue multiple when it gives the higher value, typically for loss-making or thin-margin companies. EV/Sales is Damodaran's EV/EBITDA (all firms) × EBITDA/Sales for the same industry and region, i.e. the sum of enterprise values over the sum of sales. So the method no longer drops out at break-even, and a lower EBITDA never raises the value.
- **Round logic** (cross-check, not part of the blend): the typical round for the stage (Seed for Startup stage, etc.), its usual size and the share of the company it usually sells (dilution). The raise ÷ dilution × (1 − dilution) gives the pre-money that a typical round of your size implies; the result page and PDF compare it with the blended value and show the share your raise would buy at that value.
- A method that doesn't apply to the company (Comparables without revenue, ARR or positive last-12-month EBITDA; VC, Comparables and DCF for banks and insurers) is left out and the other stage weights are scaled up. A method that applies but finds no value (the raise is larger than the exit supports; the cash flows are worth less than nothing) counts as €0 with a note, so a weaker plan never gives a higher value. If no method finds any value for the business, the user gets a plain-language message instead of a €0 valuation.
- Inputs that can't be valued are rejected with a plain-language message (including EBITDA above revenue); implausible ones (revenue jumps, PP&E out of scale, ownership ≠ 100%, use of funds ≠ raise, a value above 50× revenue, …) produce warnings, and the PDF cover says how many inputs need a second look.
- A saved valuation keeps the figures it was saved with (PDF and scenarios); `POST /valuations/{id}/rerun` recalculates with today's data.

## Germany (the default country)

The wizard opens with Germany and Europe selected. Any other country works exactly as before. For German companies:

- **Risk-free rate:** the 10-year Bund yield, not the US Treasury rate. Germany is rated Aaa, so its country risk premium is 0 and only the mature-market equity risk premium (4.2%) applies.
- **Tax, year by year:** corporate tax (15% until 2027, then 14/13/12/11% and 10% from 2032, as enacted in July 2025) + 5.5% solidarity surcharge + trade tax (3.5% × Hebesatz). A projection year that spans two calendar years blends their rates by days. The value after Year 5 (terminal value) and the WACC use the long-run 2032 rate. The Hebesatz is the national average unless the user enters their municipality's under "Advanced". If a user enters a flat tax rate through the API, it still overrides the schedule.
- **Scorecard benchmark:** Equidam's Scorecard average for Germany, €6,550,000 (Parameters Update P6.3, 30 July 2026): the average pre-money valuation of angel, pre-seed and seed rounds in Germany over the last 30 months. Equidam publishes one figure per country, so the Idea and Development stages use the same figure. Equidam updates it twice a year.

All of these figures live in `valuation_engine/reference_data.json` under `country_specific` → `Germany`, each with its source and date. Update them once a year there; no code changes are needed.

German example (`GERMAN_CASE` in `audit/recompute.py`): Beispiel Software GmbH, Software (System & Application), Europe, Startup stage, otherwise the same inputs as the example below, with no tax override. Valuation date 6 October 2026, Bund 3.50%, Hebesatz 409%.

| Method | Value |
|---|---|
| Venture Capital | 971,446 € |
| Comparables | 1,134,461 € (revenue multiple) |
| DCF | 1,327,405 € |
| **Blended pre-money** | **1,153,087 €** |
| **Post-money** | **1,453,087 €** |

## Verified numbers (Valuativa DOO example)

Valuation date 6 October 2026. Tanzania, Software (Entertainment), Emerging
Markets, Startup stage. Last 12 months: revenue €300,000, EBITDA €50,000,
cash €20,000, no debt. Year-1 revenue €1,000,000 growing 10% a year, capex
€30,000 in Years 2–5, capital raised €300,000, tax rate 10%. Full inputs:
`REFERENCE_CASE` in `audit/recompute.py`.

| Method | Value |
|---|---|
| Scorecard | not used (company has revenue) |
| Venture Capital | 1,242,335 € |
| Comparables | 1,258,079 € (revenue multiple; the EBITDA multiple gives 697,925 €) |
| DCF | 828,766 € |
| **Blended pre-money** | **1,103,096 €** |
| **Post-money** | **1,403,096 €** |

Round logic: a typical Seed round sells 15–25% of the company (median 19.5%), so a €300,000 raise implies a
pre-money of €900,000 to €1,700,000; at the blended value it would buy 21.4%.

These are pinned in `tests/test_engine.py` and recomputed independently by
`audit/recompute.py`.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python audit/recompute.py      # prints every figure next to its independent recomputation
```

## Benchmark data

Country data and industry benchmarks come from Prof. Aswath Damodaran's
January 2026 datasets (NYU Stern). To refresh the industry benchmarks,
download his `vebitda`, `wacc`, `margin`, `wcdata` and `dbtfund`
spreadsheets for US, Europe, Japan, emerging markets, China, India and
Global into one folder, then run:

```bash
pip install xlrd openpyxl
python refresh_industry_benchmarks.py path/to/folder valuation_engine/reference_data.json          # report only
python refresh_industry_benchmarks.py path/to/folder valuation_engine/reference_data.json --write  # apply
```

Then check the result against the same files and run the tests:

```bash
python audit/check_benchmarks.py path/to/folder
DAMODARAN_DIR=path/to/folder python -m pytest -q
```

Rules the refresh applies:
- A regional figure based on fewer than 10 companies is not used; the
  engine falls back to that industry's global figure.
- D&A (% of revenue) and the interest rate on debt aren't published
  directly, so they are derived from Damodaran's margin and debt ratios;
  implausible results fall back the same way.
- EV/Sales is derived as EV/EBITDA (all firms) × EBITDA/Sales; outside
  0.05×–40× it falls back the same way.
- The SaaS EV/ARR multiple and the round benchmarks (dilution and round
  size by stage) are not from Damodaran; they sit in `saas_arr_multiple`
  and `round_benchmarks` in `reference_data.json` with their sources and
  dates, and are updated by hand.
- "Retail (Online)" is no longer published by Damodaran; it uses his "Retail (General)" figures.
- Country risk data (`country_data`) has its own tool: download `ctryprem.xlsx` from Damodaran's
  [country risk page](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html), then run
  `python refresh_country_data.py ctryprem.xlsx valuation_engine/reference_data.json` (add `--write` to apply).
- The Scorecard's regional benchmark is Equidam's H1 2026 median (see `sources.scorecard_benchmark`). Germany uses its own figure (`country_specific.Germany.stage_benchmarks`, Equidam's Scorecard average for Germany); a figure marked `to_be_sourced: true` is shown as a placeholder in the results and PDF. Users can always enter their own benchmark in the wizard.

## License

Add a `LICENSE` file before making this repository public if you intend
others to use or modify it.
