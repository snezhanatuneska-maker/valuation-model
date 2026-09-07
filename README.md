# Startup Valuation Model

A blended startup valuation tool (Scorecard, Venture Capital, DCF Multiples,
and DCF methods) — originally an Excel workbook, rebuilt into a tested
Python calculation engine, a FastAPI backend, and a web frontend.

Given a company's industry, country, stage, financial projections, and a
short qualitative questionnaire, it computes a blended pre-money and
post-money valuation — with three formula bugs and one data-entry error
found and fixed along the way (see `docs/error_review_and_fixes.pdf`).

This version is deliberately consolidated to as few files as possible
(11 total) to make manual upload easy — each major piece (the engine, the
API, the frontend) lives in a single file rather than being split across
many modules.

## Structure

| Directory | Contents |
|---|---|
| `docs/` | Original + corrected Excel workbooks, and the bug write-up PDF |
| `valuation_engine/` | The calculation engine — all code in one `__init__.py`, reference data in one `reference_data.json` |
| `valuation_api/` | FastAPI backend — all code in one `app.py` |
| `frontend/` | The web wizard — one `index.html` with CSS and JS inlined |

## How to run it

The frontend (`frontend/index.html`) is just the form — it needs the
backend (the API) running in the background to actually calculate
anything. If you open the page and see *"Could not reach the valuation
API"*, that means the steps below haven't been done yet.

**You'll need [Python](https://www.python.org/downloads/) installed**
(3.10 or newer). Everything else below is typed into a terminal
(Terminal on Mac, PowerShell on Windows).

### Step 1 — Get into the project folder

```bash
cd path/to/valuation-model
```
(Drag the `valuation-model` folder into the terminal window after typing
`cd ` with a trailing space, and it'll fill in the path for you.)

### Step 2 — Install the engine

```bash
cd valuation_engine
pip install -e .
cd ..
```

If `pip` says "command not found," use `pip3` instead everywhere below.

### Step 3 — Install and start the API

```bash
cd valuation_api
pip install -r requirements.txt
uvicorn app:app --reload
```

You'll see output ending in:
```
Uvicorn running on http://127.0.0.1:8000
```

**Leave this terminal window open.** The server only runs while this
window stays open — closing it (or hitting Ctrl+C) stops the API, and
the frontend will go back to showing the "Could not reach" error.

### Step 4 — Open the frontend

In a **new/second** terminal window or Finder/Explorer, open
`frontend/index.html` directly in your browser (double-click it, or
`open frontend/index.html` on Mac). Refresh the page if it was already
open — the "Could not reach the API" error should be gone and the
dropdowns should populate.

### Troubleshooting

| Problem | Fix |
|---|---|
| `pip: command not found` | Use `pip3` instead of `pip` |
| `Could not reach the valuation API at http://localhost:8000` | The API (Step 3) isn't running, or its terminal window was closed. Reopen it and re-run `uvicorn app:app --reload` from inside `valuation_api/` |
| `uvicorn: command not found` | Step 3's `pip install` didn't finish — re-run `pip install -r requirements.txt` and check for errors above the "command not found" line |
| Port 8000 already in use | Something else is already running on that port. Run `uvicorn app:app --reload --port 8001` instead, then edit near the top of `frontend/index.html`'s `<script>` block: change `http://localhost:8000` to `http://localhost:8001` |

### Every time after the first setup

You don't need to repeat Steps 2/3's `pip install` again unless you
re-download the project. Just:
1. Open a terminal, `cd` into `valuation_api`, run `uvicorn app:app --reload`
2. Open `frontend/index.html` in your browser

## Running the tests

```bash
cd valuation_engine && python3 -m pytest tests/ -v
cd ../valuation_api  && python3 -m pytest tests/ -v
```

Both suites include a regression test that rebuilds the original
Valuativa DOO case end-to-end and checks every number against the
corrected workbook.

## Verified numbers (Valuativa DOO example)

| Method | Value |
|---|---|
| Scorecard | 1,023,000 € |
| Venture Capital | 965,486.96 € |
| DCF Multiples | 2,142,052.40 € |
| DCF | 270,529.88 € |
| **Blended pre-money** | **1,118,596.42 €** |
| **Post-money** | **1,418,596.42 €** |

## License

Add a `LICENSE` file before making this repository public if you intend
others to use or modify it.
