# Questions for the owner

**Status, 7 October 2026:** questions 1–11 are decided and 12 is done. The owner kept the app's method on every point (option a)
and the PDF was shortened (7). The golden test now expects the app's own Werkpuls results (pinned run: blended
pre-money €1,050,656.82, post-money €1,850,656.82).

Things the check-and-fix loop found that are method, data or design choices, so I didn't change them. Each has
a plain-language explanation, the options and my recommendation. Nothing here is a crash or a wrong sum.

Most of them come from one source: the brief describes the original Excel workbook, but the app was changed on
purpose in the October 2026 audit (AUDIT_REPORT.md, "Fix status"). The app's arithmetic matches your hand
figures wherever both use the same rule (`tests/test_golden.py`).

For reference, Werkpuls in the app with your pinned benchmarks: VC €666,107, Comparables left out, DCF
€1,380,271, blended pre-money **€1,050,657**, post-money €1,850,657. With today's live data and the 30% tax rate:
VC €145,499, DCF €1,764,863, blended **€1,017,464**, post-money €1,817,464.

## 1. Method weights (decided 7 October 2026: keep, option a)

The brief says Scorecard 15%, VC 25%, Multiples 30%, DCF 30% for every company. The app uses a weight set per
stage; at the Startup stage it is Scorecard 0%, VC 30%, Comparables 35%, DCF 35%, because the Scorecard is built
for companies without revenue (Payne) and is only used at the Idea and Development stages. A method that can't
give a value is left out and the others scaled up, so Werkpuls ends up VC 46.2% / DCF 53.8%. (Had the Scorecard
counted, Werkpuls's answers score 92.5% of the German €6.55M benchmark: €6,058,750.)

- a) Keep the stage weights. **Recommended**: it was a deliberate audit decision and the PDF explains it.
- b) Go back to fixed 15/25/30/30 for all stages.

## 2. EBITDA margin in Years 1–4 (decided 7 October 2026: keep, option a)

The brief applies the benchmark margin (27%) from Year 1, so Year-1 EBITDA is €202,500. The app starts from the
company's own last-12-month margin (−19.0% for Werkpuls) and moves in equal steps to the benchmark by Year 5, so
Year-1 EBITDA is −€73,786; Year 5 is the same in both (€792,787.50). With Year-1 at the benchmark margin, the app
reproduces every one of your EBITDA, EBIT and cash-flow figures exactly.

- a) Keep the glide path. **Recommended**: a loss-making company doesn't reach industry margins overnight.
- b) Use the benchmark margin from Year 1, as in the workbook.

## 3. Year-1 change in working capital (decided 7 October 2026: keep, option a)

The workbook sets it to 0. The app compares Year-1 working capital with today's (6% × €420,000 = €25,200), so
Werkpuls has €19,800 in Year 1 and Year-1 free cash flow is €19,800 lower than your €110,750. Years 2–5 match.

- a) Keep. **Recommended** (audit finding N8: growth ties up cash from the first year).
- b) Set Year 1 to 0 again.

## 4. One "risk multiplier" (0.55) versus three stage assumptions (decided 7 October 2026: keep, option a)

The workbook used 0.55 three ways. The app has three separate assumptions per stage, each used by one method
only, and the 0.55 doesn't appear anywhere. For the Startup stage: VC target return 50% (brief: 55%),
Comparables private-company discount 30% (brief: × 0.55, i.e. 45% off), DCF survival probability 50% (the brief's
"hurdle adjustment"). The DCF discounts at the cost of capital only and then multiplies by the survival
probability; screen and PDF label it exactly that way ("Probability of survival (for this stage) 50.0%"). The old
"Hurdle rate 40%" line no longer appears anywhere.

- a) Keep the three assumptions. **Recommended**.
- b) Return to one 0.55 figure used in all methods.

## 5. Multiples method: last 12 months instead of Year 1 (decided 7 October 2026: keep, option a)

The brief's "DCF-Multiples" is Year-1 projected EBITDA × 18 × 0.55 = €2,004,750, and it moves with the revenue
scenarios. The app's "Comparables" is last-12-month EBITDA × Damodaran's trailing multiple × (1 − 30%), minus debt
plus cash. Werkpuls had negative EBITDA in the last 12 months, so the method is left out (never negative, never
NaN), and it doesn't move with the revenue slider. The app's arithmetic on the same inputs gives your
€3,645,000 / €2,004,750 and all five sensitivity values exactly.

- a) Keep trailing EBITDA. **Recommended for now**: it matches Damodaran's trailing multiple.
- b) For loss-making companies only, fall back to Year-1 projected EBITDA, so young companies don't lose this
  method. Worth considering later; it changes results for many companies.

## 6. VC method subtracts today's debt (decided 7 October 2026: keep, option a)

The app assumes today's debt (€100,000 KfW loan) is still owed at exit and subtracts it from the exit value; the
workbook didn't. With no debt and a 55% return, the app gives exactly your post-money €1,977,846.09, pre-money
€1,177,846.09, 40.45%, 16,980 new shares at €47.11.

- a) Keep. **Recommended**: the other two cash-flow methods subtract debt too.
- b) Ignore debt in the VC method.

## 7. PDF length: 13 pages instead of 9–10 (decided: shortened, option a; now 10 pages, 11 before revenue)

Charts render, no page is empty and nothing runs outside the margins, but the Werkpuls report has 13 pages. Each
method has its own page even when it isn't used (the Scorecard page says only "Not used for this company"), use
of funds and ownership sit on a half-empty page, and the sources run over two pages.

- a) Shorten to about 10 pages: one line for unused methods on the Valuation page, use of funds and ownership on
  the summary page. **Recommended**; I can do it once you say yes.
- b) Keep the current layout.

## 8. Werkpuls shows a capex note (decided 7 October 2026: keep, option a)

The brief says Werkpuls should show no warnings. The ownership, use-of-funds and revenue checks are indeed quiet,
but the app flags "Use of funds includes €50,000 of capital expenditure, but planned capex for Year 1 is
€40,000", which is a real mismatch in the test inputs. The other notes it shows (margin gap, low VC value,
Comparables left out) explain the result rather than point at input errors.

- a) Keep the rule and accept the note for Werkpuls. **Recommended**.
- b) Change Werkpuls so the two capex figures agree (both €40,000 or both €50,000).

## 9. Demo login, paywall and per-email saving (decided 7 October 2026: keep, option a)

The brief asks to check that demo login and the demo paywall still work, and that saved valuations are scoped per
email. All three were removed in the October audit: there is no login or payment step, the public demo saves
nothing, and when saving is switched on, valuations are listed and deleted per browser (anonymous ID). Someone who
has a saved valuation's random ID can open it; that's fine without accounts, but should change with real logins.

- a) Keep it this way until real accounts come. **Recommended**.
- b) Bring back the demo login and paywall screens.

## 10. A flat tax rate for German companies in the wizard (decided 7 October 2026: keep, option a)

For Germany the wizard hides the tax-rate box and uses the German schedule (corporate tax falling from 15% to 10%
by 2032, plus solidarity surcharge and trade tax). So the brief's "corporate tax rate 30%" can only be set
through the API; the browser tests therefore use the schedule.

- a) Keep the schedule only. **Recommended**: it's more accurate and was a deliberate choice.
- b) Add an optional "use my own flat rate" box for Germany.

## 11. One industry far from the rest (decided 7 October 2026: keep, option a)

In the sweep of every industry × region × stage, Real Estate Investment Trusts (REITs) in Japan come out about 10×
the median of their stage (Expansion, Growth, Maturity), because of Damodaran's Japan REIT figures. Logged as a
warning, not a failure: REITs are rarely startups.

- a) Leave as is. **Recommended**.
- b) Use the global REIT figures for Japan.

## 12. Live deployment (done 7 October 2026: merged to `main`, live site checked twice, everything passes)

This session's network settings block both live addresses (valuation-model-a1iu.onrender.com and
snezhanatuneska-maker.github.io), so I couldn't check the live site or confirm a redeploy. The fixes are on branch
`claude/great-goldberg-0zs2fz`; Render and GitHub Pages only update from `main`.

- a) Merge the branch into `main` yourself (or tell me to), then rerun the live checks from a session that can
  reach both hosts. **Recommended**.
- b) Leave the live site as it is for now.
