# Questions for the owner

**Status, 8 October 2026:** questions 1–11 are decided, 12 is done and 13 is settled (revenue multiple). The owner kept the app's
method on every point (option a) and the PDF was shortened (7). The reliability fixes of 8 October (AUDIT_REPORT.md,
"Fix status") keep every one of those decisions. They change the terminal value (the years after Year 5 are now a business
growing 2% forever: working capital grows 2%, capex at least D&A, profit fully taxed), so the golden test now expects
pinned blended pre-money €1,043,836.30, post-money €1,843,836.30 (was €1,050,656.82 / €1,850,656.82). With the
revenue multiple in Comparables (13) it expects €1,252,087.59 / €2,052,087.59.

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

## 13. Comparables just above break-even (settled, 8 October 2026: revenue multiple)

Since the 8 October fixes, a weaker plan never gives a higher value, with one exception that comes from decision 5
(Comparables is left out when last-12-month EBITDA is zero or negative). A company with a small positive EBITDA gets
Comparables at full weight on that small figure, which pulls the blend down; one with zero EBITDA has it left out and
the weight goes to VC and DCF. Example (Tanzania test case): EBITDA €10,000 gives €572,855, EBITDA €0 gives €742,012.
Werkpuls with EBITDA +€10,000 gives €1,097,853, with €0 €1,466,610. So a founder near break-even could raise the value
by reporting a little less profit.

- a) Keep as is and mention it in the Comparables text. Simple, but the gap stays.
- b) For loss-making companies with revenue, count Comparables at cash minus debt (what the multiple gives for zero
  EBITDA) instead of leaving it out. No gap, and a weaker EBITDA never helps; lowers the value of loss-making companies
  at the Startup stage and later, e.g. Werkpuls from €1,218,619 to about €866,000. **Recommended**, as it makes the rule
  "a weaker plan never shows a higher value" hold everywhere.
- c) Phase Comparables in: its weight rises from 0 at a 0% EBITDA margin to full weight at a 10% margin. Removes the
  jump but not the dip just above zero, and is a rule of the app's own with no published source.

**Settled by the owner's request to add a revenue multiple (a variant of b).** Comparables now takes the higher of
EBITDA × EV/EBITDA and revenue × EV/Sales (Damodaran, same industry and region), or ARR × the public SaaS EV/ARR
multiple when ARR is entered. It is left out only for a company with no revenue, no ARR and no positive EBITDA. The
value no longer jumps at break-even and never rises as EBITDA falls; `tests/test_reliability.py` steps EBITDA from
+€150,000 to −€150,000 for both test companies to check this. Tanzania test case: EBITDA €10,000 gives €958,727,
€0 €922,635, −€10,000 €886,543. Unlike option b, loss-making companies with revenue are valued higher than before, not
lower (Werkpuls €1,411,688), because their revenue now counts.


## 14. VC exit for companies still growing into their margin (decided 10 October 2026: revenue multiple at exit)

Tested as German founders (Munich pre-seed, Berlin SaaS seed with €500k ARR, Hamburg e-commerce Series A,
Heidelberg biotech), the blended values came out far below what typical rounds of their size imply, and the VC method
was €0 for most of them. Its exit used only exit-year EBITDA × EV/EBITDA. With the default exit in Year 3, the margin is
still on its way from today's (often a loss) to the industry margin in Year 5, so exit EBITDA was negative or small and
the method found no value. Investors value a company that is still growing into its margin on revenue.

**Decided by the owner (10 October 2026: "you decide how to tackle the problem"; new figures accepted the same day).** The VC exit is now the higher of
exit-year EBITDA × EV/EBITDA and exit-year revenue × EV/Sales, the rule Comparables already uses (13). More profit or
more revenue never lowers the value, so "a weaker plan never shows a higher value" still holds. A method that finds no
value still counts as €0. New reference figures: Tanzania test case €1,274,502 (was €1,103,096), German example
€1,342,446 (was €1,153,087), Werkpuls pinned €1,482,841.42 (was €1,252,087.59; `tests/test_golden.py` shows the hand
calculation). Berlin SaaS seed: €2.36M → €3.53M.

Also new: when the blended value is below what a typical round of the raise implies (round logic), the checks explain
the difference (`below_round_pricing`): the methods value what the plan supports, rounds are priced on the usual stake
and on comparable rounds, and what narrows the gap.

Also looked at:
- **Scorecard benchmark, Germany vs. other countries (decided 10 October 2026: keep, option a).** Germany uses
  Equidam's *average* of closed angel, pre-seed and seed rounds (€6.55M); every other country uses Equidam's *median
  model valuation* for its region (Europe about €2.9M), so the same company is valued about 2.3× higher in Germany than
  in Austria. Options were a) keep; b) use one kind of figure everywhere. The owner kept it: Germany is the default
  experience.
- **A market anchor for companies with revenue (built 11 October 2026 with PitchBook data; see below).** At the Startup and Expansion stages no method looks at what comparable
  rounds pay, so seed and Series A companies stay well below typical round prices (Hamburg Series A €6.96M vs €28–45M
  implied). Option: a German round median per stage (e.g. from Dealroom, EY Startup-Barometer or Equidam) × the
  Scorecard score, at a modest weight. Needs a sourced dataset; the round logic itself can't serve, as it rises with the
  amount raised.
  **Weights decided 10 October 2026:** 25% at the Startup stage, 20% at Expansion, 15% at Growth, taken proportionally
  from the other methods. **Data still needed** for each of Seed (Startup), Series A (Expansion) and Series B (Growth),
  pre-seed optional: median pre-money in Germany (DACH or Europe marked as such) of rounds closed since 2024, the
  25th-75th percentile if published, number of rounds, period, source with link and publication date. Post-money with the
  median round size also works. Likely sources: PitchBook European Venture Report, Dealroom, Equidam (pre-seed and seed
  only), Atomico State of European Tech (Europe fallback).

**Market method, built 11 October 2026.** Source: PitchBook, Q2 2026 European VC Valuations Report (20 August 2026),
median pre-money valuations by region, DACH (pp. 12-13). The owner downloaded the report; only its figures are kept
(`market_round_benchmarks` in `reference_data.json`), the PDF was removed from the repository. Figures chosen with the
owner: seed €6.4M (DACH 2025; H1 2026's €9.3M rests on fewer than 20 rounds; Equidam's German €6.55M agrees) and
Series A €23.0M (DACH long-term median of Series A-B; the 2025 and 2026 figures include Series B and rest on few
deals). No separate Series B figure exists, so the Growth stage has no market method (the 15% approved for it is not
used). Value = round benchmark × questionnaire score − debt, at least €0; weights 25% (Startup) and 20% (Expansion),
the other methods scaled down in proportion. Only for Germany, Austria and Switzerland; every other country and stage is
unchanged. The market method is left out of the "methods disagree" check, as it is above the fundamental methods by
design. New figures: German example €2,494,834 (was €1,342,446); Werkpuls pinned €2,567,131.07 (was €1,482,841.42).
Caveat: the benchmark doesn't scale with the company's size, so the stage choice matters more in DACH (a small company
at the Expansion stage gets the Series A benchmark).

## 15. Fewer weak spots for a presentation (done 11 October 2026)

A review before presenting the model to a German university startup community found that the same company (the German
example) came out at €2.49M at the Startup stage, €5.52M at Expansion and €1.89M at Growth: the stage is the
founder's own choice, and the market method switched between the seed benchmark, the Series A benchmark and nothing.
Three changes, all approved by the owner the same day:

- **Stage check (`stage_round_size`).** From the Expansion stage on, a raise below the stage's typical round size
  (Dealroom bands in `round_benchmarks`) gets a warning naming the stage the raise fits and the blended value there,
  e.g. "At the Startup stage the blended value would be €2,494,834 instead of €5,520,275". The value itself is not
  changed; the founder decides. The Startup stage gets no such check, as small seed rounds are common in Germany.
- **Market method at the Growth stage, 15%** (the weight approved in 14), with the Series A–B median (€23.0M), as
  PitchBook publishes no separate Series B figure. The German example at Growth: €1.89M → €4.81M. Still below the
  Expansion stage's €5.52M; a weight of 20% would remove that dip for this company, but 15% was the decision.
  Series B rounds are usually priced higher, so this errs low.
- **The headline is a range:** the value if revenue comes in 20% below or above the plan, with the blend as the
  central value (screen and PDF cover; `value_range` in the API). German example: €2.34M – €2.65M, central
  €2,494,834.

Not changed: the figures of every company at the Idea, Development, Startup and Expansion stages, and every
country outside DACH.

Still open at the time: a market benchmark per European region and a test against real rounds; see 16.

## 16. Market method for every European region, and a backtest tool (done 11 October 2026)

**Market method by region.** The PitchBook report the DACH figures come from (Q2 2026 European VC Valuations Report,
pp. 12-13) has the same two charts for seven regions. Every region now uses its own figures by the DACH rule (seed:
the 2025 median; Series A and Growth: the long-term Series A–B median):

| Region | Countries in the app | Seed | Series A / Growth |
|---|---|---|---|
| DACH | Germany, Austria, Switzerland, Liechtenstein | €6.4M | €23.0M |
| France & Benelux | France, Belgium, Netherlands, Luxembourg | €5.2M | €16.3M |
| Nordics | Denmark, Finland, Iceland, Norway, Sweden | €4.7M | €17.8M |
| Southern Europe | Spain, Portugal, Italy, Greece, Malta, Cyprus, Andorra | €3.6M | €13.4M |
| UK & Ireland | United Kingdom, Ireland, Isle of Man, Jersey, Guernsey | €4.9M | €20.8M |
| Central & Eastern Europe | EU member states in the region (Poland, Czech Republic, Slovakia, Hungary, Romania, Bulgaria, Croatia, Slovenia, Baltic states) | €5.5M | €25.9M |
| Israel | Israel | €6.1M | €25.4M |

The assignment of countries to regions is the app's; non-EU countries in Central & Eastern Europe, Türkiye and Russia
get no market method. PitchBook notes fewer than 20 data points for several regions (Central & Eastern Europe, DACH,
Israel, Southern Europe; for Series A–B also the Nordics). The German example's value moved from €2.49M in Germany
and €1.25M in France to €2.49M and €2.14M; German figures are unchanged. Outside Europe and Israel nothing changed.

**Backtest tool.** `audit/backtest.py` and `audit/backtest_template.csv`: one row per real round (with its source),
the script reports how far the model is from the price paid. No real rounds are in the repository: their figures
have to be collected (press releases, Handelsregister filings, Dealroom). That is the next step for a published
accuracy figure.

Still open at the time, owner's choice: the Growth stage below the Expansion stage, and no LICENSE file; see 17.

## 17. Growth-stage weight 20% and a license (decided 11 October 2026)

- **Market method at the Growth stage: 20%** (was 15%), the same as at Expansion, so a company at the Growth stage
  is no longer valued below the same company at Expansion. German example: Startup €2.49M, Expansion €5.52M, Growth
  €5.79M (was €4.81M). The Maturity stage has no market method (no Series C figure), so the same company there is
  €1.90M; the stage check (`stage_round_size`) warns when a Maturity-stage raise is below the stage's typical round.
- **License: MIT** (`LICENSE`), with a note that third-party data stays under its owners' terms.

