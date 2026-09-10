"""
Branded PDF valuation report generator.

Rebuilds the same report the browser's "Download PDF" button already
assembles on screen (see index.html's buildFullReportHTML / the .pr-*
print stylesheet) as a real, standalone PDF file generated server-side
with reportlab - so the download is an actual one-click file instead of
going through the browser's print dialog.

Sections (in order), matching the on-screen version:
  1. Cover page
  2. Company summary (general info, business profile, market potential,
     team & product, latest operating performance)
  3. Projected financials & valuation inputs (revenue/EBITDA charts,
     industry benchmarks, planned capex, use of funds, ownership)
  4. Valuation summary (all four methods, blended value, pre/post-money)
  5. Scorecard method detail
  6. Venture Capital method detail
  7. Comparables / DCF Multiples method detail (with sensitivity table)
  8. DCF method detail
  9. Methodology & disclaimer

`build_pdf_bytes()` is the single entry point app.py calls. It takes
plain dicts (the same shapes already persisted to SQLite: a
ValuationInput.model_dump() and a dataclasses.asdict(ValuationOutput)),
so the same function works for a brand-new, unsaved valuation and for
re-downloading a past saved one.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Line, Rect, String

# ============================================================================
# Palette + typography - mirrors the .pr-* print CSS in index.html so the
# PDF matches the look of the on-screen print preview.
# ============================================================================

NAVY = colors.HexColor("#1a3153")
NAVY_LINE = colors.HexColor("#1f3a5f")
BLUE_MID = colors.HexColor("#3d6d99")
BLUE_LIGHT = colors.HexColor("#7fa8c9")
MUTED = colors.HexColor("#4a5768")
MUTED_LIGHT = colors.HexColor("#8b98a8")
ROW_STRIPE = colors.HexColor("#f7f9fb")
BORDER = colors.HexColor("#e9edf2")
BORDER_STRONG = colors.HexColor("#c8d1dc")
TOTAL_BG = colors.HexColor("#eef2f7")
TEXT = colors.HexColor("#26313f")
DISCLAIMER_BG = colors.HexColor("#f7f9fb")

PALETTE_HEX = ["#1f3a5f", "#3d6d99", "#7fa8c9", "#c9a24b", "#a3623f", "#6b8f71", "#9a7fb5", "#c2c2c2"]
PALETTE = [colors.HexColor(h) for h in PALETTE_HEX]

PAGE_SIZE = A4
PAGE_W, PAGE_H = PAGE_SIZE
MARGIN_TOP = 20 * mm
MARGIN_BOTTOM = 20 * mm
MARGIN_SIDE = 15 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_SIDE
SIDE_GAP = 16  # must match the `gap` default in side_by_side()
HALF_W = (CONTENT_W - SIDE_GAP) / 2  # exact width of one side_by_side() column

STYLES = {
    "eyebrow": ParagraphStyle(
        "eyebrow", fontName="Helvetica", fontSize=10.5, leading=13,
        textColor=MUTED_LIGHT, tracking=0.5,
    ),
    "h1": ParagraphStyle(
        "h1", fontName="Times-Bold", fontSize=30, leading=34, textColor=NAVY,
        spaceAfter=6,
    ),
    "cover_meta_label": ParagraphStyle(
        "cover_meta_label", fontName="Helvetica-Bold", fontSize=10.5, leading=20, textColor=NAVY,
    ),
    "cover_meta_value": ParagraphStyle(
        "cover_meta_value", fontName="Helvetica", fontSize=10.5, leading=20, textColor=MUTED,
    ),
    "cover_disclaimer": ParagraphStyle(
        "cover_disclaimer", fontName="Helvetica", fontSize=8.5, leading=13, textColor=MUTED_LIGHT,
    ),
    "h2": ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY,
        spaceBefore=2, spaceAfter=10, leftIndent=10, borderColor=NAVY_LINE,
        borderWidth=0, borderPadding=0,
    ),
    "h3": ParagraphStyle(
        "h3", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=colors.HexColor("#2c4266"),
        spaceBefore=10, spaceAfter=5,
    ),
    "sub": ParagraphStyle(
        "sub", fontName="Helvetica-Oblique", fontSize=9.5, leading=13, textColor=MUTED_LIGHT,
        spaceAfter=8,
    ),
    "td_label": ParagraphStyle("td_label", fontName="Helvetica", fontSize=9, leading=12, textColor=TEXT),
    "td_value": ParagraphStyle(
        "td_value", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=TEXT, alignment=2,
    ),
    "th": ParagraphStyle(
        "th", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED_LIGHT,
    ),
    "th_right": ParagraphStyle(
        "th_right", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED_LIGHT, alignment=2,
    ),
    "total_label": ParagraphStyle(
        "total_label", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=NAVY,
    ),
    "total_value": ParagraphStyle(
        "total_value", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=NAVY, alignment=2,
    ),
    "metric_label": ParagraphStyle(
        "metric_label", fontName="Helvetica", fontSize=9.5, leading=12, textColor=MUTED,
    ),
    "metric_value": ParagraphStyle(
        "metric_value", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY,
        spaceAfter=6,
    ),
    "legend_label": ParagraphStyle("legend_label", fontName="Helvetica", fontSize=8.5, leading=12, textColor=MUTED),
    "legend_value": ParagraphStyle(
        "legend_value", fontName="Helvetica-Bold", fontSize=8.5, leading=12, textColor=NAVY, alignment=2,
    ),
    "disclaimer": ParagraphStyle(
        "disclaimer", fontName="Helvetica", fontSize=8.5, leading=13, textColor=colors.HexColor("#5c6673"),
    ),
    "header_left": ParagraphStyle("header_left", fontName="Helvetica-Bold", fontSize=8, textColor=MUTED_LIGHT),
    "header_right": ParagraphStyle("header_right", fontName="Helvetica", fontSize=8, textColor=MUTED_LIGHT, alignment=2),
    "footer": ParagraphStyle(
        "footer", fontName="Helvetica", fontSize=7.5, textColor=colors.HexColor("#a7b0bc"), alignment=1,
    ),
    "callout_label": ParagraphStyle("callout_label", fontName="Helvetica", fontSize=10, textColor=MUTED),
    "callout_value": ParagraphStyle(
        "callout_value", fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, alignment=2,
    ),
}

# ============================================================================
# Formatting helpers (mirror the money()/pct() helpers in index.html)
# ============================================================================


def money(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "\u2014"
    sign = "-" if n < 0 else ""
    return f"{sign}\u20ac{abs(n):,.0f}"


def pct(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "\u2014"
    return f"{n * 100:.1f}%"


def safe(v: Any) -> str:
    if v is None or v == "":
        return "\u2014"
    return str(v)


def g(d: Optional[dict], key: str, default: Any = None) -> Any:
    if not d:
        return default
    return d.get(key, default)


LABEL_STYLE = STYLES["td_label"]
VALUE_STYLE = STYLES["td_value"]


def esc(s: Any) -> str:
    """Escapes text for use inside a reportlab Paragraph (which parses a small XML
    dialect), so literal characters like the "&" in "PP&E" or "SG&A" render correctly
    instead of being mistaken for the start of a markup entity."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def P(text: str, style: ParagraphStyle, raw: bool = False) -> Paragraph:
    """raw=True skips escaping, for the few spots that deliberately embed
    reportlab markup like <b>...</b>."""
    return Paragraph(str(text) if raw else esc(text), style)


# ============================================================================
# Table builders
# ============================================================================


def info_table(rows: list[tuple[str, str]], col_widths=None) -> Table:
    """Two-column label/value table (mirrors .pr-table used for plain info blocks)."""
    data = [[P(safe(label), LABEL_STYLE), P(safe(value), VALUE_STYLE)] for label, value in rows]
    widths = col_widths or [CONTENT_W * 0.55, CONTENT_W * 0.45]
    t = Table(data, colWidths=widths)
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i in range(len(data)):
        if i % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_STRIPE))
    t.setStyle(TableStyle(style))
    return t


def data_table(
    headers: Optional[list[str]],
    rows: list[list[str]],
    total_row: Optional[list[str]] = None,
    total_label_span: int = 1,
    col_widths=None,
    first_col_align_left: bool = True,
) -> Table:
    """
    Multi-column table with an optional header row and an optional bold
    "total" row (mirrors .pr-table with <thead> and .pr-total-row).
    """
    data = []
    n_cols = len(headers) if headers else (len(rows[0]) if rows else 1)

    def fmt_row(cells, value_style=VALUE_STYLE, label_style=LABEL_STYLE):
        out = []
        for i, c in enumerate(cells):
            if i == 0 and first_col_align_left:
                out.append(P(safe(c), label_style))
            else:
                out.append(P(safe(c), value_style))
        return out

    if headers:
        data.append([P(h, STYLES["th"] if i == 0 else STYLES["th_right"]) for i, h in enumerate(headers)])
    for r in rows:
        data.append(fmt_row(r))
    total_idx = None
    if total_row:
        data.append(fmt_row(total_row, value_style=STYLES["total_value"], label_style=STYLES["total_label"]))
        total_idx = len(data) - 1

    t = Table(data, colWidths=col_widths, repeatRows=1 if headers else 0)
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    start = 1 if headers else 0
    for i in range(len(rows)):
        if i % 2 == 1:
            r = start + i
            style.append(("BACKGROUND", (0, r), (-1, r), ROW_STRIPE))
    if headers:
        style.append(("LINEBELOW", (0, 0), (-1, 0), 1.1, BORDER_STRONG))
    if total_row:
        style += [
            ("BACKGROUND", (0, total_idx), (-1, total_idx), TOTAL_BG),
            ("LINEABOVE", (0, total_idx), (-1, total_idx), 1.2, NAVY_LINE),
            ("LINEBELOW", (0, total_idx), (-1, total_idx), 0, colors.white),
        ]
        if total_label_span > 1:
            style.append(("SPAN", (0, total_idx), (total_label_span - 1, total_idx)))
    t.setStyle(TableStyle(style))
    return t


# ============================================================================
# Charts
# ============================================================================


def bar_chart(categories: list[str], values: list[float], color=NAVY_LINE, width=CONTENT_W / 2 - 8, height=110) -> Drawing:
    d = Drawing(width, height)
    pad_bottom, pad_top, pad_side = 20, 16, 4
    max_v = max([abs(v) for v in values] + [1]) * 1.18
    n = max(len(categories), 1)
    gap = (width - pad_side * 2) / n
    bar_w = min(gap * 0.55, 40)
    d.add(Line(pad_side, pad_bottom, width - pad_side, pad_bottom, strokeColor=colors.HexColor("#d5dbe3")))
    for i, (cat, v) in enumerate(zip(categories, values)):
        h = (v / max_v) * (height - pad_bottom - pad_top) if max_v else 0
        x = pad_side + i * gap + (gap - bar_w) / 2
        d.add(Rect(x, pad_bottom, bar_w, max(h, 0), fillColor=color, strokeColor=None))
        label = f"\u20ac{v / 1000:,.0f}k" if abs(v) >= 1000 else money(v)
        d.add(String(x + bar_w / 2, pad_bottom + h + 4, label, fontName="Helvetica-Bold", fontSize=6.5,
                      fillColor=MUTED, textAnchor="middle"))
        d.add(String(x + bar_w / 2, pad_bottom - 11, str(cat), fontName="Helvetica", fontSize=6.5,
                      fillColor=MUTED_LIGHT, textAnchor="middle"))
    return d


def pie_chart(values: list[float], size=90) -> Drawing:
    d = Drawing(size, size)
    total = sum(max(v, 0) for v in values) or 1
    pie = Pie()
    pie.x, pie.y = 2, 2
    pie.width = pie.height = size - 4
    pie.data = [max(v, 0.0001) for v in values]
    pie.labels = None
    pie.sideLabels = False
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 1.2
    for i in range(len(values)):
        pie.slices[i].fillColor = PALETTE[i % len(PALETTE)]
    d.add(pie)
    return d


def legend_table(entries: list[tuple[str, float]], fmt=money) -> Table:
    rows = []
    for i, (label, value) in enumerate(entries):
        dot = Table([[""]], colWidths=[7], rowHeights=[7])
        dot.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), PALETTE[i % len(PALETTE)])]))
        rows.append([dot, P(safe(label), STYLES["legend_label"]), P(fmt(value), STYLES["legend_value"])])
    t = Table(rows, colWidths=[10, (HALF_W - 10) * 0.62, (HALF_W - 10) * 0.38])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def side_by_side(left, right, gap=16) -> Table:
    w = (CONTENT_W - gap) / 2
    t = Table([[left, right]], colWidths=[w, w])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


# ============================================================================
# Labels (mirrors index.html's SCORECARD_LABELS / CRITERIA_LABELS / METHOD_LABELS)
# ============================================================================

SCORECARD_LABELS = {
    "management_team_experience": "Management team experience",
    "willingness_to_step_aside_for_ceo": "Willingness to step aside for an experienced CEO",
    "management_team_completeness": "How complete is the management team?",
    "product_development_stage": "Product development stage",
    "product_compelling_to_customers": "Is the product compelling to customers?",
    "product_can_be_duplicated": "Can this product be duplicated by others?",
    "strength_of_competitors_in_market": "Strength of competitors in the market",
    "strength_of_competitive_products": "Strength of competitive products",
    "target_market_size": "Target market size",
    "revenue_potential_in_5_years": "Revenue potential in 5 years",
    "sales_channels_partners": "Sales channels / sales partners",
    "marketing_partners": "Marketing partners",
    "need_for_additional_funding_rounds": "Need for additional funding rounds",
}
MARKET_POTENTIAL_KEYS = [
    "strength_of_competitors_in_market", "strength_of_competitive_products",
    "target_market_size", "revenue_potential_in_5_years",
    "sales_channels_partners", "marketing_partners",
]
TEAM_PRODUCT_KEYS = [
    "management_team_experience", "willingness_to_step_aside_for_ceo",
    "management_team_completeness", "product_development_stage",
    "product_compelling_to_customers", "product_can_be_duplicated",
    "need_for_additional_funding_rounds",
]
CRITERIA_LABELS = {
    "strength_of_the_team": "Strength of the team",
    "size_of_the_opportunity": "Size of the opportunity",
    "competitive_environment": "Competitive Environment",
    "strength_and_protection_of_product": "Strength & Protection of Product",
    "strategic_relationships_with_partners": "Strategic Relationships with partners",
    "funding_required": "Funding Required",
}
METHOD_LABELS = {
    "scorecard": "Scorecard method",
    "venture_capital": "Venture Capital method",
    "dcf_multiples": "DCF Multiples method",
    "dcf": "DCF method",
}
METHOD_CHART_LABELS = {"scorecard": "Scorecard", "venture_capital": "VC", "dcf_multiples": "DCF Multiples", "dcf": "DCF"}
BENCHMARK_LABELS = [
    ("cogs_pct_revenue", "COGS (% of revenue)"),
    ("sga_pct_revenue", "SG&A (% of revenue)"),
    ("da_pct_revenue", "D&A (% of revenue)"),
    ("acc_receivable_pct_revenue", "Accounts receivable (% of revenue)"),
    ("inventory_pct_revenue", "Inventory (% of revenue)"),
    ("acc_payable_pct_revenue", "Accounts payable (% of revenue)"),
]


# ============================================================================
# Logo handling
# ============================================================================


def _decode_logo(logo_base64: Optional[str]) -> Optional[bytes]:
    if not logo_base64:
        return None
    try:
        raw_b64 = logo_base64
        if "," in raw_b64 and raw_b64.strip().lower().startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        raw = base64.b64decode(raw_b64)
        # Validate it's actually a readable image before returning it.
        ImageReader(io.BytesIO(raw)).getSize()
        return raw
    except Exception:
        return None


def _fit_image(logo_bytes: bytes, max_w: float, max_h: float) -> Image:
    iw, ih = ImageReader(io.BytesIO(logo_bytes)).getSize()
    scale = min(max_w / iw, max_h / ih, 1.0)
    return Image(io.BytesIO(logo_bytes), width=iw * scale, height=ih * scale)


# ============================================================================
# Page header / footer (drawn on every "Content" page, skipped on the cover)
# ============================================================================


def _make_page_decorator(company_name: str, today_str: str, logo_bytes: Optional[bytes]):
    def _draw(canvas, doc):
        canvas.saveState()
        header_y = PAGE_H - MARGIN_TOP + 9 * mm
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(MUTED_LIGHT)
        canvas.drawString(MARGIN_SIDE, header_y, company_name.upper())
        if logo_bytes is not None:
            try:
                reader = ImageReader(io.BytesIO(logo_bytes))
                iw, ih = reader.getSize()
                max_w, max_h = 26 * mm, 8 * mm
                scale = min(max_w / iw, max_h / ih, 1.0)
                w, h = iw * scale, ih * scale
                canvas.drawImage(
                    reader, PAGE_W - MARGIN_SIDE - w, header_y - h + 6, width=w, height=h,
                    preserveAspectRatio=True, mask="auto",
                )
            except Exception:
                pass
        else:
            canvas.setFont("Helvetica", 8)
            canvas.drawRightString(PAGE_W - MARGIN_SIDE, header_y, "COMPANY VALUATION REPORT")
        canvas.setStrokeColor(BORDER)
        canvas.line(MARGIN_SIDE, header_y - 4, PAGE_W - MARGIN_SIDE, header_y - 4)

        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#a7b0bc"))
        canvas.drawCentredString(
            PAGE_W / 2, MARGIN_BOTTOM - 10 * mm,
            f"Startup Valuation Tool \u00b7 Generated {today_str} \u00b7 Informational estimate, not a certified appraisal",
        )
        canvas.restoreState()

    return _draw


def _draw_nothing(canvas, doc):
    pass


# ============================================================================
# Page builders - each returns a list of flowables
# ============================================================================


def _cover_page(company_name: str, today_str: str, logo_bytes: Optional[bytes]) -> list:
    story = [Spacer(1, 55 * mm)]
    band = Table([[""]], colWidths=[35 * mm], rowHeights=[2.5])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), NAVY_LINE)]))
    story.append(band)
    story.append(Spacer(1, 16))
    if logo_bytes is not None:
        try:
            story.append(_fit_image(logo_bytes, 45 * mm, 20 * mm))
            story.append(Spacer(1, 14))
        except Exception:
            pass
    story.append(P("COMPANY VALUATION REPORT", STYLES["eyebrow"]))
    story.append(P(company_name, STYLES["h1"]))
    meta_rows = [
        [P("Prepared for", STYLES["cover_meta_label"]), P(company_name, STYLES["cover_meta_value"])],
        [P("Prepared with", STYLES["cover_meta_label"]), P("Startup Valuation Tool", STYLES["cover_meta_value"])],
        [P("As of", STYLES["cover_meta_label"]), P(today_str, STYLES["cover_meta_value"])],
    ]
    meta = Table(meta_rows, colWidths=[35 * mm, CONTENT_W - 35 * mm])
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(Spacer(1, 8))
    story.append(meta)
    story.append(Spacer(1, 40))
    story.append(P(
        "This report is an automated, informational estimate blending four standard valuation "
        "methodologies. It is not a certified appraisal, and should not be relied on as financial, "
        "investment, or legal advice.",
        STYLES["cover_disclaimer"],
    ))
    return story


def _company_summary_page(cp: dict, mkt: dict, ops: dict) -> list:
    story = [P("Company summary", STYLES["h2"])]

    general = info_table([
        ("Company name", g(cp, "company_name")),
        ("Contact name", g(cp, "contact_name")),
        ("Contact email", g(cp, "contact_email")),
        ("Company address", g(cp, "address")),
        ("Website", g(cp, "website")),
        ("Country", g(cp, "country")),
        ("Number of founders", g(cp, "num_founders")),
        ("Number of employees", g(cp, "num_employees")),
        ("Year of incorporation", g(cp, "year_of_incorporation")),
        ("Company stage", g(cp, "company_stage")),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6])
    business = info_table([
        ("Business activity", g(cp, "business_activity")),
        ("Industry", g(cp, "industry")),
        ("Business territory", g(cp, "business_territory_region")),
        ("Business model", g(cp, "business_model")),
        ("Committed capital", money(g(cp, "committed_capital", 0))),
        ("Exit strategy", g(cp, "exit_strategy")),
        ("Planned time to exit (yrs)", g(cp, "planned_time_to_exit_years")),
        ("Tax rate assumption", pct(g(cp, "dcf_tax_rate_override", 0))),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6])

    left_block = [P("General info", STYLES["h3"]), general]
    right_block = [P("Business profile", STYLES["h3"]), business]
    story.append(side_by_side(left_block, right_block))
    story.append(Spacer(1, 10))

    competitors = [g(mkt, "key_competitor_1"), g(mkt, "key_competitor_2"), g(mkt, "key_competitor_3")]
    competitors = [c for c in competitors if c]
    market_rows = [(SCORECARD_LABELS.get(k, k), g(mkt, k)) for k in MARKET_POTENTIAL_KEYS]
    market_rows.append(("Key competitors", ", ".join(competitors) if competitors else None))
    market_table = info_table(market_rows, col_widths=[HALF_W * 0.4, HALF_W * 0.6])

    team_rows = [(SCORECARD_LABELS.get(k, k), g(mkt, k)) for k in TEAM_PRODUCT_KEYS]
    team_table = info_table(team_rows, col_widths=[HALF_W * 0.4, HALF_W * 0.6])
    perf_table = info_table([
        ("Current revenue", money(g(ops, "current_revenue_last_12_months"))),
        ("Current EBITDA", money(g(ops, "current_ebitda"))),
        ("Cash currently available", money(g(ops, "cash_available"))),
        ("Current PP&E value", money(g(ops, "current_ppe_value"))),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6])

    left_block2 = [P("Market potential", STYLES["h3"]), market_table]
    right_block2 = [P("Team & product", STYLES["h3"]), team_table, P("Latest operating performance", STYLES["h3"]), perf_table]
    story.append(side_by_side(left_block2, right_block2))
    return story


def _projections_page(cp: dict, fin: dict, funding: dict, ownership: list, output: dict, benchmark: dict) -> list:
    story = [P("Projected financials & valuation inputs", STYLES["h2"])]
    years = g(output, "projections", {}).get("years", [])
    year_labels = [y.get("year_label", "") for y in years]
    revenue_vals = [y.get("revenue", 0) for y in years]
    ebitda_vals = [y.get("ebitda", 0) for y in years]

    rev_block = [P("Revenue by year", STYLES["h3"]), bar_chart(year_labels, revenue_vals, color=NAVY_LINE)]
    ebitda_block = [P("EBITDA by year", STYLES["h3"]), bar_chart(year_labels, ebitda_vals, color=BLUE_MID)]
    story.append(side_by_side(rev_block, ebitda_block))
    story.append(Spacer(1, 8))

    region = g(cp, "business_territory_region")

    def bm_pct(key):
        table = g(benchmark, key)
        if table and region in table:
            return pct(table[region])
        return "\u2014"

    dcf = g(output, "dcf", {})
    scorecard = g(output, "scorecard", {})
    dm = g(output, "dcf_multiples", {})
    industry = g(cp, "industry")
    story.append(P(f"Industry benchmarks \u2014 {safe(industry)} / {safe(region)}", STYLES["h3"]))
    story.append(info_table([
        ("Hurdle rate / risk multiplier", pct(g(dcf, "hurdle_rate"))),
        ("Benchmark average pre-money valuation (stage)", money(g(scorecard, "benchmark_pre_money_valuation"))),
        ("EV/EBITDA multiple", f"{g(dm, 'ev_ebitda_multiple', 0):.2f}x"),
        *[(label, bm_pct(key)) for key, label in BENCHMARK_LABELS],
    ]))

    story.append(P("Planned capital expenditures", STYLES["h3"]))
    capex = g(fin, "capex_by_year", [0, 0, 0, 0, 0])[1:]
    story.append(data_table(["Y2", "Y3", "Y4", "Y5"], [[money(c) for c in capex]], first_col_align_left=False))
    story.append(Spacer(1, 10))

    funds_entries = [(k, v) for k, v in (g(funding, "use_of_funds") or {}).items() if v]
    ownership_entries = [(o.get("name"), o.get("ownership_pct")) for o in (ownership or []) if o.get("ownership_pct", 0) > 0]
    allocated = sum(v for _, v in ownership_entries)
    if allocated < 0.999:
        ownership_entries.append(("Unallocated", 1 - allocated))

    funds_block = [P("Use of funds", STYLES["h3"])]
    if funds_entries:
        funds_block.append(pie_chart([v for _, v in funds_entries]))
        funds_block.append(Spacer(1, 4))
        funds_block.append(legend_table(funds_entries, fmt=money))
    else:
        funds_block.append(P("\u2014", STYLES["td_label"]))

    own_block = [P("Ownership structure", STYLES["h3"])]
    if ownership_entries:
        own_block.append(pie_chart([v for _, v in ownership_entries]))
        own_block.append(Spacer(1, 4))
        own_block.append(legend_table(ownership_entries, fmt=pct))
    else:
        own_block.append(P("\u2014", STYLES["td_label"]))

    story.append(side_by_side(funds_block, own_block))
    return story


def _valuation_summary_page(output: dict) -> list:
    story = [P("Valuation", STYLES["h2"])]
    story.append(P("Blended average of four valuation methodologies, weighted by company stage.", STYLES["sub"]))

    method_values = g(output, "method_values", {})
    rows = []
    cats, vals = [], []
    for key, mv in method_values.items():
        rows.append([METHOD_LABELS.get(key, key), pct(mv.get("weight")), money(mv.get("pre_money_value")), money(mv.get("weighted_value"))])
        cats.append(METHOD_CHART_LABELS.get(key, key))
        vals.append(mv.get("pre_money_value", 0))
    cats.append("Blended")
    vals.append(g(output, "blended_pre_money_valuation"))

    total_row = ["Blended pre-money valuation", "", "", money(g(output, "blended_pre_money_valuation"))]
    story.append(data_table(
        ["Method", "Weight", "Pre-money value", "Weighted value"], rows, total_row=total_row, total_label_span=3,
        col_widths=[CONTENT_W * 0.34, CONTENT_W * 0.18, CONTENT_W * 0.24, CONTENT_W * 0.24],
    ))
    story.append(Spacer(1, 12))
    story.append(bar_chart(cats, vals, color=NAVY_LINE, width=CONTENT_W, height=130))
    story.append(Spacer(1, 12))

    def callout(label, value, emphasize=False):
        t = Table([[P(label, STYLES["callout_label"]), P(value, STYLES["callout_value"])]],
                  colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
        bg = TOTAL_BG if emphasize else ROW_STRIPE
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    story.append(callout("Pre-money valuation", money(g(output, "blended_pre_money_valuation"))))
    story.append(Spacer(1, 6))
    story.append(callout("Capital needed", money(g(output, "capital_needed"))))
    story.append(Spacer(1, 6))
    story.append(callout("Post-money valuation", money(g(output, "post_money_valuation")), emphasize=True))
    return story


def _method_value_header(label: str, value: str) -> list:
    return [P(label, STYLES["metric_label"]), P(value, STYLES["metric_value"])]


def _scorecard_page(sc: dict) -> list:
    story = [P("Scorecard method", STYLES["h2"])]
    story += _method_value_header("Pre-money valuation", money(g(sc, "pre_money_valuation")))
    story.append(P(
        "Compares the company's qualitative traits against a benchmark average company for the same "
        "stage/region, scoring each of six weighted criteria above or below 100%.", STYLES["sub"],
    ))
    criteria = g(sc, "criteria", {})
    rows = [[CRITERIA_LABELS.get(k, k), pct(c.get("weight")), pct(c.get("score")), money(c.get("amount_assigned"))]
            for k, c in criteria.items()]
    total_row = ["Pre-money valuation", "", "", money(g(sc, "pre_money_valuation"))]
    story.append(data_table(
        ["Criterion", "Weight", "Score", "Amount assigned"], rows, total_row=total_row, total_label_span=3,
        col_widths=[CONTENT_W * 0.4, CONTENT_W * 0.18, CONTENT_W * 0.18, CONTENT_W * 0.24],
    ))
    story.append(Spacer(1, 10))
    story.append(info_table([
        ("Benchmark average pre-money valuation", money(g(sc, "benchmark_pre_money_valuation"))),
        ("Risk multiplier", g(sc, "risk_multiplier")),
    ]))
    return story


def _benchmark_source_note(source: str, label: str = "EV/EBITDA multiple") -> Optional[str]:
    """A short, honest note when a benchmark value had to fall back from the
    requested industry+region, because Damodaran's raw source data for that
    exact combination was missing or unusable (common for EV/EBITDA in
    financial-sector industries, where EBITDA isn't a meaningful metric)."""
    if source == "industry_global":
        return f"Note: no {label} data for this region \u2014 using this industry's global average instead."
    if source == "cross_industry":
        return f"Note: no {label} data for this industry/region \u2014 using a broader cross-industry benchmark instead."
    return None


def _vc_page(vc: dict) -> list:
    story = [P("Venture Capital method", STYLES["h2"])]
    story += _method_value_header("Pre-money valuation", money(g(vc, "pre_money_valuation")))
    story.append(P(
        "Values the company by discounting a projected exit value (at the planned exit year) back to "
        "today, using a hurdle rate that compensates investors for early-stage risk.", STYLES["sub"],
    ))
    story.append(P("Exit-year base model", STYLES["h3"]))
    story.append(info_table([
        ("Exit-year revenue", money(g(vc, "exit_year_revenue"))),
        ("Exit-year EBITDA", money(g(vc, "exit_year_ebitda"))),
        ("EV/EBITDA multiple", f"{g(vc, 'ev_ebitda_multiple', 0):.2f}x"),
        ("Exit value", money(g(vc, "exit_value"))),
    ]))
    note = _benchmark_source_note(g(vc, "ev_ebitda_multiple_source"))
    if note:
        story.append(P(note, STYLES["sub"]))
    story.append(P("Discounting to present value", STYLES["h3"]))
    story.append(info_table([
        ("Time to exit (years)", g(vc, "time_to_exit")),
        ("Hurdle rate / risk multiplier", pct(g(vc, "hurdle_rate"))),
        ("Investment amount", money(g(vc, "investment_amount"))),
        ("Number of existing shares", f"{g(vc, 'number_of_existing_shares', 0):,.0f}"),
        ("Post-money valuation", money(g(vc, "post_money_valuation"))),
        ("Pre-money valuation", money(g(vc, "pre_money_valuation"))),
        ("Ownership fraction \u2014 investors", pct(g(vc, "ownership_fraction_investors"))),
        ("Ownership fraction \u2014 entrepreneurs", pct(g(vc, "ownership_fraction_entrepreneurs"))),
        ("Number of new shares", f"{round(g(vc, 'number_of_new_shares', 0)):,}"),
        ("Price per share", money(g(vc, "price_per_share"))),
        ("Final wealth \u2014 investors (at exit)", money(g(vc, "final_wealth_investors"))),
        ("Final wealth \u2014 entrepreneurs (at exit)", money(g(vc, "final_wealth_entrepreneurs"))),
    ]))
    return story


def _dcf_multiples_page(dm: dict) -> list:
    story = [P("Comparables (DCF Multiples) method", STYLES["h2"])]
    story += _method_value_header("Pre-money valuation", money(g(dm, "pre_money_valuation")))
    story.append(P(
        "Applies an industry EV/EBITDA multiple to Year 1 EBITDA, then discounts by the stage risk "
        "multiplier \u2014 the market-multiples counterpart to the VC method.", STYLES["sub"],
    ))
    story.append(info_table([
        ("Year 1 revenue", money(g(dm, "revenue"))),
        ("Year 1 EBITDA", money(g(dm, "ebitda"))),
        ("EV/EBITDA multiple", f"{g(dm, 'ev_ebitda_multiple', 0):.2f}x"),
        ("Exit value", money(g(dm, "exit_value"))),
        ("Risk multiplier", g(dm, "risk_multiplier")),
        ("Pre-money valuation", money(g(dm, "pre_money_valuation"))),
    ]))
    note = _benchmark_source_note(g(dm, "ev_ebitda_multiple_source"))
    if note:
        story.append(P(note, STYLES["sub"]))
    story.append(P(
        "See \u201cScenario & sensitivity\u201d for how this method's value shifts across a range of "
        "Year 1 revenue outcomes.", STYLES["sub"],
    ))
    return story


def _scenario_sensitivity_page(scenarios: dict) -> list:
    story = [P("Scenario & sensitivity", STYLES["h2"])]
    story.append(P(
        "How the valuation shifts if Year 1 revenue comes in above or below plan (with the rest of "
        "the growth trajectory scaling proportionally). Scorecard doesn't move \u2014 it scores "
        "qualitative traits, not revenue \u2014 while Venture Capital, DCF Multiples, and DCF each "
        "move through their own full projection.", STYLES["sub"],
    ))
    if not scenarios:
        story.append(P(
            "Scenario data wasn't available when this report was generated.", STYLES["td_label"],
        ))
        return story

    labels = list(scenarios.keys())
    n = len(labels)

    def val(label, *path):
        d = scenarios[label]
        for key in path:
            d = d.get(key, {}) if isinstance(d, dict) else {}
        return d if not isinstance(d, dict) else None

    rows = [
        ["Scorecard"] + [money(val(l, "scorecard", "pre_money_valuation")) for l in labels],
        ["Venture Capital"] + [money(val(l, "venture_capital", "pre_money_valuation")) for l in labels],
        ["DCF Multiples"] + [money(val(l, "dcf_multiples", "pre_money_valuation")) for l in labels],
        ["DCF"] + [money(val(l, "dcf", "hurdle_adjusted", "enterprise_value")) for l in labels],
    ]
    total_row = ["Blended pre-money"] + [money(val(l, "blended_pre_money_valuation")) for l in labels]
    headers = ["Method"] + labels
    col_w = [CONTENT_W * 0.28] + [CONTENT_W * 0.72 / n] * n
    story.append(data_table(headers, rows, total_row=total_row, col_widths=col_w))
    story.append(Spacer(1, 14))

    story.append(P("Blended pre-money valuation across scenarios", STYLES["h3"]))
    blended_vals = [val(l, "blended_pre_money_valuation") or 0 for l in labels]
    story.append(bar_chart(labels, blended_vals, color=NAVY_LINE, width=CONTENT_W, height=140))
    return story


def _dcf_page(dcf: dict, years: list) -> list:
    story = [P("DCF method", STYLES["h2"])]
    hurdle = g(dcf, "hurdle_adjusted", {})
    story += _method_value_header("Enterprise value (risk-adjusted)", money(g(hurdle, "enterprise_value")))
    story.append(P(
        "Discounts five years of unlevered free cash flow plus a terminal value back to present, then "
        "applies the same stage risk multiplier used across the other methods.", STYLES["sub"],
    ))
    story.append(info_table([
        ("Tax rate", pct(g(dcf, "tax_rate"))),
        ("Discount rate (WACC)", pct(g(dcf, "discount_rate"))),
        ("Perpetual growth rate", pct(g(dcf, "perpetual_growth_rate"))),
        ("Hurdle rate / risk multiplier", pct(g(dcf, "hurdle_rate"))),
    ]))

    story.append(P("Unlevered free cash flow", STYLES["h3"]))
    year_labels = [y.get("year_label", "") for y in years]
    headers = [""] + year_labels

    def yr_row(label, field):
        return [label] + [money(y.get(field)) for y in years]

    rows = [
        yr_row("EBIT", "ebit"),
        yr_row("Less: taxes", "tax_amount"),
        yr_row("Plus: D&A", "da"),
        yr_row("Less: capex", "capex"),
        yr_row("Less: change in NWC", "change_in_working_capital"),
    ]
    fcf = g(dcf, "unlevered_fcf_by_year", [])
    total_row = ["Unlevered FCF"] + [money(v) for v in fcf]
    n = max(len(years), 1)
    col_w = [CONTENT_W * 0.28] + [CONTENT_W * 0.72 / n] * n
    story.append(data_table(headers, rows, total_row=total_row, col_widths=col_w))
    story.append(Spacer(1, 10))

    base = g(dcf, "base", {})
    ev_col_widths = [HALF_W * 0.65, HALF_W * 0.35]
    left = [P("Enterprise value (unadjusted)", STYLES["h3"]), info_table([
        ("PV of FCF", money(g(base, "pv_of_fcf"))),
        ("PV of terminal value", money(g(base, "pv_of_terminal_value"))),
    ], col_widths=ev_col_widths), info_table(
        [("Enterprise value", money(g(base, "enterprise_value")))], col_widths=ev_col_widths,
    )]
    right = [P("Enterprise value (risk-adjusted)", STYLES["h3"]), info_table([
        ("PV of FCF", money(g(hurdle, "pv_of_fcf"))),
        ("PV of terminal value", money(g(hurdle, "pv_of_terminal_value"))),
    ], col_widths=ev_col_widths), info_table(
        [("Enterprise value", money(g(hurdle, "enterprise_value")))], col_widths=ev_col_widths,
    )]
    story.append(side_by_side(left, right))
    return story


def _methodology_page() -> list:
    story = [P("Valuation approach: methods, data & disclaimer", STYLES["h2"])]
    story.append(P("Methodologies used", STYLES["h3"]))
    story.append(P(
        "This report blends four widely used valuation methods \u2014 Scorecard, Venture Capital, "
        "Comparables (Market Multiples), and Discounted Cash Flow \u2014 weighted by the development "
        "stage of the company, so early-stage qualitative methods carry more weight for younger "
        "companies and cash-flow-based methods carry more weight as a company matures.",
        STYLES["td_label"],
    ))
    story.append(P("Data sources", STYLES["h3"]))
    story.append(P(
        "Industry and regional benchmarks (cost structure ratios, EV/EBITDA multiples, risk premiums) "
        "are drawn from the dataset maintained by Prof. Aswath Damodaran at NYU Stern School of "
        "Business, updated annually.", STYLES["td_label"],
    ))
    story.append(Spacer(1, 12))
    disclaimer_box = Table([[P(
        "<b>Disclaimer:</b> This tool is not a licensed financial advisor, investment advisor, or "
        "legal entity authorized to provide financial, investment, or legal advice. This report is "
        "intended for informational purposes only and should not be construed as a solicitation or "
        "recommendation for any financial transaction. All valuations are based on information and "
        "assumptions believed to be accurate at the time of preparation, but no guarantee is made "
        "regarding completeness, accuracy, or future performance. Consult a qualified financial, "
        "legal, or investment professional before making decisions based on this analysis.",
        STYLES["disclaimer"], raw=True,
    )]], colWidths=[CONTENT_W])
    disclaimer_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DISCLAIMER_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 16), ("RIGHTPADDING", (0, 0), (-1, -1), 16),
    ]))
    story.append(disclaimer_box)
    return story


# ============================================================================
# Entry point
# ============================================================================


def build_pdf_bytes(
    payload: dict, output: dict, benchmark: Optional[dict] = None, scenarios: Optional[dict] = None,
) -> bytes:
    """
    payload: a ValuationInput.model_dump() dict.
    output:  a dataclasses.asdict(ValuationOutput) dict.
    benchmark: optional industry-benchmark dict (ve.industry_benchmarks()[industry]),
               used for the % of revenue benchmark rows. Safe to omit.
    scenarios: optional {"80%": <asdict ValuationOutput>, "90%": ..., ...} from
               ve.run_valuation_scenarios(), used for the Scenario & sensitivity
               page. Safe to omit (that page just notes the data wasn't available).
    """
    cp = g(payload, "company_profile", {}) or {}
    mkt = g(payload, "market_and_team_assessment", {}) or {}
    ops = g(payload, "operating_performance", {}) or {}
    fin = g(payload, "financial_assumptions", {}) or {}
    funding = g(payload, "funding", {}) or {}
    ownership = g(payload, "ownership", []) or []

    company_name = g(cp, "company_name") or "Untitled company"
    today_str = datetime.now().strftime("%B %d, %Y")
    logo_bytes = _decode_logo(g(cp, "logo_base64"))

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=MARGIN_SIDE, rightMargin=MARGIN_SIDE,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
        title=f"{company_name} - Valuation Report",
    )
    cover_frame = Frame(MARGIN_SIDE, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="cover")
    content_frame = Frame(MARGIN_SIDE, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="content")
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=_draw_nothing),
        PageTemplate(id="Content", frames=[content_frame], onPage=_make_page_decorator(company_name, today_str, logo_bytes)),
    ])

    story: list = []
    story += _cover_page(company_name, today_str, logo_bytes)
    story.append(NextPageTemplate("Content"))
    story.append(PageBreak())
    story += _company_summary_page(cp, mkt, ops)
    story.append(PageBreak())
    story += _projections_page(cp, fin, funding, ownership, output, benchmark or {})
    story.append(PageBreak())
    story += _valuation_summary_page(output)
    story.append(PageBreak())
    story += _scenario_sensitivity_page(scenarios or {})
    story.append(PageBreak())
    story += _scorecard_page(g(output, "scorecard", {}))
    story.append(PageBreak())
    story += _vc_page(g(output, "venture_capital", {}))
    story.append(PageBreak())
    story += _dcf_multiples_page(g(output, "dcf_multiples", {}))
    story.append(PageBreak())
    story += _dcf_page(g(output, "dcf", {}), g(output, "projections", {}).get("years", []))
    story.append(PageBreak())
    story += _methodology_page()

    doc.build(story)
    return buffer.getvalue()
