# Startup Valuation Model

A blended startup valuation tool (Scorecard, Venture Capital, DCF Multiples,
and DCF methods) — originally an Excel workbook, rebuilt into a Python
calculation engine, a FastAPI backend, and a web frontend.

Given a company's industry, country, stage, financial projections, and a
short qualitative questionnaire, it computes a blended pre-money and
post-money valuation, and can generate a polished, branded PDF report of
the result — with three formula bugs and one data-entry error found and
fixed along the way versus the original workbook.

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
(cover page, company summary, projections, valuation breakdown, one
page per method, and a methodology/disclaimer page) on the server and
downloads it directly as a real `.pdf` file — no print dialog involved.

Optionally, upload a **company logo** near the top of Step 1 (PNG or
JPEG, under 2 MB) — if provided, it replaces the default mark on the
report's cover page and header.

Saved valuations (History tab) can also have their PDF re-downloaded
at any time, even after closing and reopening the app, since the
report is rebuilt fresh from the saved inputs each time.

## Verified numbers (Valuativa DOO example)

Tanzania, Software (Entertainment), Emerging Markets, Startup stage,
Year-1 revenue 1,000,000 € growing 10% a year, capex 30,000 € in Years
2–5, capital needed 300,000 €, DCF tax rate 10%.

| Method | Value |
|---|---|
| Scorecard | 1,023,000.00 € |
| Venture Capital | 1,422,481.00 € |
| DCF Multiples | 2,915,592.70 € |
| DCF | 367,591.19 € |
| **Blended pre-money** | **1,494,025.42 €** |
| **Post-money** | **1,794,025.42 €** |

The Scorecard (and therefore the blend) depends on the questionnaire
answers; 1,023,000 € is for answers that score 93% of the benchmark
company. The other three reflect Damodaran's January 2026 data (country
risk and industry benchmarks) and the WACC fix.

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

Rules the refresh applies:
- A regional figure based on fewer than 10 companies is not used; the
  engine falls back to that industry's global figure.
- D&A (% of revenue) and the interest rate on debt aren't published
  directly, so they are derived from Damodaran's margin and debt ratios;
  implausible results fall back the same way.
- "Retail (Online)" is not in Damodaran's files and keeps its earlier values.

## License

Add a `LICENSE` file before making this repository public if you intend
others to use or modify it.
