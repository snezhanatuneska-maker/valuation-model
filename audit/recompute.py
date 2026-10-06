"""
Independent recomputation: rebuilds every figure of a valuation from first
principles (reading reference_data.json directly, never calling the engine's
math) and diffs it against what valuation_engine returns. Also runs edge
cases and a sweep over every stage x industry x region.

The test suite (tests/) uses this as its regression reference.

Usage:  python audit/recompute.py
Needs:  pip install -r requirements.txt
Not wired into the app.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REF = json.loads((ROOT / "valuation_engine" / "reference_data.json").read_text(encoding="utf-8"))

EM = "Emerging Markets (Asia, Latin America, Eastern Europe, Mid East and Africa)"

# ---------------------------------------------------------------------------
# Reference case: "Valuativa DOO" from README.md (Tanzania, Software
# (Entertainment), Emerging Markets, Startup stage, Y1 revenue 1,000,000 EUR
# growing 10%/yr, capex 30,000 in Y2-Y5, capital needed 300,000, DCF tax 10%),
# with the operating figures quoted in the audit brief (LTM revenue 300k,
# EBITDA 50k, PP&E 1.24M, committed capital 45k, 1 employee, 80% ownership,
# 20k capex in the use of funds). Scorecard answers chosen to give the
# README's 93% total.
# ---------------------------------------------------------------------------
REFERENCE_CASE = {
    "company_profile": {
        "company_name": "Valuativa DOO",
        "valuation_date": "2026-10-06",
        "country": "Tanzania",
        "num_founders": 1,
        "num_employees": 1,
        "year_of_incorporation": 2023,
        "company_stage": "Startup stage",
        "committed_capital": 45000,
        "industry": "Software (Entertainment)",
        "business_territory_region": EM,
        "business_model": "SaaS",
        "planned_time_to_exit_years": 3,
        "dcf_tax_rate_override": 0.10,
    },
    "market_and_team_assessment": {
        "management_team_experience": "Demonstrated experience as a CEO",
        "willingness_to_step_aside_for_ceo": "Unwilling (*Deal braker)",
        "management_team_completeness": "Entrepreneur only",
        "target_market_size": "< $50 million ",
        "revenue_potential_in_5_years": "$20 to $50 Million",
        "strength_of_competitors_in_market": "Fractured many small players",
        "strength_of_competitive_products": "Competitive products are similar to ours",
        "product_development_stage": "Orders or early sales from customers",
        "product_compelling_to_customers": "This product will be a necessity",
        "product_can_be_duplicated": "Solid patent protections",
        "sales_channels_partners": "Key sales partners in place, channels secured, order traction",
        "marketing_partners": "Key partners in place",
        "need_for_additional_funding_rounds": "None",
    },
    "operating_performance": {
        "current_revenue_last_12_months": 300000,
        "current_ebitda": 50000,
        "cash_available": 20000,
        "current_ppe_value": 1240000,
    },
    "financial_assumptions": {
        "revenue_year1": 1000000,
        "revenue_growth_rates": [0.10, 0.10, 0.10, 0.10],
        "capex_by_year": [0, 30000, 30000, 30000, 30000],
        "existing_debt_balance": 0,
    },
    "ownership": [{"name": "Founder", "ownership_pct": 0.80}],
    "funding": {
        "capital_needed": 300000,
        "use_of_funds": {"Product and R&D": 150000, "Sales and marketing": 100000,
                         "Capital expenditures": 20000},
    },
    "vc_assumptions": {"number_of_existing_shares": 1000000},
}

# ---------------------------------------------------------------------------
# German example: a Berlin SaaS company with no tax-rate override, so it uses
# the Bund rate, the stepped German tax schedule (average Hebesatz) and the
# German Scorecard table. Same valuation date as the reference case.
# ---------------------------------------------------------------------------
EU = "Europe (EU, UK, Switzerland & Scandinavia)"
GERMAN_CASE = copy.deepcopy(REFERENCE_CASE)
GERMAN_CASE["company_profile"].update({
    "company_name": "Beispiel Software GmbH",
    "country": "Germany",
    "business_territory_region": EU,
    "industry": "Software (System & Application)",
    "dcf_tax_rate_override": None,
})
GERMAN_CASE["operating_performance"].update({"current_ppe_value": 60000})



# ---------------------------------------------------------------------------
# First-principles helpers (no engine imports)
# ---------------------------------------------------------------------------
MIN_VALID = {"beta": 0.01, "equity_pct_capital": 0.01, "ev_ebitda_multiple": 0.01, "ebitda_margin": None}


def num_ok(v, min_valid):
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            and (min_valid is None or v >= min_valid))


def bench(industry, metric, region):
    """Documented fallback: region -> industry Global -> median Global of all industries."""
    min_valid = MIN_VALID.get(metric, 0.0)
    table = REF["industry_benchmarks"][industry][metric]
    if num_ok(table.get(region), min_valid):
        return table[region], "region"
    if num_ok(table.get("Global"), min_valid):
        return table["Global"], "industry_global"
    pool = sorted(d[metric]["Global"] for d in REF["industry_benchmarks"].values()
                  if num_ok(d.get(metric, {}).get("Global"), min_valid))
    mid = len(pool) // 2
    return (pool[mid] if len(pool) % 2 else (pool[mid - 1] + pool[mid]) / 2), "cross_industry"


def mround(x, m):
    return round(x / m) * m


FINANCIALS = {"Bank (Money Center)", "Banks (Regional)", "Brokerage & Investment Banking",
              "Financial Svcs. (Non-bank & Insurance)", "Insurance (General)", "Insurance (Life)",
              "Insurance (Prop/Cas.)", "Investments & Asset Management", "Reinsurance"}


def german_rate(year, hebesatz):
    """KSt for the calendar year x (1 + soli) + 3.5% x Hebesatz."""
    t = REF["country_specific"]["Germany"]["tax"]
    sched = {int(y): r for y, r in t["corporate_tax_by_year"].items()}
    kst = sched[min(max(year, min(sched)), max(sched))]
    return kst * (1 + t["solidarity_surcharge"]) + t["trade_tax_base_rate"] * hebesatz


def yearly_tax_rates(cp, country):
    """(rates for Y1..Y5, long-run rate)."""
    import datetime as dt
    tax = cp.get("dcf_tax_rate_override")
    special = REF.get("country_specific", {}).get(cp["country"], {}).get("tax")
    if tax is not None or not special:
        tax = country["corporate_tax_rate"] if tax is None else tax
        return [tax] * 5, tax
    h = cp.get("trade_tax_hebesatz")
    h = special["average_hebesatz"] if h is None else h / 100
    v = dt.date.fromisoformat(str(cp["valuation_date"]))
    rates = []
    for i in range(5):  # time-weighted over the calendar years each projection year touches
        a, b = v.replace(year=v.year + i), v.replace(year=v.year + i + 1)
        mid = dt.date(b.year, 1, 1)
        rates.append(((mid - a).days * german_rate(a.year, h) + (b - mid).days * german_rate(b.year, h))
                     / (b - a).days)
    return rates, german_rate(9999, h)


def recompute(case):
    """Textbook recomputation of every figure, written independently of the engine."""
    cp, fa, op = case["company_profile"], case["financial_assumptions"], case["operating_performance"]
    ind, reg = cp["industry"], cp["business_territory_region"]
    stage = REF["stage_parameters"][cp["company_stage"]]
    country = REF["country_data"][cp["country"]]
    special = REF.get("country_specific", {}).get(cp["country"], {})
    mkt = REF["market_parameters"]
    out = {}
    tax_by_year, tax = yearly_tax_rates(cp, country)
    rf = special["risk_free_rate"]["value"] if "risk_free_rate" in special else mkt["risk_free_rate"]
    debt, cash = fa.get("existing_debt_balance", 0), op.get("cash_available", 0)

    # --- projections: margin glides from the company's own to the target by Y5 ---
    target = fa.get("target_ebitda_margin_override")
    target = bench(ind, "ebitda_margin", reg)[0] if target is None else target
    ltm_rev = op["current_revenue_last_12_months"]
    start = max(-1.0, min(op["current_ebitda"] / ltm_rev, 0.9)) if ltm_rev > 0 else target
    da_pct = bench(ind, "da_pct_revenue", reg)[0]
    wc_pct = (bench(ind, "acc_receivable_pct_revenue", reg)[0] + bench(ind, "inventory_pct_revenue", reg)[0]
              - bench(ind, "acc_payable_pct_revenue", reg)[0])
    rev = [fa["revenue_year1"]]
    for gr in fa["revenue_growth_rates"]:
        rev.append(rev[-1] * (1 + gr))
    prev_wc, nol, fcf, ebitdas = ltm_rev * wc_pct, 0.0, [], []
    for i, r in enumerate(rev):
        ebitda = r * (start + (target - start) * (i + 1) / 5)
        ebit = ebitda - r * da_pct
        if ebit <= 0:
            taxable, nol = 0.0, nol - ebit
        else:
            used = min(nol, ebit)
            nol -= used
            taxable = ebit - used
        wc = r * wc_pct
        other = r * da_pct - fa["capex_by_year"][i] - (wc - prev_wc)
        fcf.append(ebit - taxable * tax_by_year[i] + other)
        terminal_fcf = ebit - taxable * tax + other  # Year 5 re-taxed at the long-run rate
        prev_wc = wc
        ebitdas.append(ebitda)
    out.update(ebitda=ebitdas, fcf=fcf, start_margin=start, target_margin=target, tax_by_year=tax_by_year)

    # --- WACC (CAPM with the country's total equity risk premium) ---
    beta = bench(ind, "beta", reg)[0]
    ke = mround(rf + beta * country["equity_risk_premium"], 0.0025)
    we, wd = bench(ind, "equity_pct_capital", reg)[0], bench(ind, "debt_pct_capital", reg)[0]
    we, wd = we / (we + wd), wd / (we + wd)
    wacc = mround(wd * (1 - tax) * bench(ind, "cost_of_debt", reg)[0] + we * ke, 0.0025)
    out.update(cost_of_equity=ke, wacc=wacc)

    # --- Scorecard (Payne): benchmark x sum(weight x score), no haircut ---
    L, aliases = REF["scorecard_qualitative_lookup"], REF["scorecard_option_aliases"]
    m = case["market_and_team_assessment"]
    s = lambda k: L[k][aliases.get(k, {}).get(m[k], m[k])]
    groups = {
        "strength_of_the_team": (0.30, ["management_team_experience", "willingness_to_step_aside_for_ceo",
                                        "management_team_completeness"]),
        "size_of_the_opportunity": (0.25, ["target_market_size", "revenue_potential_in_5_years"]),
        "competitive_environment": (0.10, ["strength_of_competitors_in_market", "strength_of_competitive_products"]),
        "strength_and_protection_of_product": (0.15, ["product_development_stage", "product_compelling_to_customers",
                                                      "product_can_be_duplicated"]),
        "strategic_relationships_with_partners": (0.10, ["sales_channels_partners", "marketing_partners"]),
        "funding_required": (0.10, ["need_for_additional_funding_rounds"]),
    }
    own = special.get("stage_benchmarks", {}).get("stages", {}).get(cp["company_stage"])
    benchmark = (cp.get("benchmark_pre_money_override") or (own and own["eur"])
                 or REF["scorecard_benchmarks"]["regions"][reg]["eur"])
    out["scorecard_amounts"] = {k: benchmark * w * sum(s(q) for q in qs) / len(qs) for k, (w, qs) in groups.items()}
    out["scorecard"] = sum(out["scorecard_amounts"].values())

    # --- Venture Capital: exit / (1 + target return)^T - investment ---
    mult = bench(ind, "ev_ebitda_multiple", reg)[0]
    T = cp["planned_time_to_exit_years"]
    exit_equity = ebitdas[T - 1] * mult - debt  # today's debt assumed outstanding at exit
    I = case["funding"]["capital_needed"]
    post = (exit_equity / (1 + stage["vc_target_return"]) ** T
            if ebitdas[T - 1] > 0 and exit_equity > 0 and ind not in FINANCIALS else None)
    out["vc_post"] = post
    out["vc"] = post - I if post is not None and post > I and ind not in FINANCIALS else None
    if out["vc"] is not None:
        F = I / post
        out["vc_new_shares"] = case["vc_assumptions"]["number_of_existing_shares"] * F / (1 - F)
        out["vc_price"] = I / out["vc_new_shares"]

    # --- Comparables: trailing EBITDA x multiple x (1 - private discount) - debt + cash ---
    ltm = op["current_ebitda"]
    eq = ltm * mult * (1 - stage["private_company_discount"]) - debt + cash
    # equity can't be negative: floored at zero when debt exceeds the value
    out["comparables"] = max(eq, 0.0) if ltm > 0 and ind not in FINANCIALS else None

    # --- DCF at WACC, Gordon terminal value, x survival, - debt + cash ---
    g = mkt["perpetual_growth_rate"]
    pv = sum(f / (1 + wacc) ** (i + 1) for i, f in enumerate(fcf))
    pv_tv = terminal_fcf * (1 + g) / (max(wacc, g + 0.02) - g) / (1 + wacc) ** 5
    ev = pv + pv_tv
    eq = ev * stage["survival_probability"] - debt + cash
    out.update(dcf_pv_fcf=pv, dcf_pv_tv=pv_tv, dcf_ev=ev)
    out["dcf"] = max(eq, 0.0) if ev > 0 and ind not in FINANCIALS else None

    # --- blend: stage weights rescaled over the meaningful methods ---
    vals = {"scorecard": out["scorecard"], "venture_capital": out["vc"],
            "comparables": out["comparables"], "dcf": out["dcf"]}
    w = stage["method_weights"]
    usable = {k: v for k, v in vals.items() if v is not None and w[k] > 0}
    total_w = sum(w[k] for k in usable)
    out["blended"] = sum(v * w[k] for k, v in usable.items()) / total_w if total_w else None
    out["post_money"] = out["blended"] + I if out["blended"] is not None else None
    out["method_values"] = vals
    return out


# ---------------------------------------------------------------------------
# Diff against the engine
# ---------------------------------------------------------------------------
def engine_output(case):
    import valuation_engine as ve
    return ve.run_valuation(ve.ValuationInput(**case))


def compare(case, mine=None):
    """Returns [(figure, recomputed, engine, ok)] for every figure."""
    mine = mine or recompute(case)
    e = engine_output(case)
    mv = e.method_values
    pairs = [
        ("WACC", mine["wacc"], e.wacc.wacc),
        ("Cost of equity", mine["cost_of_equity"], e.wacc.cost_of_equity),
        *[(f"EBITDA Y{i+1}", v, e.projections.years[i].ebitda) for i, v in enumerate(mine["ebitda"])],
        *[(f"Tax rate Y{i+1}", v, e.projections.years[i].tax_rate) for i, v in enumerate(mine["tax_by_year"])],
        *[(f"FCF Y{i+1}", v, e.projections.years[i].unlevered_fcf) for i, v in enumerate(mine["fcf"])],
        *[(f"Scorecard amount: {k}", v, e.scorecard.criteria[k].amount_assigned)
          for k, v in mine["scorecard_amounts"].items()],
        ("Scorecard", mine["scorecard"], mv["scorecard"].pre_money_value),
        ("VC post-money", mine["vc_post"], e.venture_capital.post_money_valuation),
        ("VC pre-money", mine["vc"], mv["venture_capital"].pre_money_value),
        ("Comparables", mine["comparables"], mv["comparables"].pre_money_value),
        ("DCF PV(FCF)", mine["dcf_pv_fcf"], e.dcf.pv_of_fcf),
        ("DCF PV(TV)", mine["dcf_pv_tv"], e.dcf.pv_of_terminal_value),
        ("DCF", mine["dcf"], mv["dcf"].pre_money_value),
        ("Blended pre-money", mine["blended"], e.blended_pre_money_valuation),
        ("Post-money", mine["post_money"], e.post_money_valuation),
    ]
    rows = []
    for name, a, b in pairs:
        tolerance = 1e-9 if name.startswith("Tax rate") else 0.01  # rates are fractions, the rest euros
        ok = (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tolerance + 1e-9 * abs(a))
        rows.append((name, a, b, ok))
    return rows


def _fmt(v):
    return f"{v:16,.2f}" if v is not None else f"{'n/m':>16s}"


def diff_against_engine(case):
    rows = compare(case)
    print(f"{'Figure':48s} {'Recomputed':>16s} {'Engine':>16s}  ok")
    for name, a, b, ok in rows:
        print(f"{name:48s} {_fmt(a)} {_fmt(b)}  {'OK' if ok else 'DIFF'}")
    bad = sum(not r[3] for r in rows)
    print(f"\n{len(rows) - bad}/{len(rows)} figures match the engine.\n")
    return bad


# ---------------------------------------------------------------------------
# Edge cases and a sweep over every industry x region x stage
# ---------------------------------------------------------------------------
def variant(**changes):
    c = copy.deepcopy(REFERENCE_CASE)
    for path, v in changes.items():
        d = c
        keys = path.split("__")
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = v
    return c


EDGE_CASES = {
    "Loss-making company (LTM EBITDA -80k)": variant(operating_performance__current_ebitda=-80_000),
    "Pre-revenue company": variant(operating_performance__current_revenue_last_12_months=0,
                                   operating_performance__current_ebitda=-60_000),
    "Negative industry margin: Drugs (Biotechnology) / Japan": variant(
        company_profile__industry="Drugs (Biotechnology)", company_profile__country="Japan",
        company_profile__business_territory_region="Japan"),
    "Maturity stage": variant(company_profile__company_stage="Maturity stage"),
    "Idea stage": variant(company_profile__company_stage="Idea stage"),
    "US / United States": variant(company_profile__country="United States",
                                  company_profile__business_territory_region="US"),
    "Germany / Europe": variant(company_profile__country="Germany",
                                company_profile__business_territory_region="Europe (EU, UK, Switzerland & Scandinavia)"),
    "Missing benchmark: India": variant(company_profile__country="India",
                                        company_profile__business_territory_region="India"),
    "Bank (Money Center) / US": variant(company_profile__industry="Bank (Money Center)",
                                        company_profile__country="United States",
                                        company_profile__business_territory_region="US"),
    "Debt larger than value (5M)": variant(financial_assumptions__existing_debt_balance=5_000_000),
    "Raise larger than VC post-money (5M)": variant(funding__capital_needed=5_000_000),
    "Exit in 5 years": variant(company_profile__planned_time_to_exit_years=5),
    "Revenue collapses (-90% in Y2)": variant(financial_assumptions__revenue_growth_rates=[-0.9, 0.1, 0.1, 0.1]),
}


def edge_cases():
    import valuation_engine as ve
    print("Edge cases:")
    bad = 0
    for desc, c in EDGE_CASES.items():
        try:
            rows = compare(c)
            r = engine_output(c)
            mv = "; ".join(f"{k} {v.pre_money_value:,.0f}" if v.status == "ok" else f"{k} {v.status}"
                           for k, v in r.method_values.items())
            mismatch = [n for n, _, _, ok in rows if not ok]
            bad += bool(mismatch)
            print(f"- {desc}\n    {mv}; blended {r.blended_pre_money_valuation:,.0f}"
                  + (f"\n    MISMATCH: {mismatch}" if mismatch else ""))
        except ve.ValuationError as ex:
            print(f"- {desc}\n    no meaningful method (clear message): {str(ex)[:120]}")
    print()
    return bad


def sweep():
    """Every industry x region x stage: no crash, positive blend (or a clear ValuationError)."""
    import valuation_engine as ve
    regions = REF["categorical_options"]["business_territory_region"]
    total, problems, no_method = 0, [], 0
    for stage in REF["stage_parameters"]:
        for ind in REF["industry_benchmarks"]:
            for reg in regions:
                c = variant(company_profile__industry=ind, company_profile__business_territory_region=reg,
                            company_profile__company_stage=stage)
                total += 1
                try:
                    r = engine_output(c)
                    if not r.blended_pre_money_valuation >= 0:
                        problems.append(f"{stage} / {ind} / {reg}: blended {r.blended_pre_money_valuation:,.0f}")
                except ve.ValuationError:
                    no_method += 1
                except Exception as ex:  # noqa: BLE001 - any other failure is a bug
                    problems.append(f"{stage} / {ind} / {reg}: {type(ex).__name__}: {ex}")
    print(f"Sweep: {total} stage x industry x region runs, {len(problems)} problems, "
          f"{no_method} with no applicable method (clear message)")
    for line in problems[:20]:
        print("   ", line)
    return problems


if __name__ == "__main__":
    print("=== Reference case: Valuativa DOO ===\n")
    bad = diff_against_engine(REFERENCE_CASE)
    print("=== German example: Beispiel Software GmbH ===\n")
    bad += diff_against_engine(GERMAN_CASE)
    bad += edge_cases()
    problems = sweep()
    sys.exit(1 if bad or problems else 0)
