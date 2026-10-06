# Valuativa: accuracy audit and improvement review

**Audit date:** 6 October 2026
**Code audited:** branch `claude/relaxed-maxwell-qytry6` at commit `3b2642b` (it includes the January 2026 Damodaran data refresh). No app code was changed.
**Live site:** this audit environment's network policy blocked both https://snezhanatuneska-maker.github.io/valuation-model/ and the Render API, so I reviewed the code that is deployed and a PDF generated from it (`audit/reference_case_report.pdf`). See finding N3: the live site may still be running older code.

## Fix status (follow-up change, 6 October 2026)

Everything below the line is the original audit, kept as written. This table records what the follow-up change did about each finding.

| # | Finding | Status | What changed |
|---|---|---|---|
| 1 | Scorecard table didn't add up | **Fixed** | No hidden multiplier any more, so the rows add up to the total. A test checks this. |
| 2 | 0.55 used four ways; hurdle rose with maturity | **Fixed** | Three separate stage assumptions, each used by one method only: VC target return 65% (Idea) → 20% (Maturity), private-company discount 40% → 20% (Comparables), survival probability 30% → 95% (DCF). Scorecard has no haircut (Payne). |
| 3 | DCF double-counted risk (WACC + 55%) | **Fixed** | Discounts at WACC only, then weights the result by the stage's survival probability (anchored on BLS data: ~78% of new businesses survive one year, ~49% survive five). |
| 4 | DCF PVs / terminal value disclosure | **Fixed** | The PDF shows PV of cash flows, PV of terminal value, terminal value % of EV, and every input to the discount rate. |
| 5 | No valuation date | **Fixed** | New valuation-date field (default today, pinned when saved). Years are labelled "Y1 (Oct 2027)" etc. |
| 6 | Enterprise value reported as equity | **Fixed** | Comparables and DCF subtract debt and add cash. The VC method subtracts debt from the exit value. Equity is floored at zero, never negative. |
| 7 | Comparables on forward EBITDA; inconsistent names | **Fixed** | Uses last-12-month EBITDA with Damodaran's trailing multiple. Called "Comparables (EV/EBITDA multiple)" everywhere. "Exit value" label replaced. |
| 8 | Two totals | **Fixed** | `simple_average_valuation` removed. |
| 9 | Price per share "€1" | **Fixed** | Shown to the cent (€1.60). |
| 10 | Rounding, hidden assumptions, fallbacks | **Fixed** | Rates shown with 2 decimals. The hidden "other opex 1.5%" line is gone. Every fallback is footnoted in the PDF and listed as a note. |
| 11 | Capex mismatch; Y1 capex missing from PDF | **Fixed** | Y1 is shown. Use-of-funds capex ≠ Y1 capex raises a warning. |
| N1 | Margins left out R&D | **Fixed** | Target margin = Damodaran EBITDA/Sales (new `ebitda_margin` column; `rd_pct_revenue` also stored). |
| N2 | Company's own numbers ignored | **Fixed** | Margin starts from the last-12-month margin and reaches the industry margin (or the user's target) by Year 5. Last-12-month EBITDA drives Comparables. Cash and debt feed the equity bridge. Year-1 working-capital change starts from last-12-month revenue. |
| N3 | Live site on older code | **Open (needs you)** | All changes are on branch `claude/relaxed-maxwell-qytry6`. The site updates only after it is merged to `main` and Render redeploys. |
| N4 | Inputs that crashed the server | **Fixed** | Validated with a plain-language 422, e.g. "revenue year1: Input should be greater than 0". |
| N5 | Negative or impossible values blended | **Fixed** | Methods that aren't meaningful are left out and the weights rescaled. If nothing applies, a clear error is shown. |
| N6 | Two tax rates; negative tax | **Fixed** | One rate (user's, else the country's statutory rate). Losses carried forward. |
| N7 | Derived cost of debt | **Fixed** | Uses Damodaran's published cost of debt. |
| N8 | Y1 working-capital change always 0 | **Fixed** | See N2. |
| N9 | Terminal-value floor silent; banks valued on EBITDA | **Fixed** | Warning when the floor applies. Banks and insurers use Scorecard only, with an explanation. |
| N10 | DCF page described the wrong method | **Fixed** | Rewritten. Every method page starts with "How this number was reached". |
| N11 | Option typos and gaps | **Fixed** | Spelling fixed with aliases (old saved answers still work). Added "$50 to $100 million" (market) and "$50 to $100 Million" (revenue potential). |
| N12 | Two report builders | **Fixed** | The unused browser report (~350 lines) removed; the server PDF is the only one. |
| N13 | Missing `docs/` references | **Fixed** | References removed. |
| Data | Stage pre-money benchmark has no source | **Partly fixed** | Labelled "internal estimate" in the app and PDF. Users can enter their own benchmark. **A cited table is still needed from you.** |
| Data | Country risk data not verified | **Checked; update pending** | All 157 countries follow Damodaran's January 2026 method exactly (premium = 4.23% + 1.52 × default spread; a test enforces this). Tanzania, Brazil, China and India match his published figures. Damodaran's July 2026 update (4.20%, ~180 countries) can be loaded with `refresh_country_data.py` once `ctryprem.xlsx` is available. |
| Data | Retail (Online) had stale values (zeros for India) | **Fixed** | Uses Damodaran's current Retail (General) figures. |
| Validation | Section 4 rules | **Fixed** | Engine warnings for every rule. The wizard blocks ownership ≠ 100% and use of funds ≠ raise. Region is preset from the country. Tax defaults to the country's rate. |
| Tests | No tests | **Fixed** | `tests/` has 72 tests; GitHub Actions runs them on every push. All 819 checked benchmark values match Damodaran's files. |
| Business | Demo login/payment; SQLite on Render | **Login and payment removed** | The fake sign-in and €9 checkout are gone. Saved valuations belong to the browser (anonymous ID) and nobody can list other people's. Real accounts, payment and a persistent database come later. |

New stage assumptions (VC returns, private-company discounts, survival probabilities) are the app's own assumptions, set at the midpoints of published ranges for the matching stage (re-checked: VC returns were raised one step, e.g. Startup 40% → 50%, after matching the app's stage definitions to the literature's financing stages). Their sources are listed in `reference_data.json` → `sources` and on the PDF's last page.

---

### What I added (audit only, not wired into the app, nothing committed)

| File | What it does |
|---|---|
| `audit/recompute.py` | Recomputes every figure of the reference case from the raw data, without calling the engine's math, and compares the result with the engine. Also prints "textbook" alternatives, 14 edge cases, and a sweep over all 658 industry × region combinations. |
| `audit/check_benchmarks.py` | Compares `reference_data.json` with Damodaran's original spreadsheets using its own parser, so it doesn't rely on the app's refresh script. |
| `audit/smoke_test.py` | Calls every API route in-process, writes the reference PDF, and prints the PDF's text for review. |
| `audit/reference_case_report.pdf` | The PDF the app produces today for the reference case. |

### Reference case

The brief's "famous company" line was empty, so I used the README's **Valuativa DOO** case: Tanzania, Software (Entertainment), Emerging Markets, Startup stage, Year‑1 revenue €1,000,000 growing 10%/yr, capex €30k in Years 2–5, capital needed €300k, DCF tax rate 10%. I added the operating figures from your brief: last-12-month revenue €300k, EBITDA €50k, PP&E €1.24M, committed capital €45k, 1 employee, 80% ownership, €20k capex in the use of funds. If you send me a real company's inputs, I'll add it as a second case.

### Test results

| Check | Result |
|---|---|
| Existing regression or smoke tests in the repo | **None exist.** There is no `tests/` folder and no CI. |
| `recompute.py`: independent recompute vs engine | **28/28 figures match** to the cent. The engine does exactly what its code says. |
| README "Verified numbers" vs engine | **6/6 match** (blended €1,494,025.42, post-money €1,794,025.42). |
| `check_benchmarks.py`: data vs Damodaran Jan 2026 files | **539 values checked, 0 mismatches.** |
| `smoke_test.py`: API routes | **20/23 pass.** The 3 failures are inputs that crash the server with HTTP 500 instead of returning a clear message (see N4). |

---

## 1. Summary

- **The arithmetic is right, but some of the method choices are not.** The code does exactly what it says, and the Damodaran data is copied accurately. The problems are in how the methods are set up. One "stage factor" (0.55 for Startup) is used four different ways. It cuts the Scorecard by 45%, acts as a 55% return target in the VC method, cuts Comparables by 45%, and is **added on top of** the discount rate in the DCF (18.25% + 55% = 73.25%). Each method gives a different answer from what a textbook version would give.
- **Mature companies are penalised more than idea-stage ones.** The same 0.35 → 0.85 number serves as both the "risk multiplier" and the "hurdle rate". As a multiplier, rising with maturity is fine. As a return target it is backwards. A "Maturity stage" company is discounted at 85% a year and an "Idea stage" one at 35%. The same company is worth €713k in the VC method at Maturity and €2.31M at Idea stage.
- **Projected profits come from industry averages, not from the company.** The last-12-month revenue, EBITDA, cash and PP&E you collect are not used in any calculation. Projected EBITDA is always revenue × the industry margin, and that margin leaves out R&D. For software and pharma, that overstates EBITDA by 10–18 percentage points (US Software (Entertainment): 50.8% in the app vs 34.9% in Damodaran's own EBITDA/Sales).
- **The blended number is close to a textbook blend for this case, but only because the errors cancel out.** In the reference case, Scorecard and DCF are too low, Comparables is too high, and the total lands near €1.46–1.49M either way. That is luck, not accuracy. An investor who reads the per-method pages (DCF at a 73% discount rate, a Scorecard table whose rows don't add up to its total) will lose confidence.
- **Can a customer trust the numbers today? Not yet for anything they would show an investor.** It's fine as an indicative range. Four fixes would make it defensible, and the data layer is already solid: one consistent definition of stage risk, R&D-aware margins anchored to the company's actual figures, input checks, and honest labels. See section 6.

---

## 2. Accuracy findings

Status codes:
- **CONFIRMED** means the issue exists in today's code.
- **FIXED** means it was in your old report but is gone from today's code.
- **NOT AN ISSUE** means it is not present or is correct.

Several issues in the brief came from an older build. The figures 19.6557994× and Y1 EBITDA 198k match `origin/main` exactly, not this branch (see N3).

"Engine" means `valuation_engine/__init__.py`. "Data" means `valuation_engine/reference_data.json`.

| # | Issue | Severity | Status | Expected vs actual (reference case) | File:line | Suggested fix |
|---|---|---|---|---|---|---|
| 1 | Scorecard math | High | **FIXED** (amounts) / **CONFIRMED** (table) | Each criterion's amount is now benchmark × weight × score (Opportunity = 2M × 0.25 × 0.90 = **450,000**, correct). But the PDF table's rows add up to **1,860,000** while its total row says **1,023,000**. The × 0.55 is applied between the two with no row showing it, so the table doesn't add up on the page. | Engine:636–640; report.py:701–712; index.html:1660–1670 | Add a "Subtotal (Σ weight × score × benchmark)" row before the total. Better still, remove the 0.55 (see #2), and then the rows add up to the total. |
| 2 | One 0.55 "stage factor" used four different ways | **Critical** | **CONFIRMED** (worse than described) | Scorecard × 0.55 (**1,023,000** vs Payne's textbook 1,860,000). VC: exit ÷ 1.55³ (0.55 used as a 55% *rate*). Comparables × 0.55. DCF: discount rate = WACC **+ 55 points** = 73.25%. `risk_multiplier` equals `hurdle_rate` for every stage and rises 0.35 → 0.85 with maturity. That direction is right for a multiplier but **wrong for a hurdle rate**: same company at Maturity stage gives VC €713k vs €2.31M at Idea stage. Labels mix "Hurdle rate / risk multiplier 55.0%" and "Risk multiplier 0.55". The "Hurdle rate 40%" label from your old report no longer exists. | Engine:640, 701, 768, 869; Data:12762–12822 (stage_parameters); report.py:609, 711, 748, 775, 841; index.html:1586, 1670, 1694, 1734, 1764 | Split it into two parameters with one meaning each. **(a) VC target return by stage**, falling as the company matures. Typical ranges (Sahlman; Damodaran, *Valuing Young, Start-up and Growth Companies*, 2009): start-up 50–70%, first stage 40–60%, second stage 35–50%, bridge/IPO 25–35%. **(b)** Use it **only** in the VC method. Remove it from Scorecard (Payne's method has no haircut). In Comparables, replace it with an explicit, labelled private-company/illiquidity discount if you want one. In DCF, see #3. |
| 3 | Risk counted twice in DCF | **Critical** | **CONFIRMED** | Rate used = 18.25% WACC + 55% = **73.25%**. DCF = **€367,591**; at WACC alone it is €1,795,765. The terminal value falls from 52% to 9% of EV. WACC already contains beta and the country risk premium. Damodaran says to put start-up risk **either** in a VC-style target rate applied to success-case cash flows, **or** in a cost of capital plus an explicit survival-probability adjustment. Doing both double-counts. The IPEV Valuation Guidelines (2022) likewise say a risk should be reflected once, in the cash flows or in the discount rate, not both. | Engine:868–873 | Discount at WACC (or an explicit cost of equity). Then apply a **stated** probability of survival/failure. The data already has an unused `survival_rate_by_years_since_incorporation` table (Data:12953), but it needs a cited source first. Show the rate actually used in the PDF. |
| 4 | DCF present values and terminal value | Low | **FIXED** / **NOT AN ISSUE** | Current code uses end-of-year discounting (Excel NPV convention) with no stub or mid-year adjustment. The Gordon terminal value FCF₅ × 1.02 / (r − g) is computed correctly and discounted by (1 + r)⁵. My independent recompute matches to the cent. The old figures (643,351 and 1,723,499) can't be produced by any current code path. The terminal value is 52% of EV at WACC, which is normal, but the report doesn't disclose it. | Engine:820–873 | Show "Terminal value as % of EV" and the formula used. |
| 5 | Stale dates; "2,027" label | Low | **NOT AN ISSUE** (current code) | Today's report labels years Y1–Y5 and shows only the generation date. There is no transaction date and no FY2024 label, and the "2,027" bug doesn't occur. But there is **no valuation date input at all**, so "Year 1" has no calendar meaning. | report.py:945; index.html:1476 | Add a valuation-date field. Derive FY labels from it, formatted without thousands separators. |
| 6 | Enterprise value reported as pre-money equity | **Critical** (small for this case) | **CONFIRMED** | DCF and Comparables produce enterprise value, but the summary table calls them "Pre-money value". There is no net-debt or cash bridge. The cash (€20k) and existing debt inputs are collected and ignored (debt only changes a net-profit line that nothing uses). For a company with €1M of debt, every EBITDA method would be overstated by €1M. | Engine:932–953; report.py:654 | Equity = EV − debt + excess cash. Apply this to DCF and Comparables, and show the bridge in the PDF. |
| 7 | Comparables uses forward EBITDA with a trailing multiple; labels inconsistent | **Critical** + High (label) | **CONFIRMED** | 19.37× (Damodaran's EV ÷ **trailing** EBITDA) × **forward** Y1 EBITDA of €273,685, when the actual EBITDA is €50,000. Y1 × multiple = €5.30M, × 0.55 = €2.92M. Actual EBITDA × multiple = €968k. The same method appears as "DCF Multiples method" (summary, chart, wizard, scenarios), "Comparables (DCF Multiples) method" (detail page) and "Comparables (Market Multiples)" (methodology). It isn't a DCF at all. Its line "Exit value" is really today's EV. | Engine:756–778; report.py:406, 409, 764, 774, 888; index.html:913, 1306, 1724–1743, 1805 | Use trailing (LTM) EBITDA with the trailing multiple. If forward is kept, use a forward multiple or discount Y1 back one year, and say so. Rename everywhere to **"Comparables (EV/EBITDA multiple)"**. Change "Exit value" to "Implied enterprise value". When LTM EBITDA ≤ 0, switch to EV/Sales or skip the method. |
| 8 | Two totals on the summary | — | **FIXED** | Only the weighted blend is shown, labelled "Blended pre-money valuation". The engine still calculates `simple_average_valuation` and the API returns it, but nothing displays it. | Engine:949 | Optional: remove it from the API output to avoid future confusion. |
| 9 | VC units and formulas | Low | **FIXED** (units) / formulas **NOT AN ISSUE** | The share count shows as "1,000,000" with no €. Formulas checked and correct: F = I / POST (17.4%), y = x·F/(1−F) = 210,899 new shares, p = I/y = €1.4225. Remaining issue: price per share displays as **"€1"** because money is rounded to whole euros. | Engine:704–710; report.py:756; index.html:1702 | Show price per share with 2–4 decimals. |
| 10 | Rounding and display | Low–Medium | **Mostly FIXED** | The multiple shows 19.37× everywhere and percentages use 1 decimal. Still open: (a) WACC shows **18.2%**, actual 18.25%; (b) D&A 0.9% is a **Global fallback** (Emerging Markets is "NA") but displays as if it were regional; (c) "Other opex" (1.5% of Y1 revenue, growing a fixed 10%/yr whatever growth you enter) is not shown anywhere in the PDF. | report.py:181, 597–601, 839; Engine:393, 415 | Use 2 decimals for rates. Footnote any fallback value. List every hidden assumption on an "Assumptions" page. |
| 11 | Capex inconsistency | Medium | **CONFIRMED** | Use of funds lists €20,000 capex, Y1 planned capex is €0, and nothing checks or links the two. The PDF capex table also **drops Year 1 entirely** (it shows Y2–Y5 only). | report.py:616; index.html:1565; Engine:448 | Show Y1 in the capex table. Warn when use-of-funds capex ≠ Y1 capex. |
| N1 | Industry margin leaves out R&D | **Critical** | **CONFIRMED** | EBITDA = revenue × (1 − COGS% − SG&A%) − 1.5%. Damodaran's SG&A excludes R&D, so R&D is never deducted. Engine margin vs Damodaran's own EBITDA/Sales: US Software (Entertainment) **50.8% vs 34.9%**; EM Software (System & Application) **16.2% vs 2.7%**; US Pharma 48.3% vs 33.6%. For Software (Entertainment), Emerging Markets (the reference case) it's 27.4% vs 25.9%, close. | Engine:382–416 | Use Damodaran's EBITDA/Sales directly, or add R&D/Sales as a cost line. Both are in the same `margin` file. |
| N2 | Company's actual numbers are ignored | **Critical** | **CONFIRMED** | Last-12-month revenue (300k), EBITDA (50k), cash, PP&E, committed capital and employees appear in the PDF but are used in **no** calculation. The projected margin is always the industry margin (27.4% here, vs the company's actual 16.7%). Users can't enter their own cost structure, so a loss-making plan can't be modelled. A 50k-revenue company still shows positive EBITDA. | Engine:375–451 | Start from actual margin and converge to the industry margin over 5 years. Let users override cost percentages. Show "your margin vs benchmark" side by side. |
| N3 | Live site may run older data | High | **CONFIRMED** (deployment) | `origin/main` (776891d) doesn't have the Jan-2026 benchmark refresh. On main, the reference case gives blended **€1,114,760**; this branch gives **€1,494,025**. Main's figures (19.6557994×, Y1 EBITDA 198,142, 19.8% margin) are exactly the ones in your old report. | git: `origin/main` vs `3b2642b` | Check which branch GitHub Pages and Render deploy. Merge after the fixes. Show the data date ("Damodaran, Jan 2026") on the report. |
| N4 | Inputs that crash the server (HTTP 500) | Medium | **CONFIRMED** | Revenue 0 → ZeroDivisionError. Capital needed 0 → ZeroDivisionError. Time to exit 6 → IndexError (the HTML limits it to 5, but the API doesn't). The user sees a raw "500" error. | Engine:690–710; app.py:205–210 | Validate in the input model (revenue > 0, capital > 0, 1 ≤ exit ≤ 5) and return a plain-language 400. |
| N5 | Negative or impossible results pass through | Medium | **CONFIRMED** | Negative blended value for Drugs (Biotechnology)/Japan (−€26k) and Real Estate (Development)/China (−€65k). When capital needed exceeds VC post-money, the investor stake is 290% and VC pre-money is −€3.28M, still blended into the total. | Engine:701–713, 932–950 | Treat a method whose value is ≤ 0 as "not meaningful". Drop it and re-weight, with a note. |
| N6 | Two different tax rates | Medium | **CONFIRMED** | FCF uses the user's 10%; WACC uses the country's 30%. When EBIT is negative, tax becomes a refund. | Engine:402, 507, 838 | Use one rate, defaulting to the country's statutory rate. Floor tax at 0, or carry losses forward. |
| N7 | Cost of debt uses a derived rate instead of the published one | Medium | **CONFIRMED** | The WACC uses `book_interest_rate`, which is back-calculated (e.g. **15.96%** for Software (Entertainment) / Europe). Damodaran publishes `cost_of_debt` (5.74%), and the app stores it but never uses it. Five industry/region values are above 12%. The effect is small here because the debt weight is ~8%. | Engine:506 | Use `cost_of_debt`. |
| N8 | Year‑1 working-capital change is always 0 | Low | **CONFIRMED** | The step from today's working capital to Y1's (revenue tripling from 300k to 1M) is ignored. | Engine:428 | Start from working capital on last-12-month revenue. |
| N9 | Terminal-value floor applied silently | Medium | **CONFIRMED** | Banks get WACC 2.75% < g + 2%, so the floor kicks in (`terminal_value_floor_applied = True`) but the report doesn't say so. Banks and insurers are valued on EBITDA and FCF, which isn't meaningful for them. | Engine:853–864 | Show a note in the report. For financial-sector industries, block EBITDA methods or warn. |
| N10 | DCF page describes the wrong method | High | **CONFIRMED** | The DCF page says "…then applies the same stage risk multiplier used across the other methods". The code doesn't multiply; it adds 55 points to the rate. The 73.25% rate actually used is never shown, only "Discount rate (WACC) 18.2%". | report.py:833–842; index.html:1759–1764 | Fix the wording. Show "Rate used = WACC + x%" (or, after the fix, WACC plus a survival %). |
| N11 | Scorecard options have typos and gaps | Low (typos) / Medium (gaps) | **CONFIRMED** | Typos: "Demostrated", "Deal braker", "Well definied", "No  partners" (double space), "< $50 million " (trailing space). These strings are lookup keys, so fixing them needs a data migration. Market-size options skip **$50–100M**. Revenue-potential options skip **$50–100M**. "Unwilling" scores 0 and wipes out a third of the team score. The typos from your brief ("Strenght", "Ammount", "Avalible", "Commited", "Perpetural", "Mulltiple", "requirments") are **not present** in the current code. | Data:12895, 12911, 12928, 12932–12938 | Fix the text, keeping the old strings as aliases. Add the missing ranges. |
| N12 | Two separate report builders | Low | **CONFIRMED** | `index.html` (browser print) and `report.py` (server PDF) build the same report separately, so they can drift apart. | index.html:1476–1810; report.py | Keep only the server PDF. |
| N13 | Engine comments cite a missing `docs/` folder | Low | **CONFIRMED** | The engine says the bug write-ups are in `docs/`; that folder doesn't exist. | Engine:12, 396, 549, 790 | Add the documents or remove the references. |

### Reference case, method by method (€)

| Method | As coded today | Textbook version | Why they differ |
|---|---|---|---|
| Scorecard | 1,023,000 | 1,860,000 | Extra × 0.55 (#2) |
| Venture Capital | 1,422,481 | 1,422,481 at 55%; 2,037,574 at 40% | 55% is within the start-up range; the problem is how it changes across stages (#2) |
| Comparables | 2,915,593 | 968,465 (LTM EBITDA × 19.37) | Forward vs trailing EBITDA (#7) |
| DCF | 367,591 | 1,795,765 at WACC (before any survival adjustment) | Double-counted risk (#3) |
| **Blended (stage weights)** | **1,494,025** | ≈1.46M using the column above | Errors cancel out, for this case only |

---

## 3. Data findings (benchmarks vs Damodaran, January 2026)

The source spreadsheets are in git history (commit `4ff0af9`, internal "Date updated" = 5 Jan 2026). `audit/check_benchmarks.py` compared every value for 7 industries × 11 metrics × 7 regions: Software (Entertainment), Software (System & Application), Retail (General), Restaurant/Dining, Business & Consumer Services, Drugs (Pharmaceutical) and Bank (Money Center), across US, Europe, Japan, Emerging Markets, China, India and Global.

**Result: 539 values, 0 mismatches.** Every regional value from fewer than 10 firms is correctly stored as "NA".

| Check | Status |
|---|---|
| Software (Entertainment) / Emerging Markets: COGS 40.70%, SG&A 30.43%, EV/EBITDA 19.37× | **Match** `marginemerg.xls` (COGS/Sales, SG&A/Sales) and `vebitdaemerg.xls` (EV/EBITDA, positive-EBITDA firms) exactly |
| EV/EBITDA basis | The app uses Damodaran's "only positive EBITDA firms" block (19.37×), not "all firms" (22.76×). That is defensible, but say so in the report. |
| D&A % and book interest rate | **Can't be checked directly.** Damodaran doesn't publish them; the app derives them. Five book-interest values are above 12% (e.g. Software (Entertainment) / Europe 15.96%, Electrical Equipment / Global 21.9%). See N7. |
| Margins (COGS + SG&A) | Copied correctly, but they leave out R&D, so EBITDA is overstated for R&D-heavy industries (N1). |
| Country risk data (ERP, CRP, tax) | **Not verified against the source.** The `ctryprem` file isn't in the repo and Damodaran's site was blocked here. The values are internally consistent: Germany ERP 4.23% with CRP 0; US 4.46% = 4.23% + 0.23% after Moody's Aa1 downgrade; Tanzania 10.06% = 4.23% + 5.83%. **Action:** add `ctryprem.xlsx` to the refresh tool. |
| Risk-free rate | Hard-coded at 4.00%. Damodaran's Jan 2026 sheets use a 3.95% T-bond rate. Minor, but put it in the data file with a date. |
| **"Average pre-money by stage and region" (€2,000,000 for Startup/EM)** | **No source anywhere in the repo.** All 42 values are round numbers with no citation or date. This drives the whole Scorecard method. **Must be cited (e.g. a named Dealroom/PitchBook/Carta report and year) or replaced.** |
| Scorecard weights 30/25/10/15/10/10 | Consistent with Payne's published Scorecard: team 30, opportunity 25, product 15, competition 10, partners 10. Payne then has "need for additional investment" 5 and "other" 5; the app merges these into one 10% "funding required". Fine, but cite Payne. |
| Stage weights and risk multipliers/hurdles | No source cited. They also conflict as described in #2. |
| Survival rates by year | Stored but unused, with no source. |
| "Macedonia" country name | Damodaran's own label. Consider showing "North Macedonia". |

**Claims in the PDF methodology and disclaimer:**
- **Not present in today's code:** "47,810 companies across 134 countries", "adheres to IPEV / EVS", and PitchBook/Crunchbase/Dealroom. The app couldn't back any of them up, so don't add them back unless they become true.
- **Present and accurate:** "four widely used methods", "weighted by stage", "Damodaran, NYU Stern, updated annually".
- **Present and inaccurate:** the DCF subtitle (N10).
- **Missing:** which data date was used, which values fell back to Global, and where the stage benchmarks come from.
- **Branding:** the product calls itself "Startup Valuation Tool" throughout. "Valuativa" doesn't appear in the app or PDF.

---

## 4. Validation gaps (input checks)

The only checks today are the browser's "required field" markers (index.html:1139–1146). The API accepts any number.

| Rule | Enforced today? | What happens now | Recommended |
|---|---|---|---|
| Y1 revenue vs last-12-month revenue (300k → 1M = +233%, then +10%/yr) | No | Accepted silently. Every EBITDA method is built on the jump. | Warn above +100% YoY. Show the growth curve, including the jump from last-12-month revenue to Y1. |
| PP&E or capex out of scale (PP&E 1.24M, revenue 300k, 1 employee, 45k committed) | No | PP&E is ignored entirely. | Warn when PP&E > 2× revenue for asset-light industries, or capex > 30% of revenue. |
| Projected vs actual EBITDA margin | No | The projected margin is always the industry's (27.4% vs actual 16.7%). | Show both and warn on a gap above 10 points (N2). |
| Ownership sums to 100% | No | 80% is accepted; the PDF adds an "Unallocated 20%" slice. | Require 100% ± 0.5%, or ask the user to confirm. |
| Use of funds = capital needed | No | 270k of 300k is accepted silently. | Warn and show the difference. |
| Use-of-funds capex vs Y1 capex | No | No link (#11). | Warn on mismatch. |
| Zero revenue / capital needed 0 / exit > 5 years | No | **HTTP 500 crash** (N4). | Validate with a clear message. |
| Negative or zero EBITDA | No (and the user can't enter one; N2) | Only reachable through benchmarks. VC, Comparables and DCF go negative and are still blended; blended can be negative (N5). | Mark the method "not meaningful", drop it and re-weight. Fall back to EV/Sales. |
| Capital needed > VC post-money | No | Investor stake 290%, negative pre-money. | Error: "the raise is larger than the exit supports". |
| WACC ≤ growth + 2% | Yes, silently (floor) | The floor is applied without telling the user (N9). | Show a note. |
| Missing regional benchmark (fallback) | Yes, works | Falls back to the Global value; tested with Software (Entertainment) / India, which runs fine. The fallback is disclosed only for EV/EBITDA. | Footnote every fallback value (D&A, margins, beta). |
| Banks and insurers (no meaningful EV/EBITDA) | Partly | Bank (Money Center)/US runs, using a fallback multiple and FCF. The output looks meaningful but isn't. | Block EBITDA/FCF methods for financial-sector industries, or show a warning. |
| Growth rates of −100% or worse; tax rate outside 0–60% | No | Accepted. | Range-check. |
| Country vs region mismatch (e.g. Germany + "US") | No | Accepted. | Default the region from the country and warn on mismatch. |

---

## 5. Improvement ideas, ranked by impact on getting paying customers

1. **Credibility: one clear risk model, documented on one page.** Fix #2, #3 and #7, then add an "Assumptions & sources" page listing every number used, where it comes from, its date, and whether it fell back to a broader average. This page is what an investor or accountant will check first.
2. **Explain why each method gives its number.** For each method, add a 3–4 line plain-language walk-through. Example: "Your Year‑3 EBITDA €331k × industry multiple 19.4× = exit value €6.4M. An investor wanting 55%/yr needs it to be worth €1.72M today. Minus your €300k raise = €1.42M." A small waterfall chart per method would do the same job.
3. **Use the company's own numbers.** Start from actual revenue and margin and move gradually toward the industry average (N2). Let users override cost percentages. Founders distrust a model that ignores their real numbers.
4. **Show a range, not a single figure.** Add a "football field" chart showing each method's low–high range (the scenario engine already computes ±20–30%), with the blended point marked. That's more honest and more persuasive.
5. **Show warnings in the wizard.** Run every section 4 rule as a yellow "check this" note before Calculate, and list any accepted warnings in the PDF appendix.
6. **Report clarity:**
   - EV → equity bridge (#6)
   - valuation date and FY labels (#5)
   - consistent method names (#7)
   - a subtotal row in the Scorecard table (#1)
   - show the discount rate actually used (N10)
   - show Y1 capex (#11)
   - 2-decimal rates and per-share prices (#9, #10)
7. **Make the data easy to trust and update.** Cite the stage pre-money benchmarks. Add the country-risk file to the refresh tool. Print "Damodaran data as of 5 Jan 2026" on the cover.
8. **Reliability:**
   - Turn the `audit/` scripts into automated tests that run on every change (there are no tests today).
   - Return clear errors instead of crashes.
   - Deploy one branch to both the website and the API, and check that they match (N3).
   - Before taking money, also note that login and payment are demo-only today: any email or card is accepted. Saved history is stored in SQLite on the Render server; on many Render plans the disk is wiped on redeploy, so check yours.
9. **Features later:**
   - Pre/post-money cap table showing dilution from the VC method.
   - Survival-probability DCF using a cited dataset.
   - EV/Sales comparables for pre-profit companies.
   - Downloadable Excel of the model, which accountants like.

---

## 6. Recommended fix order (top 5)

1. **One definition of stage risk (#2, #3).** Split the 0.55 into a VC target return that falls as the company matures, used only in the VC method. Remove it from Scorecard. In DCF, use WACC plus an explicit survival adjustment instead of WACC + 55 points. This changes every company's number, so do it first.
2. **Fix the profit base (N1, N2, #7).** Deduct R&D (or use Damodaran's EBITDA/Sales), anchor projections to the company's actual figures, and run Comparables on trailing EBITDA with a matching multiple.
3. **Input checks and no crashes (section 4, N4, N5).** Add range checks and plain-language warnings, and drop methods with zero or negative values from the blend.
4. **Honest labels and an equity bridge (#6, #7, #1, N10, #10, #11).** One name per method, a correct DCF description, the actual rate shown, EV − debt + cash = equity, a Scorecard subtotal row, Y1 capex, and fallback footnotes.
5. **Provenance and deployment (N3, section 3).** Cite or replace the stage pre-money benchmarks, add country-risk data to the refresh tool, put the data date on the PDF, confirm what's live, and add the audit scripts as automated tests.

Waiting for your decisions. I haven't changed, committed or pushed any app code.
