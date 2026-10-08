# Reliability audit: can a founder trust the number?

**Audit date:** 8 October 2026
**Code audited:** branch `claude/serene-maxwell-0d1cwh` at commit `71002f7` (same as `main`). No app code was changed.
**Earlier audit:** the 6 October 2026 accuracy audit, and the record of what was fixed after it, is kept in [`audit/AUDIT_REPORT_2026-10-06.md`](audit/AUDIT_REPORT_2026-10-06.md).

> **Missing inputs.** The two reference files (`audit_inputs/main file.xlsx` and `audit_inputs/end result.pdf`) were not in the repository or anywhere in this environment, so **the Excel column below could not be recalculated with LibreOffice** (LibreOffice is installed and ready). I used the PDF figures quoted in your brief. Every one of them can be reproduced to the euro from the first Python copy of the workbook (commit `1d2bcf3`, September 2026) plus the two workbook errors documented there. So the Excel file very likely gives the same numbers as the PDF. Once you add the files, I'll fill in the Excel column and confirm.

---

## Fix status (8 October 2026, same day)

You asked me to fix everything I found necessary, with German startups as the users. Everything below the next line is the original audit, kept as written. This section records what changed.

**Verdict after the fixes: High for the logic, still an estimate.** A weaker plan no longer shows a higher value. I checked this on 3,948 German runs (every industry × stage, each input made 30% worse): the value never rose. Contradictory inputs are refused, a business with no value gets a message instead of a number, and saved valuations keep their figures. What remains is judgement: the stage weights, VC return and survival probability are the app's stated assumptions, and the methods still disagree, so founders should quote the range shown on the cover.

| # | Finding | Status | What changed |
|---|---|---|---|
| C1 | Workbook-era reports too high | **No code change** | Your decision: whether to tell people who hold old reports. |
| M1 | A failing method was dropped, so bad news raised the value | **Fixed** | "Doesn't apply" and "finds no value" are now separate. Comparables without positive EBITDA, and banks and insurers, are still left out (your decision 5). VC and DCF that apply but find no value now count as €0 (DCF: cash net of debt), with a note. Raising more money, more capex, less revenue, a lower margin or more debt now never raises the value. |
| M2 | Revenue €0 with positive EBITDA accepted | **Fixed** | Refused, with the wizard's wording. Companies with no revenue yet now start the projection from **their own loss** (today's EBITDA ÷ Year-1 revenue) instead of the industry margin in Year 1. This applies your decision 2 to pre-revenue startups. |
| M3 | EBITDA above revenue accepted | **Fixed** | Refused with a plain message (the wizard already did this; the API now does too). |
| M4 | Absurd values with warnings only | **Fixed** | New warning when the value is above 50× last-year revenue. The PDF cover now says "Before sharing this report: N of your inputs need a second look", repeating the plausibility warning when it applies. |
| M5 | Saved valuations recalculated on reopening | **Fixed** | The saved PDF and scenarios come from what was saved; `POST /{id}/rerun` is the explicit "recalculate with today's data". Valuations saved before today are re-run as before (their stored output may predate fields the report needs). |
| m1 | VC method ignores cash | **Withdrawn, not a bug** | The VC method values the company from its exit, and reaching that exit spends both today's cash and the new money. The new money is subtracted, and today's cash is already used up inside the plan; adding it again would count it twice. Debt stays as decided (decision 6). |
| m2 | Terminal value used Year 5's working-capital growth | **Fixed, and widened** | The years after Year 5 are now consistently a business growing 2% forever: working capital grows 2%, **capex at least D&A** (Damodaran's stable-growth rule), and profit **fully taxed**. The tax part fixes a bug the new direction tests found: losses carried into Year 5 lowered tax *forever*, so a bigger past loss gave a higher value (Werkpuls at −€100k EBITDA was worth more than at −€80k). |
| m3 | Large Year-1 working-capital release | **Note added** | Info note when it exceeds 20% of Year-1 EBITDA. |
| m4 | Weights "in proportion" | **Wording fixed** | The source note now gives the actual rounded weights and calls them an app assumption. |
| m5 | WACC rounded to 0.25% | **Left as is** | €701 on the test case. Not worth changing every result for. |
| m6 | €0 valuation reports | **Fixed** | A clear message instead ("None of the methods … finds any value for the business"), naming the debt when that is the cause. |
| m7 | Browser tests never ran on GitHub | **Fixed** | The workflow installs Playwright and Chromium. All 26 browser tests pass here. |
| m8 | Comparables ignores the growth plan | **Explained** | The Comparables page says so. |
| New | Comparables just above break-even | **Open: QUESTIONS.md 13** | Still a jump at EBITDA = 0, because of decision 5. Options and a recommendation are in QUESTIONS.md. |
| — | Excel column | **Still open** | Needs `audit_inputs/main file.xlsx`. |

**Tests:** 251 pass, browser tests included (225 before). The 22 new tests in `tests/test_reliability.py` cover the direction checks on the Tanzania case and on Werkpuls, €0 methods, refused inputs, pre-revenue projections, the terminal year, the plausibility warning on the cover, the working-capital note, the weights text and saved valuations. 20 of them fail on the previous code. The independent recalculation (`audit/recompute.py`) was updated to the same rules and still matches the engine on every figure.

**How values move** (before → after):

| Case | Before | After | Why |
|---|---|---|---|
| Tanzania test case (your PDF case) | €911,380 | €907,043 | Terminal year: working capital at 2% |
| German example (Beispiel Software GmbH) | €1,005,182 | €1,018,497 | Terminal year |
| Werkpuls GmbH (German SaaS, live data) | €1,017,464 | €1,218,619 | Terminal year: Year 5 grows 25%, so the old model treated working capital as absorbing cash at that pace forever |
| Werkpuls, pinned golden figures | €1,050,656.82 | €1,043,836.30 | Terminal year (capex at least D&A offsets the working-capital change) |
| Werkpuls at Development stage, no revenue, −€300k EBITDA | €2,924,077 | €2,670,903 | Today's loss now carries into Year 1 |
| Werkpuls at Idea stage, no revenue, −€60k EBITDA | €2,979,919 | €2,848,923 | Same |
| Werkpuls raising €2.5M instead of €0.8M | €1,764,863 (higher than raising €0.8M!) | €1,151,465 (lower, as it should be) | VC counts as €0 instead of being dropped |
| Werkpuls with Year-1 revenue €300k | €444,424 | €319,767 | Same |
| German example, EBITDA €400k on €300k revenue | €3,625,254 | refused with a message | M3 |

**Changes numbers customers have seen:** yes, every one of the "Before → After" rows above. Most valuations move by a few percent. Fast-growing companies (high Year-5 growth) rise. Pre-revenue companies with real losses, and cases where VC or DCF found no value, fall. Contradictory inputs are now refused.

Not changed, for German users to know: existing tax-loss carryforwards (Verlustvortrag) can't be entered, and the German minimum taxation (Mindestbesteuerung: past losses offset profit in full only up to €1M a year, above that only 70%, 60% from 2028) and trade-tax add-backs aren't modelled. Both mostly matter for companies with large accumulated losses. Ask if you want them added.

---

## 1. Verdict

**Reliability today: Medium.** The arithmetic is now dependable:
- An independent recalculation matches the engine on 64 of 64 figures.
- The same input always gives the same answer.
- The preview, the saved valuation, the scenario slider and the PDF all show identical numbers.
- 819 benchmark values match Damodaran's files with no mismatches.
- 225 automated tests pass.
- None of 3,948 industry × region × stage runs produced a crash, a blank or a negative number.

What keeps it from "High" is judgement, not arithmetic:
- **The answer depends a lot on which method you ask.** For your test case the three methods range from €698k to €1.24M.
- **The same company is now valued 39% lower than in the PDF you have** (€911,380 today vs €1,497,178). The PDF was built on a workbook that had real errors, so the old number was too high, not the new one.
- **A few inputs can move the value the wrong way.** When a method fails, the tool drops it and gives its weight to the others. So a worse plan can produce a higher value. For example, cutting Year-1 revenue from €200k to €80k raises the value from €287k to €698k.
- **Some contradictory inputs are accepted.** Leaving last-year revenue at €0 while keeping €50k EBITDA raises the value by 11%.

A founder can trust today's number as a **well-documented, indicative range**. They should not quote it as a single precise price, and they should not rely on any report produced before September 2026.

---

## 2. Every issue found

"Effect" is the change to the blended pre-money valuation of your test case (Software (Entertainment), Tanzania, Startup stage) unless another example is named. Severity: **Critical** = the number is wrong in a way an investor would catch; **Major** = the number can be badly off for some realistic inputs; **Minor** = small or cosmetic.

| # | Severity | What is wrong | Example with numbers | Effect on final valuation | Suggested fix |
|---|---|---|---|---|---|
| C1 | **Critical** (old reports only) | The PDF you have (and any report made with the workbook-era tool) overstates the value. It carries three workbook errors: (a) every Scorecard criterion used the 30% team weight, so the weights add up to 180% instead of 100%; (b) the DCF terminal value was never discounted back to today; (c) the market risk premium in the discount rate was only the country premium, leaving out the base premium. | Scorecard €1,699,500 should be €984,500 under that tool's own formula. DCF €1,194,218 should be €270,530. Blend €1,497,178 should be €1,112,821. | **+€384,357 too high** against the same tool with the errors fixed. **+€585,798** against today's tool. | No code fix: today's engine no longer has these errors. Decide whether founders who received old reports should be told, and offer them a re-run. |
| M1 | **Major** | When a method gives no positive value, it is dropped and its weight is handed to the others. That turns bad news into a higher value. | Y1 revenue €200k gives €286,967. Y1 revenue €80k gives €697,925. Raising €1.5M gives €551,380 pre-money; raising €1.6M gives €769,542. A €1.95M Y1 capex gives €949,191, but €1.9M gives €629,600. Across all industries, regions and stages, this happens in 277 of 3,570 runs (7.8%). In those runs the value is a median 67% (€415k) higher than if the failed method counted as zero. | Test case: none. Affected cases: typically +50% to +100%. | Separate "this method does not apply" (e.g. Comparables with negative trailing EBITDA: dropping it is fine) from "this method applies and says the company is worth little" (DCF or VC ≤ 0: count it as €0, as the scenario table already does). Always show a warning when a method is dropped. |
| M2 | **Major** | Revenue €0 with EBITDA above €0 is accepted. Comparables is then still applied, while the report note says it "can't be applied". The projection jumps straight to the industry margin. | Same company with last-12-month revenue set to €0 (EBITDA still €50k): €1,015,231 instead of €911,380. | **+€103,852 (+11.4%)** for leaving a field at 0 | Reject EBITDA ≠ 0 when revenue is 0 (or ask the user to confirm). Make the Comparables status match the note. |
| M3 | **Major** | EBITDA larger than revenue is accepted. The starting margin is silently capped at 90%, and Comparables uses the full EBITDA. | EBITDA €400k on revenue €300k gives €3,366,325. The only message is a margin-gap warning. | **+€2.45M (+269%)** | Reject EBITDA > revenue with a plain message. |
| M4 | **Major** | Allowed but extreme inputs give absurd values, with warnings only. | Growth 1000%/yr (allowed up to 1000%) values a €300k-revenue company at **€2.66 billion**. Growth 100%/yr gives €4.55M. | Unbounded | Add a plausibility check on the result (e.g. above 50× last-year revenue → strong warning on the cover page). Ask for confirmation of growth above 100%/yr; it currently warns only when last-year revenue is above 0. |
| M5 | **Major** (only if saving is switched on) | A saved valuation is recalculated with current data and code whenever it is reopened or its PDF is downloaded. After a data refresh, a founder's saved number changes without notice. | `GET /valuations/{id}/report` and `/rerun` re-run the stored inputs. | Unknown; any data refresh | Store the output and draw the saved PDF from it. Offer "recalculate with today's data" as a separate, labelled action. (Saving is off in today's demo mode.) |
| m1 | Minor | Cash is handled inconsistently. Comparables and DCF add cash; the VC method doesn't (it does subtract debt). So only 70% of the company's cash reaches the blended value. | €20,000 of cash adds €14,000. With €50M of cash the value is €35.9M, not about €51M. | **−€6,000** (test case); large for cash-rich companies | Add cash in the VC method too, or treat cash the same way in every method. |
| m2 | Minor | The terminal value (years after Year 5) is built from Year 5's cash flow, including a working-capital release sized for 10% growth, but it then assumes 2% growth forever. | Y5 working-capital release €11,403; at 2% growth it would be €2,509. | **+€4,337** too high | Recalculate the working-capital change for the terminal year at the long-run growth rate. |
| m3 | Minor | Year 1 gets a €59,969 cash inflow from working capital. Suppliers are assumed to fund 15.7% of revenue at once while revenue triples, so Year-1 free cash flow (€227,527) exceeds EBITDA (€185,145). The sign is right (see lead 8), but the size is optimistic for a tripling business. | Payables (15.7%) are larger than receivables plus inventory (7.2%), so growth releases cash. | **+€8,894** (vs ignoring the opening position) | Show this line plainly in the report and warn when the working-capital inflow is above ~20% of EBITDA. |
| m4 | Minor | The methodology page says the stage weights are "carried over from the workbook, with the Scorecard's weight moved to the other methods in proportion". Exact proportions would be 29.4/35.3/35.3; the app uses 30/35/35. The weights themselves have no published source. | — | **−€2,781** if exact proportions were used | Either use exact proportions or reword ("rounded"). Label the weights as an app assumption. |
| m5 | Minor | The cost of equity and WACC are rounded to the nearest 0.25%, a habit inherited from the workbook. | WACC 17.96% shown and used as 18.00%. | **−€701** | Use unrounded rates (display can stay rounded). |
| m6 | Minor | A debt larger than the company's value gives a "€0 pre-money valuation" report instead of a clear "can't be valued" message. | Debt €10M: blended €0, post-money €300,000. The PDF does explain the zero. | — | Show a clear message rather than a €0 headline figure. |
| m7 | Minor | The 26 browser tests (wizard, scenario slider vs API, phone layout) never run on GitHub: the CI machine doesn't install Playwright. | They all pass when run here. | — | Install Playwright + Chromium in `.github/workflows/tests.yml`. |
| m8 | Minor (method choice) | Comparables uses only the last 12 months' EBITDA. For a company planning to triple revenue, this ignores the plan entirely. That is the textbook pairing with a trailing multiple, so it is not an error, but founders should know. | Comparables €697,925 vs VC €1,242,335 (1.8× apart). | Explains most of the gap between methods | Say so in the Comparables "How this number was reached" text. Optionally add a forward-multiple variant, discounted one year. |
| — | Info | The Excel workbook and the original PDF are not in the repository, so the Excel column is unverified. | — | — | Add the two files to `audit_inputs/`. |

Not issues (checked and fine today): no crash on any of 49 edge inputs (each gives a result or a plain-language message); no hard-coded transaction dates; fallback data is footnoted in the PDF; the VC page labels shares as a count, not €; only one total is shown; preview = saved = PDF; scenario slider = engine.

---

## 3. Reconciliation (Step 1)

Test case: Software (Entertainment), Emerging Markets, Startup stage, Tanzania, tax 10%, last-12-month revenue €300k, EBITDA €50k, cash €20k, PP&E €1.24M, committed capital €45k, Year-1 revenue €1,000,000 growing 10%/yr, capex 0/30k/30k/30k/30k, raising €300k, exit in 3 years. Valuation date 6 Oct 2026 (it changes only the year labels here).

- **Excel**: not recalculated, because the file wasn't supplied (see the note at the top).
- **PDF**: figures from your brief. Values marked † are worked back from those figures with the original Python copy of the workbook (`1d2bcf3`), which reproduces every PDF figure you quoted to the euro.
- **Current engine**: today's code, run directly and through the preview endpoint (identical).

| Item | Excel | Current engine | PDF | Difference |
|---|---|---|---|---|
| Benchmark data | — | Damodaran Jan 2026 | older Damodaran set | data refresh |
| EBITDA margin used | — | Y1 18.5% → Y5 25.9% (starts at the company's own 16.7%) | 19.8% → 19.8%† (1 − COGS 47.8% − SG&A 30.9% − hidden 1.5% "other costs") | see note 1 |
| EBITDA Y1 | — | 185,145 | 198,142† | −6.6% |
| EBITDA Y2 | — | 223,986 | 217,956† | +2.8% |
| EBITDA Y3 | — | 268,744 | 239,752† | +12.1% |
| EBITDA Y4 | — | 320,213 | 263,727† | +21.4% |
| EBITDA Y5 | — | 379,289 | 290,100† | +30.7% |
| Working-capital change Y1 | — | −59,969 (cash inflow) | 0† | note 2 |
| Free cash flow Y1 | — | 227,527 | 180,398† | +26.1% |
| Free cash flow Y2 | — | 181,174 | 176,617† | +2.6% |
| Free cash flow Y3 | — | 222,414 | 197,279† | +12.7% |
| Free cash flow Y4 | — | 269,791 | 220,007† | +22.6% |
| Free cash flow Y5 | — | 324,120 | 245,007† | +32.3% |
| Cost of equity | — | 19.00% | 18.50%† | note 3 |
| Discount rate (WACC) | — | 18.00% | 16.50%† (printed "17%") | note 3 |
| PV of 5 years' FCF | — | 739,135 (at 18%) | 643,351† at 16.5%; **246,293** at 71.5% | note 4 |
| Terminal value | — | 2,066,262 at Y5; **PV 903,182** | **1,723,499**, never discounted | note 4 |
| Terminal value in the DCF | — | 903,182 (55% of EV) | **947,924** = 1,723,499 × 0.55 | note 4 |
| DCF before risk | — | 1,642,317 | 1,446,473† | — |
| Risk step | — | × 50% survival probability, + cash 20,000 | 71.5% rate on cash flows, × 0.55 on terminal value | note 4 |
| **DCF result** | — | **841,159** | **1,194,218** | **−29.6%** |
| VC: exit-year (Y3) EBITDA | — | 268,744 | 239,752† | +12.1% |
| VC: EV/EBITDA multiple | — | 19.37× | 19.66×† | −1.5% (data) |
| VC: exit value | — | 5,205,380 | 4,712,515† | +10.5% |
| VC: required return | — | 50% | 55% | note 5 |
| VC: post-money | — | 1,542,335 | 1,265,487† | +21.9% |
| **VC result** | — | **1,242,335** | **965,487** | **+28.7%** |
| Comparables: EBITDA used | — | 50,000 (last 12 months) | 198,142† (Year 1, forecast) | note 6 |
| Comparables: × multiple | — | 968,465 | 3,894,641 | −75.1% |
| Comparables: discount | — | −30% private-company discount, + cash | × 0.55 | note 6 |
| **Comparables result** | — | **697,925** | **2,142,052** | **−67.4%** |
| **Scorecard result** | — | not used (company has revenue); would be 2,119,360 | **1,699,500** | note 7 |
| Weights (SC/VC/Comp/DCF) | — | 0 / 30 / 35 / 35% | 15 / 25 / 30 / 30% | note 8 |
| Simple average | — | not shown | 1,500,314 | lead 4 |
| **Weighted pre-money** | — | **911,380** | **1,497,178** | **−39.1% (−€585,798)** |
| **Post-money** | — | **1,211,380** | **1,797,178** | −32.6% |

Where the −€585,798 comes from, by method (weight × value): Scorecard −€254,925, VC +€131,328, Comparables −€398,342, DCF −€63,860.

**Every difference above 0.5%, and which side is right:**

1. **EBITDA (−6.6% to +30.7%).** The PDF ignores the company's own figures: it applies the industry cost ratios from Year 1, plus a hidden 1.5% "other costs" line, and leaves out R&D. Today's engine starts from the company's actual 16.7% margin and moves to Damodaran's EBITDA/Sales margin (25.9%, which includes R&D) by Year 5. **The current engine is right in method.** It still assumes the margin reaches the industry average in five years; that is an assumption, and the user can override it.
2. **Working-capital change Y1 and FCF Y1 (+26%).** The PDF sets the Year-1 change to 0, ignoring the jump from €300k to €1M revenue. **The current engine is right in principle.** The size of the inflow is optimistic (issue m3).
3. **Discount rate (16.5% vs 18.0%).** The workbook used only Tanzania's *extra* country premium (8.04%) as the whole market premium, leaving out the base premium. It also used a back-calculated interest rate. Today's engine uses the full premium (9.77%), newer beta (1.54 vs 1.80) and Damodaran's published cost of debt. **The current engine is right.**
4. **DCF (−29.6%).** The PDF's DCF is internally inconsistent: it discounts five years of cash at 71.5% a year (16.5% + 55%), yet takes the terminal value at 16.5% **without discounting it back from Year 5 at all**, then multiplies it by 0.55. Correctly discounted at 16.5%, the terminal value is worth €803,121 today, not €1,723,499. **The current engine is right.** It discounts everything at WACC once and then applies a stated 50% survival probability (lead 2).
5. **VC (+28.7%).** Both use the same formula. The current value is higher because Year-3 EBITDA is higher (+12.1%, note 1) and the required return is 50% instead of 55% (+10.3%); the multiple is slightly lower (−1.5%). 50% and 55% are both within the published 50–70% range for start-ups. **Neither is wrong**; the difference is an assumption.
6. **Comparables (−67.4%).** The PDF multiplies a *forecast* EBITDA by a multiple that Damodaran computes on *past* EBITDA, then uses 0.55 as a haircut. Today's engine pairs past EBITDA with the past-EBITDA multiple and uses a labelled 30% private-company discount. **The current engine is right** about the pairing, but see issue m8: it ignores the growth plan.
7. **Scorecard.** The PDF's €1,699,500 = sum of the listed amounts (€3,090,000) × 0.55, and those amounts were built with every criterion weighted 30% (lead 1). **The PDF is wrong.** Today the Scorecard is not used, because Payne designed it for pre-revenue companies.
8. **Weights.** The PDF gives the Scorecard 15%; today it gets 0% and the rest is spread over the other three methods. Neither set has a published source (issue m4).

---

## 4. Your leads from the PDF (Step 2)

1. **Scorecard 1,699,500.** Found: Σ(benchmark €2M × **30%** × score) × 0.55 = €3,090,000 × 0.55. The workbook used the team's 30% weight for every criterion (weights summed to 180%) and then cut by 0.55. Correct weights give €2M × 0.895 × 0.55 = €984,500. **Today:** the rows add up to the total, there is no 0.55 cut, and at Startup stage the Scorecard isn't used.
2. **One 0.55 used three ways; "hurdle 40%".** Confirmed in the PDF. It is a 55% yearly return in VC, a 45% haircut in Comparables, and in DCF it is *added* to the 16.5% rate for the cash flows and used as a haircut on the terminal value. So **yes, the PDF's DCF is double-penalised**, at a 71.5% rate on cash flows. **Today:** three separate, sourced stage assumptions, each used by one method only: VC return 50%, private-company discount 30%, survival probability 50%. The DCF uses WACC (18%) once and then the survival probability. That is the standard approach (Damodaran), not double counting. The survival probability is still an app assumption with a large effect (lead below, sensitivity +10% → +3.2%).
3. **PV of terminal value 1,723,499.** It is the Gordon growth value at 16.5% and 2% **not discounted**: 245,007 × 1.02 ÷ (16.5% − 2%) = €1,723,498. At exactly 17% it would be €1,666,048. The 19.66× exit-multiple route gives €5,702,147 undiscounted, or €2,657,106 discounted, so no blend was used. Discounted correctly: €803,121. **246,293** is the 5 years of cash flow discounted at 71.5%. **947,924** = 1,723,499 × 0.55. Their sum is the PDF's DCF, 1,194,218.
4. **Two totals.** 1,500,314 is the simple average and 1,497,178 the weighted one. **Today:** only the weighted blend exists; the simple average was removed from the engine.
5. **Shares in €; ownership 80%.** **Today:** shown as "Existing shares (count) 1,000,000" and "Price per new share €1.24". Ownership of 80% gives a warning in the wizard and the PDF, and the chart shows "Not specified 20.0%". The wizard blocks totals above 100%; the API only warns.
6. **Dates 12/1/23 and 2024–2028.** **Today:** no hard-coded dates. Years are labelled from the valuation date (default: today), e.g. "Y1 (Oct 2027)". A date more than a year from today gives a warning. The data date (5 Jan 2026) is on the cover.
7. **Y1 capex 0 vs 20k in use of funds.** The Y1 capex (€0) is used in the cash flows. Use of funds is not used in any calculation, which is correct: it describes how the raise is spent. **Today** a warning flags the mismatch.
8. **Negative working capital.** AR 7.0% + inventory 0.2% − AP 15.7% = −8.6% of revenue. As revenue grows, suppliers fund more, so cash is released. The engine *subtracts* the change in working capital, and a negative change becomes a cash inflow. **The sign is correct.** The size is optimistic (issues m2, m3).

---

## 5. Input checks and edge cases (Step 3)

| Input | What happens today |
|---|---|
| Y1 revenue 3.3× last year (your case) | Runs; warning "233% above the last 12 months". Good. |
| Y1 revenue 30× last year (€9M) | Runs, value €7.16M; same warning plus "methods disagree". |
| PP&E €1.24M, 1 employee | Not used in any method (correct for these methods); warning "PP&E is 4.1× revenue". Value doesn't move with PP&E (0% sensitivity). |
| Revenue blank / negative / "NaN" | Clear message, e.g. "revenue year1: should be greater than 0". |
| Last-year revenue 0, EBITDA €50k | **Runs, value rises 11%** (issue M2). |
| EBITDA 0 / −€50k | Comparables left out with a note; €748,684 / €470,448. |
| EBITDA −€1M | Clear message: no method gives a value, with reasons and advice. |
| EBITDA > revenue | **Accepted** (issue M3). |
| Growth 0% / 100% / 1000% | €747,037 / €4,546,356 / **€2.66 billion** (issue M4). Below −100% is rejected. |
| Huge numbers (revenue €1 trillion) | Runs and prints; above €10 trillion is rejected with a message. |
| Capital needed 0 / exit 0 or 6 years / tax 70% | Clear message. |
| Ownership ≠ 100% | API warns; wizard warns below 100% and blocks above. |
| Use of funds ≠ capital needed | Warning. |
| Unknown industry / country | Clear "Unrecognised input" message. |
| Debt above company value | €0 valuation with explanation (issue m6). |

**All industries × regions × stages** (94 × 7 × 6 = 3,948 runs of your case):
- **0** NaN or infinite values, **0** negative values, **0** crashes.
- **0** results outside 0.1×–50× revenue: the lowest is €145,723 (0.49× last-year revenue), the highest €7,212,065 (24×).
- 252 runs (banks and insurers past the pre-revenue stages) get a clear "can't be valued with these methods" message.
- For Startup stage the median is €558,920 (10th–90th percentile €292k–€1.20M).
- All 158 countries run.

**Fallback data:** when Damodaran has no regional figure, the global one is used, and the PDF says so in a footnote ("global figure*") and in the checks table. Example: D&A for Software (Entertainment) / Emerging Markets.

---

## 6. Robustness (Step 4)

**Sensitivity: each input ±10%, change in the final value (base €911,380)**

| Input | +10% | −10% |
|---|---|---|
| Year-1 revenue | +8.6% | −8.6% |
| EV/EBITDA multiple (benchmark) | +7.7% | −7.7% |
| Industry EBITDA margin (benchmark) | +6.3% | −6.3% |
| VC required return (50%) | −4.8% | +5.4% |
| Last-year EBITDA | +4.6% | −4.6% |
| WACC (18%) | −3.5% | +4.4% |
| Beta | −2.5% | +3.6% |
| Survival probability (50%) | +3.2% | −3.2% |
| Growth rate (10%) | +2.0% | −2.0% |
| Last-year revenue (same EBITDA → lower margin) | −1.8% | +2.2% |
| Private-company discount (30%) | −1.1% | +1.1% |
| Risk-free rate | −1.1% | +1.1% |
| Capital needed | −1.0% | +1.0% |
| Tax rate, capex, payables, cash, long-run growth | under ±0.4% each | |
| PP&E, committed capital | 0% | 0% |
| Time to exit 3 → 2 / 4 years | +12.7% / −10.4% | |

No input moves the value more than in proportion. The biggest levers are the Year-1 revenue plan and the industry multiple and margin. The "disproportionate" moves are the jumps described in issue M1, where a method drops out.

**Determinism:** three engine runs and two preview calls gave byte-identical results. Preview = saved valuation = re-run = PDF (the PDF text matches every figure).
**Scenario slider:** it only displays the server's scenarios, and the 100% scenario equals the main result. Re-running the engine at 80% and 120% revenue gives exactly the slider's €755,277 and €1,067,483.

---

## 7. Tests (Step 5)

| Suite | Result |
|---|---|
| `pytest` (as GitHub runs it) | **199 passed, 1 skipped.** The skipped item is the browser suite, which is skipped because Playwright isn't installed. |
| Browser suite (`tests/test_frontend.py`), run here with Playwright | **26 passed** |
| `audit/recompute.py` (independent recalculation) | **32/32** (your case) + **32/32** (German case) match; 3,948-run sweep: 0 problems |
| `audit/check_benchmarks.py` | **819 values, 0 mismatches** with Damodaran's files |
| `audit/smoke_test.py` (every API route) | **23/23 passed** |

**Already covered:** each method's arithmetic by hand-worked cases; the full blend; edge inputs; PDF = API; same numbers on every route; scenario slider (locally only); German tax; data vs sources.

**Not covered, proposed tests (not written yet):**
1. **Direction checks:** lowering revenue, growth, EBITDA or margin, or raising capex, debt or the raise, never raises the value. These would fail today (M1).
2. **Contradictory inputs:** EBITDA ≠ 0 with revenue 0, and EBITDA > revenue, are rejected (M2, M3). Plus: Comparables is not used when last-year revenue is 0, and the note matches the status.
3. **Cash consistency:** adding €1 of cash adds €1 to every method that values equity (m1).
4. **Terminal year:** the working-capital change in the terminal cash flow uses the long-run growth rate (m2).
5. **Plausibility band:** for allowed inputs, a value above 50× revenue always carries a warning on the cover (M4).
6. **Saved valuation stays fixed:** reopening a saved valuation after a data change shows the original figures (M5).
7. **Methodology text matches the weights** used (m4).
8. **Workbook reconciliation:** your PDF case with the old data pinned reproduces €1,497,178 under the workbook rules, and today's €911,380, with each documented difference. The Excel recalculation becomes a test once the file is provided.
9. **€0 result** is shown as "can't be valued", not as a valuation (m6).
10. **Run the browser suite in CI** (m7).

---

## 8. Prioritized fix list

"Changes numbers customers have seen" means a customer who runs the same inputs again would get a different value after the fix.

| Priority | Fix | Issues | Changes numbers customers have seen? |
|---|---|---|---|
| 1 | Decide what to tell people who hold workbook-era reports (e.g. the PDF dated April 2025). Their numbers were too high; the same inputs now give 39% less. No code change. | C1 | **Already changed** (Sept–Oct 2026 fixes) |
| 2 | Stop rewarding bad news: count a method that applies but gives ≤ €0 as €0 instead of dropping it; warn whenever a method is left out. | M1 | **Yes**, about 8% of cases, usually lower |
| 3 | Reject contradictory inputs: EBITDA ≠ 0 with revenue 0; EBITDA > revenue. | M2, M3 | **Yes**, for those inputs (they get a message instead of a value) |
| 4 | Plausibility guard on the result, and confirmation for growth above 100%/yr. | M4 | No (warnings only), unless you choose to block |
| 5 | Saved valuations keep their original figures; "recalculate with today's data" becomes a separate button. | M5 | No (it prevents future silent changes) |
| 6 | Treat cash the same in every method. | m1 | **Yes**, every company with cash (your case +€6,000) |
| 7 | Terminal-year working capital at long-run growth; show the Year-1 working-capital inflow clearly. | m2, m3 | **Yes**, most growing companies (your case −€4,337) |
| 8 | Run the browser tests in CI; add the tests in section 7. | m7 | No |
| 9 | Methodology wording on weights (or exact proportional weights); unrounded WACC. | m4, m5 | No if wording only; **yes** (≈ −€2,800 / +€700) if numbers change |
| 10 | Show "can't be valued" instead of €0; explain in the Comparables text that it ignores the growth plan. | m6, m8 | No |
| 11 | Recalculate the Excel workbook once it is in `audit_inputs/`, and fill in the Excel column. | — | No |

I haven't changed any app code. Waiting for your go-ahead before fixing anything.
