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

| Method | Value |
|---|---|
| Scorecard | 1,699,500.00 € |
| Venture Capital | 965,486.96 € |
| DCF Multiples | 2,142,052.40 € |
| DCF | 270,529.88 € |

(Exact blended figures depend on the qualitative questionnaire answers
entered, since the Scorecard method — and therefore the blend — scores
those answers against a benchmark company.)

## License

Add a `LICENSE` file before making this repository public if you intend
others to use or modify it.
