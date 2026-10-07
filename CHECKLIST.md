# Check-and-fix loop: checklist

Last run: 7 October 2026, branch `claude/great-goldberg-0zs2fz`. Full test suite: **211 passed, 3 runs in a row**
(`python -m pytest -q`; the browser tests need `pip install playwright` and Chromium and are skipped otherwise).

Baseline = the code before this loop (123 tests, all passing). Final = after the fixes below.

| Check | Baseline | Final | Notes |
|---|---|---|---|
| A1 Golden case (Werkpuls) | PASS* | PASS* | Every figure the app shares with the hand calculation matches to the cent (revenue, EBIT, working capital, DCF present values, terminal value discounted, VC exit and share maths, Scorecard sum, blend). *Five convention differences are deliberate app choices: see QUESTIONS.md 1–5. `tests/test_golden.py` |
| A2 One blended number, weights add up | PASS | PASS | No simple average anywhere; blend = Σ value × weight; post = pre + raise |
| A3 Sweep industry × region × stage | PASS | PASS | 3,696 runs: no crash, NaN, "NA" or zero value; every fallback listed. WARNING: REITs / Japan is 10× its stage median (Expansion, Growth, Maturity) |
| A4 Edge inputs | FAIL | PASS | Was: "NaN"/"Infinity" accepted, and amounts like 10^200, then the PDF crashed (500). Fixed (iterations 2, 3) |
| A5 Risk multiplier / hurdle labels | FAIL | PASS | The code uses no 0.55 anywhere; labels match the code. Was: API description still said "DCF Multiples" (iteration 4). See QUESTIONS.md 4 |
| A6 Cash and debt bridge | PASS | PASS | Comparables and DCF: − debt + cash. VC: − debt at exit |
| A7 Valuation date | PASS | PASS | From the input, else today; nothing hard-coded |
| B1 Every endpoint in /openapi.json | PASS | PASS | All 23 operations incl. save / get / rerun / scenarios / report / delete (with saving switched on) |
| B2 Same numbers on every route | PASS | PASS | preview = saved = rerun = scenarios at 100% = both PDFs |
| B3 Bad input → readable 422, private saves | FAIL | PASS | Was: NaN/Infinity gave 500 from the PDF route. Saves are private per browser ID (no email login any more: QUESTIONS.md 9) |
| C1 Walk the wizard, PDF download, no console errors | FAIL | PASS | Was: a console error on every visit (missing site icon, iteration 1). Desktop 1280 px and phone 390 px |
| C2 Numbers not truncated, € and separators | PASS | PASS | No number box cut off; no horizontal scroll on the phone |
| C3 Dropdowns from API, projection preview = API | PASS | PASS | |
| C4 Validation messages | PASS | PASS | Ownership 80% (note, doesn't block), use of funds ≠ raise, Year-1 revenue 3.75× last 12 months, empty company name. Werkpuls shows none of these (it does show a capex note: QUESTIONS.md 8) |
| C5 Scenario slider | PASS | PASS | Every step matches /scenarios |
| C6 Back/forward, demo | FAIL | PASS | Back/forward keep data; sample company works (was: 1 draw in 20 had a negative use-of-funds amount, iteration 7). Was: "Start" could move the cursor out of the field being typed in (iteration 5). Demo login and paywall were removed in the October audit: QUESTIONS.md 9 |
| D1 PDF numbers = API | PASS | PASS | All method values, weights, projections, DCF and VC figures |
| D2 PDF formatting | PASS | PASS | "Existing shares (count) 25,000", years as 2027, tax as 30.0% |
| D3 Spelling | PASS | PASS | None of the listed misspellings appear on screen or in the PDF |
| D4 Charts, pages, overflow | FAIL | PASS | Charts render, no empty page, nothing outside the margins. Was 13 pages; now 10 for companies with revenue, 11 before revenue (the Scorecard page is real content there). Shortened with your OK (iteration 6) |
| E Live deployment | NOT RUN | NOT RUN | This environment's network policy blocks valuation-model-a1iu.onrender.com and snezhanatuneska-maker.github.io, and nothing is pushed to `main` yet. See "Live deployment" below |

## Iterations

1. Console error on every visit (missing site icon) → empty built-in icon.
2. "NaN"/"Infinity" accepted as numbers → rejected with a 422 naming the field.
3. Amounts like 10^200 crashed the PDF → €10 trillion limit with a readable message.
4. API description named a method "DCF Multiples" → "Comparables (EV/EBITDA multiple)".
5. "Start" moved the cursor out of a field already being typed in → only when no field has the cursor.
6. PDF was 13 pages → about 10 (unused Scorecard as one line, checks and scenarios share a page, use of funds
   and ownership back on the projections page).
7. The one-click sample sometimes had a negative "Operations" amount (rejected by the API) → split fixed.

## Live deployment (check E)

Not done: this session can't reach Render or GitHub Pages, so it can't confirm a deploy. To run it, merge this
branch into `main` (Render and GitHub Pages redeploy from `main`), then rerun the browser tests against the live
site, or ask me in a session whose network settings allow those two hosts.
