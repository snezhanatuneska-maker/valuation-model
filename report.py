"""
Branded PDF valuation report generator.

The only report renderer: the wizard's "Download PDF" and History both
call the API, which builds the PDF here with reportlab.

Sections (in order):
  1. Cover page
  2. Company summary (general info, business profile, market potential,
     team & product, latest operating performance)
  3. Projected financials & valuation inputs (projection table and charts,
     benchmarks actually used with their sources, capex, use of funds,
     ownership)
  4. Valuation summary (all four methods, blended value, pre/post-money)
  5. Checks on your inputs (warnings raised by the engine)
  6. Scenario & sensitivity
  7-10. One page per method, each starting with a plain-language
     "how this number was reached"
  11. Methods, data sources & disclaimer

`build_pdf_bytes()` is the single entry point app.py calls. It takes
plain dicts (the same shapes already persisted to SQLite: a
ValuationInput.model_dump() and a dataclasses.asdict(ValuationOutput)),
so the same function works for a brand-new, unsaved valuation and for
re-downloading a past saved one.
"""
from __future__ import annotations

import base64
import io
import re
from datetime import datetime
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
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
    sign = "-" if round(n) < 0 else ""  # never "-€0"
    return f"{sign}\u20ac{abs(n):,.0f}"


def pct(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "\u2014"
    return f"{n * 100:.1f}%"


def money_compact(n: Any) -> str:
    """Short amounts for narrow cells and chart labels: -€5k, €987k, €18.08M."""
    if n is None:
        return "—"
    v = float(n)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}\u20ac{a / 1_000_000:,.2f}M"
    if a >= 1_000:
        return f"{sign}\u20ac{a / 1_000:,.0f}k"
    return money(v)


def amount_format(values: list, limit: float = 100_000_000) -> Any:
    """Full amounts normally; compact ones (€18.08M) when the largest would not fit a table column."""
    largest = max((abs(v) for v in values if isinstance(v, (int, float))), default=0)
    return money_compact if largest >= limit else money


def money2(n: Any) -> str:
    """Money with cents, for small amounts like a price per share."""
    if n is None or n == "":
        return "\u2014"
    n = float(n)
    sign = "-" if n < 0 else ""
    return f"{sign}\u20ac{abs(n):,.2f}"


def pct2(n: Any) -> str:
    """Percent with 2 decimals, for rates (WACC, cost of equity, ...)."""
    if n is None or n == "":
        return "\u2014"
    return f"{float(n) * 100:.2f}%"


def multiple(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    return f"{float(n):.2f}\u00d7"


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
    text_table: bool = False,
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
            if text_table or (i == 0 and first_col_align_left):
                out.append(P(safe(c), label_style))
            else:
                out.append(P(safe(c), value_style))
        return out

    if headers:
        data.append([P(h, STYLES["th"] if i == 0 or text_table else STYLES["th_right"]) for i, h in enumerate(headers)])
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
    values = [v or 0 for v in values]
    hi = max(values + [0])
    lo = min(values + [0])
    span = (hi - lo) * 1.18 or 1
    plot_h = height - pad_bottom - pad_top
    base_y = pad_bottom + plot_h * (-lo / span) if lo < 0 else pad_bottom
    n = max(len(categories), 1)
    gap = (width - pad_side * 2) / n
    bar_w = min(gap * 0.55, 40)
    d.add(Line(pad_side, base_y, width - pad_side, base_y, strokeColor=colors.HexColor("#d5dbe3")))
    for i, (cat, v) in enumerate(zip(categories, values)):
        h = v / span * plot_h
        x = pad_side + i * gap + (gap - bar_w) / 2
        y0 = base_y if h >= 0 else base_y + h
        d.add(Rect(x, y0, bar_w, abs(h), fillColor=color if v >= 0 else colors.HexColor("#a3623f"), strokeColor=None))
        label = money_compact(v)
        label_y = base_y + h + 4 if h >= 0 else base_y + h - 9
        d.add(String(x + bar_w / 2, label_y, label, fontName="Helvetica-Bold", fontSize=6.5,
                      fillColor=MUTED, textAnchor="middle"))
        d.add(String(x + bar_w / 2, 6, str(cat), fontName="Helvetica", fontSize=6.5,
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
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def side_by_side(left, right, gap=16) -> Table:
    w = (CONTENT_W - gap) / 2
    t = Table([[left, right]], colWidths=[w, w])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


# ============================================================================
# Labels (mirror index.html's SCORECARD_LABELS / CRITERIA_LABELS / METHOD_LABELS)
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
    "competitive_environment": "Competitive environment",
    "strength_and_protection_of_product": "Strength & protection of product",
    "strategic_relationships_with_partners": "Strategic relationships with partners",
    "funding_required": "Funding required",
}
METHOD_LABELS = {
    "scorecard": "Scorecard",
    "venture_capital": "Venture Capital",
    "comparables": "Comparables (EV/EBITDA multiple)",
    "dcf": "Discounted cash flow (DCF)",
}
METHOD_CHART_LABELS = {"scorecard": "Scorecard", "venture_capital": "VC", "comparables": "Comparables", "dcf": "DCF"}
SOURCE_NOTES = {
    "region": "",
    "industry_global": "global figure*",
    "cross_industry": "all-industry median*",
    "user_override": "your input",
}
REGION_SHORT = {
    "Europe (EU, UK, Switzerland & Scandinavia)": "Europe",
    "Emerging Markets (Asia, Latin America, Eastern Europe, Mid East and Africa)": "Emerging Markets",
}


def short_region(region: Optional[str]) -> str:
    return REGION_SHORT.get(region or "", region or "—")


def format_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %B %Y").lstrip("0")
    except ValueError:
        return iso


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


BRAND = "Valuation Model"
AUTHOR = "Snezhana Tuneska"


def _make_page_decorator(company_name: str, date_str: str, logo_bytes: Optional[bytes]):
    def _draw(canvas, doc):
        canvas.saveState()
        header_y = PAGE_H - MARGIN_TOP + 9 * mm
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(MUTED_LIGHT)
        # Shorten a long name with "…" so it never runs into the label or logo on the right.
        name, max_w = company_name.upper(), PAGE_W - 2 * MARGIN_SIDE - 45 * mm
        if stringWidth(name, "Helvetica-Bold", 8) > max_w:
            while name and stringWidth(name + "…", "Helvetica-Bold", 8) > max_w:
                name = name[:-1]
            name = name.rstrip() + "…"
        canvas.drawString(MARGIN_SIDE, header_y, name)
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
            f"{BRAND} by {AUTHOR} · Valuation as of {date_str} · Page {doc.page} · "
            "Informational estimate, not a certified appraisal",
        )
        canvas.restoreState()

    return _draw


def _draw_nothing(canvas, doc):
    pass


def _how(text: str) -> list:
    """A shaded 'How this number was reached' box."""
    box = Table([[P(f"<b>How this number was reached.</b> {esc(text)}", STYLES["td_label"], raw=True)]],
                colWidths=[CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DISCLAIMER_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    return [box, Spacer(1, 10)]


def _not_meaningful(reason: Optional[str]) -> list:
    if not reason:
        return []
    return [P(f"<b>Not used in the blend:</b> {esc(reason)}", STYLES["td_label"], raw=True), Spacer(1, 8)]


# ============================================================================
# Page builders - each returns a list of flowables
# ============================================================================


METHOD_SHORT = {"scorecard": "Scorecard", "venture_capital": "Venture Capital", "comparables": "Comparables",
                "dcf": "DCF"}


def _at_a_glance(output: dict, scenarios: dict) -> list:
    """The result in four lines: the blend, post-money, how far apart the methods are, and what a 20%
    revenue miss does. A startup's value is uncertain; the range says how uncertain."""
    used = [METHOD_SHORT[k] for k, mv in (g(output, "method_values", {}) or {}).items()
            if mv.get("status") == "ok" and mv.get("weight_used")]
    rows = [("Pre-money valuation", money(g(output, "blended_pre_money_valuation")),
             "blend of " + (" and ".join([", ".join(used[:-1]), used[-1]]) if len(used) > 1 else used[0])
             if used else ""),
            ("Post-money valuation", money(g(output, "post_money_valuation")),
             f"after raising {money(g(output, 'capital_needed'))}")]
    rng = g(output, "method_range")
    if rng:
        rows.append(("Range across methods", f"{money(rng['low'])} ({METHOD_SHORT[rng['low_method']]}) to "
                     f"{money(rng['high'])} ({METHOD_SHORT[rng['high_method']]})", "lowest and highest method"))
    low, high = (scenarios or {}).get("80%"), (scenarios or {}).get("120%")
    if low and high and abs(high["blended_pre_money_valuation"] - low["blended_pre_money_valuation"]) >= 1:
        rows.append(("If Year-1 revenue is 20% lower or higher",
                     f"{money(low['blended_pre_money_valuation'])} to {money(high['blended_pre_money_valuation'])}",
                     "blended pre-money"))
    def value(b, c):
        note = f"<br/><font size=8 color='#8b98a8'>{esc(c)}</font>" if c else ""
        return P(f"<b>{esc(b)}</b>{note}", STYLES["td_value"], raw=True)
    table = Table([[P(a, STYLES["callout_label"]), value(b, c)] for a, b, c in rows],
                  colWidths=[CONTENT_W * 0.42, CONTENT_W * 0.58])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ROW_STRIPE), ("BACKGROUND", (0, 0), (-1, 0), TOTAL_BG),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story = [P("At a glance", STYLES["h3"]), table]
    if rng:
        story.append(P("The methods look at the company from different angles and rarely agree; the range shows how "
                       "far apart they are. The revenue line shows how much the result depends on the plan.",
                       STYLES["sub"]))
    return story


def _cover_page(company_name: str, date_str: str, data_date: str, logo_bytes: Optional[bytes],
                glance: Optional[list] = None) -> list:
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
        [P("Valuation date", STYLES["cover_meta_label"]), P(date_str, STYLES["cover_meta_value"])],
        [P("Market data", STYLES["cover_meta_label"]),
         P(f"Damodaran Online (NYU Stern), {data_date}", STYLES["cover_meta_value"])],
        [P("Report generated", STYLES["cover_meta_label"]),
         P(datetime.now().strftime("%d %B %Y").lstrip("0"), STYLES["cover_meta_value"])],
    ]
    meta = Table(meta_rows, colWidths=[35 * mm, CONTENT_W - 35 * mm])
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(Spacer(1, 8))
    story.append(meta)
    if glance:
        story.append(Spacer(1, 18))
        story += glance
    story.append(Spacer(1, 40 if not glance else 24))
    story.append(P(
        "This report is an automated, informational estimate blending four standard valuation "
        "methods. It is not a certified appraisal, and should not be relied on as financial, "
        "investment, or legal advice.",
        STYLES["cover_disclaimer"],
    ))
    story.append(Spacer(1, 6))
    story.append(P(f"Prepared with {BRAND} by {AUTHOR}.", STYLES["cover_disclaimer"]))
    return story


def _german_taxes(output: dict) -> Optional[dict]:
    """The German stepped tax schedule from the output, or None if a flat rate was used."""
    taxes = g(g(output, "projections", {}), "tax_schedule") or {}
    return taxes if taxes.get("basis") == "germany_schedule" else None


def _hebesatz_text(taxes: dict) -> str:
    who = "your municipality" if taxes.get("hebesatz_source") == "user" else "national average"
    return f"{(taxes.get('hebesatz') or 0) * 100:.0f}% ({who})"


def _company_summary_page(cp: dict, mkt: dict, ops: dict, output: dict) -> list:
    story = [P("Company summary", STYLES["h2"])]
    proj = g(output, "projections", {})
    tax_note = "your input" if g(cp, "dcf_tax_rate_override") is not None else "country statutory rate"
    german = _german_taxes(output)
    if german:
        rates = [y.get("tax_rate") for y in proj.get("years", [])]
        tax_text = (f"{pct(rates[0])} in Year 1 falling to {pct(rates[-1])} in Year 5 and "
                    f"{pct(german.get('long_run_rate'))} after (German schedule)")
    else:
        tax_text = f"{pct(g(proj, 'tax_rate'))} ({tax_note})"

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
        ("Business territory", short_region(g(cp, "business_territory_region"))),
        ("Business model", g(cp, "business_model")),
        ("Committed capital", money(g(cp, "committed_capital", 0))),
        ("Exit strategy", g(cp, "exit_strategy")),
        ("Planned time to exit", f"{g(cp, 'planned_time_to_exit_years')} "
                                 f"year{'' if g(cp, 'planned_time_to_exit_years') == 1 else 's'}"),
        ("Tax rate", tax_text),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6])

    story.append(side_by_side([P("General info", STYLES["h3"]), general],
                              [P("Business profile", STYLES["h3"]), business]))
    story.append(Spacer(1, 10))

    competitors = [c for c in (g(mkt, "key_competitor_1"), g(mkt, "key_competitor_2"), g(mkt, "key_competitor_3")) if c]
    # Questionnaire answers: only the ones the founder actually gave. Companies with revenue
    # may skip the questionnaire (the Scorecard only counts before revenue).
    answered = any(g(mkt, k) for k in (*MARKET_POTENTIAL_KEYS, *TEAM_PRODUCT_KEYS))
    answer = lambda k: g(mkt, k) or "Not answered"  # noqa: E731
    if answered:
        market_rows = [(SCORECARD_LABELS.get(k, k), answer(k)) for k in MARKET_POTENTIAL_KEYS]
        team_rows = [(SCORECARD_LABELS.get(k, k), answer(k)) for k in TEAM_PRODUCT_KEYS]
    else:
        market_rows = []
        team_rows = [("Questionnaire", "Not answered (only needed before revenue, for the Scorecard method)")]
    market_rows.append(("Key competitors", ", ".join(competitors) if competitors else None))
    market_table = info_table(market_rows, col_widths=[HALF_W * 0.4, HALF_W * 0.6])
    team_table = info_table(team_rows, col_widths=[HALF_W * 0.4, HALF_W * 0.6])
    rev = g(ops, "current_revenue_last_12_months") or 0
    margin = (g(ops, "current_ebitda") or 0) / rev if rev else None
    perf_table = info_table([
        ("Revenue, last 12 months", money(rev)),
        ("EBITDA, last 12 months", money(g(ops, "current_ebitda"))),
        ("EBITDA margin", pct(margin)),
        ("Cash available", money(g(ops, "cash_available"))),
        ("PP&E (current value)", money(g(ops, "current_ppe_value"))),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6])

    story.append(side_by_side(
        [P("Market potential", STYLES["h3"]), market_table, P("Latest operating performance", STYLES["h3"]), perf_table],
        [P("Team & product", STYLES["h3"]), team_table],
    ))
    return story


def _projections_page(cp: dict, fin: dict, funding: dict, ownership: list, output: dict, benchmark: dict) -> list:
    story = [P("Projected financials & valuation inputs", STYLES["h2"])]
    proj = g(output, "projections", {})
    years = proj.get("years", [])
    labels = [y.get("year_label", "") for y in years]
    target_name = "your target margin" if proj.get("target_margin_source") == "user_override" else "the industry margin"
    if proj.get("starting_margin_source") == "company":
        margin_text = (f"The EBITDA margin starts from your last-12-month margin ({pct(proj.get('starting_ebitda_margin'))}) "
                       f"and moves in equal steps to {target_name} ({pct(proj.get('target_ebitda_margin'))}) by Year 5.")
    else:
        margin_text = (f"With no revenue history, the projection uses {target_name} "
                       f"({pct(proj.get('target_ebitda_margin'))}) as the EBITDA margin from Year 1.")
    story.append(P(
        f"Year 1 is the 12 months after the valuation date ({format_date(proj.get('valuation_date'))}). {margin_text}",
        STYLES["sub"]))

    headers = [""] + [f"{y.get('year_label')} ({y.get('period_label', '').replace('to ', '')})" for y in years]
    amt = amount_format([y.get(k) for y in years for k in ("revenue", "ebitda", "capex", "unlevered_fcf")])
    rows = [
        ["Revenue"] + [amt(y.get("revenue")) for y in years],
        ["EBITDA margin"] + [pct(y.get("ebitda_margin")) for y in years],
        ["EBITDA"] + [amt(y.get("ebitda")) for y in years],
        ["Capital expenditure"] + [amt(y.get("capex")) for y in years],
        ["Free cash flow"] + [amt(y.get("unlevered_fcf")) for y in years],
    ]
    n = max(len(years), 1)
    story.append(data_table(headers, rows, col_widths=[CONTENT_W * 0.25] + [CONTENT_W * 0.75 / n] * n))
    story.append(Spacer(1, 8))
    story.append(side_by_side(
        [P("Revenue by year", STYLES["h3"]),
         bar_chart(labels, [y.get("revenue", 0) for y in years], color=NAVY_LINE, height=95)],
        [P("EBITDA by year", STYLES["h3"]),
         bar_chart(labels, [y.get("ebitda", 0) for y in years], color=BLUE_MID, height=95)],
    ))

    region = g(cp, "business_territory_region")
    used = g(output, "benchmarks_used", {})
    wacc = g(output, "wacc", {})

    def bm(metric, fmt=pct):
        b = used.get(metric)
        if not b:
            return "—"
        note = SOURCE_NOTES.get(b.get("source"), "")
        return f"{fmt(b.get('value'))}" + (f" ({note})" if note else "")

    def info_only(metric):
        table = g(benchmark, metric) or {}
        v = table.get(region)
        return pct(v) if v is not None else "—"

    story.append(P(f"Benchmarks used — {safe(g(cp, 'industry'))} / {short_region(region)}", STYLES["h3"]))
    rows = [
        (("Industry EBITDA margin (for reference)", f"{info_only('ebitda_margin')} (not used: your own Year-5 target is "
          f"{pct(proj.get('target_ebitda_margin'))})")
         if proj.get("target_margin_source") == "user_override"
         else ("Industry EBITDA margin (Year-5 target)", bm("ebitda_margin"))),
        ("  of which: COGS / SG&A / R&D (% of revenue, for reference)",
         f"{info_only('cogs_pct_revenue')} / {info_only('sga_pct_revenue')} / {info_only('rd_pct_revenue')}"),
        ("D&A (% of revenue)", bm("da_pct_revenue")),
        ("Accounts receivable / inventory / payable (% of revenue)",
         f"{bm('acc_receivable_pct_revenue')} / {bm('inventory_pct_revenue')} / {bm('acc_payable_pct_revenue')}"),
        ("EV/EBITDA multiple (trailing, profitable public companies)", bm("ev_ebitda_multiple", multiple)),
        ("Beta", bm("beta", lambda v: f"{float(v):.2f}")),
        ("Cost of equity / WACC", f"{pct2(g(wacc, 'cost_of_equity'))} / {pct2(g(wacc, 'wacc'))}"),
    ]
    story.append(info_table(rows, col_widths=[CONTENT_W * 0.62, CONTENT_W * 0.38]))
    if any(b.get("source") in ("industry_global", "cross_industry") for b in used.values()):
        story.append(P("* Damodaran has no usable figure for this region, so the industry's global figure "
                       "(or the median across all industries) is used.", STYLES["sub"]))

    funds_entries = [(k, v) for k, v in (g(funding, "use_of_funds") or {}).items() if v]
    ownership_entries = [(o.get("name"), o.get("ownership_pct")) for o in (ownership or []) if o.get("ownership_pct", 0) > 0]
    allocated = sum(v for _, v in ownership_entries)
    if ownership_entries and allocated < 0.995:
        ownership_entries.append(("Not specified", 1 - allocated))

    funds_block = [P("Use of funds", STYLES["h3"])]
    if funds_entries:
        funds_block += [pie_chart([v for _, v in funds_entries], size=64), Spacer(1, 4),
                        legend_table(funds_entries, fmt=money)]
    else:
        funds_block.append(P("—", STYLES["td_label"]))
    own_block = [P("Ownership structure (before this round)", STYLES["h3"])]
    if ownership_entries:
        own_block += [pie_chart([v for _, v in ownership_entries], size=64), Spacer(1, 4),
                      legend_table(ownership_entries, fmt=pct)]
    else:
        own_block.append(P("—", STYLES["td_label"]))
    story.append(side_by_side(funds_block, own_block))
    return story


def zero_value_reason(output: dict) -> str:
    """Why the pre-money value is zero (the same wording as on the results page)."""
    debt = g(g(output, "equity_bridge", {}), "debt") or 0
    if debt > 0:
        return (f"The value is €0 because the company's debt ({money(debt)}) is larger than the value the methods "
                "find for the business, so the shares are worth about nothing before the new money comes in.")
    return "The value rounds to €0: the methods used leave essentially no value for the shares before the new money."


def _valuation_summary_page(output: dict) -> list:
    story = [P("Valuation", STYLES["h2"])]
    story.append(P(
        "Each method estimates the company's equity value before the new investment (pre-money). "
        "They are blended using weights for the company's stage; a method that can't give a meaningful "
        "value for this company is left out and the other weights are scaled up.", STYLES["sub"]))

    method_values = g(output, "method_values", {})
    rows, cats, vals = [], [], []
    for key, mv in method_values.items():
        status = mv.get("status")
        if status == "ok":
            value, weighted = money(mv.get("pre_money_value")), money(mv.get("weighted_value"))
            cats.append(METHOD_CHART_LABELS.get(key, key))
            vals.append(mv.get("pre_money_value") or 0)
        elif status == "not_used":
            value, weighted = "not used at this stage", "—"
        else:
            value, weighted = "not meaningful", "—"
        rows.append([METHOD_LABELS.get(key, key), pct(mv.get("weight")), pct(mv.get("weight_used")), value, weighted])
    cats.append("Blended")
    vals.append(g(output, "blended_pre_money_valuation") or 0)

    total_row = ["Blended pre-money valuation", "", "", "", money(g(output, "blended_pre_money_valuation"))]
    story.append(data_table(
        ["Method", "Stage weight", "Weight used", "Pre-money value", "Weighted value"], rows,
        total_row=total_row, total_label_span=4,
        col_widths=[CONTENT_W * 0.32, CONTENT_W * 0.13, CONTENT_W * 0.13, CONTENT_W * 0.22, CONTENT_W * 0.20],
    ))
    for key, mv in method_values.items():
        if mv.get("status") == "not_meaningful":
            story.append(P(f"{METHOD_LABELS.get(key, key)}: {mv.get('note')}", STYLES["sub"]))
        elif key == "scorecard" and mv.get("status") == "not_used":
            story.append(P(f"Scorecard: not used at this stage. {SCORECARD_NOT_USED}", STYLES["sub"]))
    story.append(Spacer(1, 12))
    story.append(bar_chart(cats, vals, color=NAVY_LINE, width=CONTENT_W, height=130))
    story.append(Spacer(1, 12))

    def callout(label, value, emphasize=False):
        t = Table([[P(label, STYLES["callout_label"]), P(value, STYLES["callout_value"])]],
                  colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TOTAL_BG if emphasize else ROW_STRIPE),
            ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    story.append(callout("Pre-money valuation", money(g(output, "blended_pre_money_valuation"))))
    if (g(output, "blended_pre_money_valuation") or 0) < 0.5:
        story.append(Spacer(1, 4))
        story.append(P(zero_value_reason(output), STYLES["sub"]))
    story.append(Spacer(1, 6))
    story.append(callout("Capital being raised", money(g(output, "capital_needed"))))
    story.append(Spacer(1, 6))
    story.append(callout("Post-money valuation", money(g(output, "post_money_valuation")), emphasize=True))
    return story


def _warnings_page(output: dict) -> list:
    warnings = g(output, "warnings", []) or []
    has_checks = any(w.get("severity") == "warning" for w in warnings)
    story = [P("Checks on your inputs" if has_checks else "Notes on how the data was used", STYLES["h2"])]
    if not warnings:
        story.append(P("No issues were found in the inputs.", STYLES["td_label"]))
        return story
    story.append(P("The calculation ran, but the points marked as warnings deserve a second look before the numbers "
                   "are shared; notes explain how the data was used." if has_checks else
                   "Nothing here needs fixing: these notes explain how your figures and the market data were "
                   "used.", STYLES["sub"]))
    rows = [["Warning" if w.get("severity") == "warning" else "Note", w.get("message")] for w in warnings]
    t = data_table(["Type", "Detail"], rows, col_widths=[CONTENT_W * 0.14, CONTENT_W * 0.86], text_table=True)
    story.append(t)
    return story


SCENARIO_FALLS_NOTE = (
    "Here a higher Year-1 revenue gives a lower value: in your plan, extra revenue costs more cash than it "
    "brings in (for example losses at the planned margin, or the working capital it ties up), so the "
    "cash-flow based values go down.")


def _scenario_sensitivity_page(scenarios: dict) -> list:
    story = [P("Scenario & sensitivity", STYLES["h2"])]
    story.append(P(
        "How the valuation shifts if Year-1 revenue comes in above or below plan (later years scale with "
        "it). Scorecard and Comparables don't move: they use your questionnaire answers and last-12-month "
        "EBITDA, not projected revenue.", STYLES["sub"],
    ))
    if not scenarios:
        story.append(P("Scenario data wasn't available when this report was generated.", STYLES["td_label"]))
        return story

    labels = list(scenarios.keys())
    n = len(labels)
    values = [scenarios[l].get("blended_pre_money_valuation") or 0 for l in labels]
    if max(values) - min(values) < 1:
        story.append(P(
            f"The value is the same in every scenario ({money(values[0])}): only methods that don't use projected "
            "revenue give a value for this company, so a higher or lower Year-1 revenue doesn't change it.",
            STYLES["td_label"]))
        return story

    def val(label, key):
        mv = scenarios[label].get("method_values", {}).get(key, {})
        if mv.get("status") == "ok":
            return money_compact(mv.get("pre_money_value"))
        return "not used" if mv.get("status") == "not_used" else "n/m"

    rows = [[METHOD_CHART_LABELS[k]] + [val(l, k) for l in labels]
            for k in ("scorecard", "venture_capital", "comparables", "dcf")]
    total_row = ["Blended pre-money"] + [money_compact(scenarios[l].get("blended_pre_money_valuation")) for l in labels]
    col_w = [CONTENT_W * 0.28] + [CONTENT_W * 0.72 / n] * n
    story.append(data_table(["Method"] + labels, rows, total_row=total_row, col_widths=col_w))
    story.append(P("The weights stay as in the main result; a method that gives no positive value in a scenario "
                   "counts as €0." + (" n/m = left out of the blend, as in the main result."
                                      if any(scenarios[l].get("method_values", {}).get(k, {}).get("status") == "not_meaningful"
                                             for l in labels for k in ("scorecard", "venture_capital", "comparables", "dcf"))
                                      else "")
                   + (" " + SCENARIO_FALLS_NOTE
                      if any(b < a - 1 for a, b in zip(values, values[1:])) else ""), STYLES["sub"]))
    story.append(Spacer(1, 10))
    story.append(P("Blended pre-money valuation across scenarios", STYLES["h3"]))
    story.append(bar_chart(labels, [scenarios[l].get("blended_pre_money_valuation") or 0 for l in labels],
                           color=NAVY_LINE, width=CONTENT_W, height=115))
    return story


SCORECARD_NOT_USED = (
    "The Scorecard compares a pre-revenue company with the typical pre-revenue company in its region, so it "
    "applies only at the Idea and Development stages; companies with revenue are valued on their numbers by "
    "the other methods.")


def _scorecard_page(sc: dict, cp: dict, mv: Optional[dict] = None) -> list:
    story = [P("Scorecard method", STYLES["h2"])]
    if mv and mv.get("status") == "not_used":
        story.append(P(f"Not used for this company. {SCORECARD_NOT_USED}", STYLES["td_label"]))
        return story
    story += _method_value_header("Pre-money valuation", money(g(sc, "pre_money_valuation")))
    bench = g(sc, "benchmark_pre_money_valuation")
    factor = g(sc, "total_factor")
    source = g(sc, "benchmark_source")
    basis = g(sc, "benchmark_basis") or ""
    basis = basis[:1].lower() + basis[1:] if basis.startswith("Average") else basis
    bench_src = ("your own benchmark" if source == "user_override"
                 else basis if source == "country_table"
                 else f"{basis}, converted to euros")
    region = g(cp, "business_territory_region")
    where = ("like yours" if source == "user_override"
             else f"in {g(cp, 'country')}" if source == "country_table"
             else "in the US" if region == "US"
             else "anywhere (all-region figure)" if region == "Global"
             else f"in {short_region(region)}")
    story += _how(
        f"A typical pre-revenue company {where} "
        f"is valued at about {money(bench)} before investment ({bench_src}). Your answers score this company at "
        f"{pct(factor)} of that typical company overall, so {money(bench)} × {pct(factor)} = "
        f"{money(g(sc, 'pre_money_valuation'))}.")
    criteria = g(sc, "criteria", {})
    rows = [[CRITERIA_LABELS.get(k, k), pct(c.get("weight")), pct(c.get("score")), money(c.get("amount_assigned"))]
            for k, c in criteria.items()]
    total_row = ["Pre-money valuation", "", pct(factor), money(g(sc, "pre_money_valuation"))]
    story.append(data_table(
        ["Criterion", "Weight", "Score vs typical", "Benchmark × weight × score"], rows,
        total_row=total_row, col_widths=[CONTENT_W * 0.38, CONTENT_W * 0.14, CONTENT_W * 0.2, CONTENT_W * 0.28],
    ))
    story.append(P("Method: Bill Payne's Scorecard (Ohio TechAngels). A score above 100% means stronger than "
                   "the typical company on that criterion.", STYLES["sub"]))
    if g(sc, "benchmark_to_be_sourced"):
        story.append(P(f"Note: a {safe(g(cp, 'country'))}-specific benchmark by stage is still to be sourced; "
                       "the Europe figure is used as a placeholder until it is.", STYLES["sub"]))
    return story


def _method_value_header(label: str, value: str) -> list:
    return [P(label, STYLES["metric_label"]), P(value, STYLES["metric_value"])]


def _benchmark_source_note(source: str, label: str = "EV/EBITDA multiple") -> Optional[str]:
    if source == "industry_global":
        return f"Note: no usable regional {label} — this industry's global figure is used."
    if source == "cross_industry":
        return f"Note: no usable {label} for this industry — the median across all industries is used."
    return None


def _vc_page(vc: dict, output: Optional[dict] = None) -> list:
    story = [P("Venture Capital method", STYLES["h2"])]
    story += _method_value_header("Pre-money valuation", money(g(vc, "pre_money_valuation")))
    T = g(vc, "time_to_exit")
    r = g(vc, "target_return")
    story += _not_meaningful(g(vc, "not_meaningful_reason"))
    if g(vc, "post_money_valuation") is not None:
        story += _how(
            f"Projected EBITDA in the exit year ({g(vc, 'exit_year_label')}) of {money(g(vc, 'exit_year_ebitda'))} "
            f"× the industry EV/EBITDA multiple of {multiple(g(vc, 'ev_ebitda_multiple'))} gives an exit value of "
            f"{money(g(vc, 'exit_value'))}. An investor who needs a {pct(r)} annual return values that today at "
            f"{money(g(vc, 'exit_value'))} ÷ (1 + {pct(r)})^{T} = {money(g(vc, 'post_money_valuation'))} "
            + (f"after the investment. Minus the {money(g(vc, 'investment_amount'))} being raised = "
               f"{money(g(vc, 'pre_money_valuation'))} before it."
               if g(vc, "pre_money_valuation") is not None else
               f"after the investment. That is less than the {money(g(vc, 'investment_amount'))} being raised, "
               "so this method leaves no positive value before the investment and is left out of the blend."))
    story.append(P("Exit value", STYLES["h3"]))
    story.append(info_table([
        (f"Exit-year ({g(vc, 'exit_year_label')}) revenue", money(g(vc, "exit_year_revenue"))),
        (f"Exit-year ({g(vc, 'exit_year_label')}) EBITDA", money(g(vc, "exit_year_ebitda"))),
        ("EV/EBITDA multiple", multiple(g(vc, "ev_ebitda_multiple"))),
        # A non-positive exit value has no meaning; show a dash rather than a negative amount.
        ("Exit value (enterprise value)", money(g(vc, "exit_value")) if (g(vc, "exit_value") or 0) > 0 else "—"),
        ("Less: debt (assumed still outstanding at exit)", money(-(g(vc, "debt") or 0))),
        ("Exit value for shareholders",
         money(g(vc, "exit_equity_value")) if (g(vc, "exit_equity_value") or 0) > 0 else "—"),
    ]))
    note = _benchmark_source_note(g(vc, "ev_ebitda_multiple_source"))
    if note:
        story.append(P(note, STYLES["sub"]))
    story.append(P("Value today and the new investor's stake", STYLES["h3"]))
    story.append(info_table([
        ("Years to exit", T),
        ("Investor's target annual return (for this stage)", pct(r)),
        ("Post-money valuation", money(g(vc, "post_money_valuation"))),
        ("Investment", money(g(vc, "investment_amount"))),
        ("Pre-money valuation", money(g(vc, "pre_money_valuation"))),
        ("Investor ownership after the round (this method alone)", pct(g(vc, "ownership_fraction_investors"))),
        ("Existing shareholders after the round (this method alone)", pct(g(vc, "ownership_fraction_entrepreneurs"))),
        ("Existing shares (count)", f"{g(vc, 'number_of_existing_shares', 0):,.0f}"),
        ("New shares to issue (count)",
         f"{round(g(vc, 'number_of_new_shares')):,}" if g(vc, "number_of_new_shares") is not None else "—"),
        ("Price per new share", money2(g(vc, "price_per_share"))),
    ]))
    post = g(output or {}, "post_money_valuation")
    capital = g(output or {}, "capital_needed")
    if post and capital and g(vc, "ownership_fraction_investors") is not None:
        story.append(P(
            f"These shares and stakes are what this method alone implies. At the blended valuation used in the rest "
            f"of this report (post-money {money(post)}), the {money(capital)} being raised buys "
            f"{pct(capital / post)} of the company.", STYLES["sub"]))
    return story


def _comparables_page(cm: dict, cp: dict) -> list:
    story = [P("Comparables method (EV/EBITDA multiple)", STYLES["h2"])]
    story += _method_value_header("Pre-money valuation", money(g(cm, "pre_money_valuation")))
    story += _not_meaningful(g(cm, "not_meaningful_reason"))
    if g(cm, "pre_money_valuation") is not None:
        story += _how(
            f"Profitable public {safe(g(cp, 'industry'))} companies are valued at about "
            f"{multiple(g(cm, 'ev_ebitda_multiple'))} their last-12-month EBITDA. Your last-12-month EBITDA of "
            f"{money(g(cm, 'trailing_ebitda'))} × {multiple(g(cm, 'ev_ebitda_multiple'))} = "
            f"{money(g(cm, 'public_company_ev'))}. A private company of this stage is harder to sell than a listed "
            f"one, so a {pct(g(cm, 'private_company_discount'))} discount gives an enterprise value of "
            f"{money(g(cm, 'enterprise_value'))}; minus debt and plus cash gives the equity value.")
    used = g(cm, "pre_money_valuation") is not None
    # When the method doesn't apply (e.g. negative EBITDA), the inputs are shown but not
    # the meaningless results of multiplying them.
    shown = lambda v: v if used else "—"  # noqa: E731
    story.append(info_table([
        ("EBITDA, last 12 months", money(g(cm, "trailing_ebitda"))),
        ("EV/EBITDA multiple (trailing)", multiple(g(cm, "ev_ebitda_multiple"))),
        ("Value at the public-company multiple", shown(money(g(cm, "public_company_ev")))),
        ("Private-company discount (for this stage)", f"−{pct(g(cm, 'private_company_discount'))}"),
        ("Enterprise value", shown(money(g(cm, "enterprise_value")))),
        ("Less: debt", money(-(g(cm, "debt") or 0))),
        ("Plus: cash", money(g(cm, "cash"))),
        ("Equity value (pre-money)", money(g(cm, "equity_value"))),
    ]))
    note = _benchmark_source_note(g(cm, "ev_ebitda_multiple_source"))
    if note:
        story.append(P(note, STYLES["sub"]))
    return story


def _dcf_page(dcf: dict, wacc: dict, years: list, german: Optional[dict] = None) -> list:
    story = [P("Discounted cash flow (DCF) method", STYLES["h2"])]
    story += _method_value_header("Pre-money valuation", money(g(dcf, "pre_money_valuation")))
    story += _not_meaningful(g(dcf, "not_meaningful_reason"))
    r = g(dcf, "discount_rate")
    if g(dcf, "pre_money_valuation") is not None:
        story += _how(
            f"Five years of projected free cash flow plus a terminal value for the years after, discounted at the "
            f"company's cost of capital ({pct2(r)}), are worth {money(g(dcf, 'enterprise_value'))} if the business "
            f"keeps going. About {pct(g(dcf, 'survival_probability'))} of companies at this stage survive, so the "
            f"expected value is {money(g(dcf, 'risk_adjusted_enterprise_value'))}; minus debt and plus cash gives "
            f"{money(g(dcf, 'pre_money_valuation'))}.")

    headers = [""] + [y.get("year_label", "") for y in years]
    amt = amount_format([y.get(k) for y in years for k in ("ebit", "tax_on_ebit", "da", "capex", "change_in_working_capital",
                                                          "unlevered_fcf")])
    rows = [
        ["EBIT"] + [amt(y.get("ebit")) for y in years],
        *([["Tax rate (German schedule)"] + [pct(y.get("tax_rate")) for y in years]] if german else []),
        ["Tax on EBIT"] + [amt(-y.get("tax_on_ebit", 0)) for y in years],
        ["Add back D&A"] + [amt(y.get("da")) for y in years],
        ["Capital expenditure"] + [amt(-y.get("capex", 0)) for y in years],
        ["Change in working capital"] + [amt(-y.get("change_in_working_capital", 0)) for y in years],
    ]
    total_row = ["Free cash flow"] + [amt(v) for v in g(dcf, "unlevered_fcf_by_year", [])]
    n = max(len(years), 1)
    story.append(P("Unlevered free cash flow (negative numbers reduce cash)", STYLES["h3"]))
    story.append(data_table(headers, rows, total_row=total_row,
                            col_widths=[CONTENT_W * 0.28] + [CONTENT_W * 0.72 / n] * n))
    story.append(Spacer(1, 8))

    tv_share = g(dcf, "terminal_value_share")
    # When the method isn't used (e.g. negative enterprise value), the values derived from the
    # cash flows have no meaning: show dashes rather than negative amounts.
    used = g(dcf, "pre_money_valuation") is not None
    # Half-width table: compact amounts from a billion euros up, so nothing wraps.
    big = amount_format([g(dcf, k) for k in ("pv_of_fcf", "pv_of_terminal_value", "enterprise_value",
                                             "risk_adjusted_enterprise_value", "debt", "cash", "equity_value")],
                        limit=1_000_000_000)
    shown = lambda v: big(v) if used else "—"  # noqa: E731
    value_rows = info_table([
        ("PV of 5 years of free cash flow", shown(g(dcf, "pv_of_fcf"))),
        ("PV of terminal value", shown(g(dcf, "pv_of_terminal_value"))),
        ("Enterprise value (if the business survives)", shown(g(dcf, "enterprise_value"))),
        ("Terminal value share of that value",
         "—" if not used else
         "over 100% (Years 1-5 burn cash)" if tv_share is not None and tv_share > 1 else pct(tv_share)),
        ("Probability of survival (for this stage)", pct(g(dcf, "survival_probability"))),
        ("Risk-adjusted enterprise value", shown(g(dcf, "risk_adjusted_enterprise_value"))),
        ("Less: debt", big(-(g(dcf, "debt") or 0))),
        ("Plus: cash", big(g(dcf, "cash"))),
        ("Equity value (pre-money)", big(g(dcf, "equity_value"))),
    ], col_widths=[HALF_W * 0.68, HALF_W * 0.32])
    rate_rows = info_table([
        ("Risk-free rate (10-year Bund)" if german else "Risk-free rate", pct2(g(wacc, "risk_free_rate"))),
        ("Beta (industry)", f"{g(wacc, 'beta', 0):.2f}"),
        ("Country equity risk premium", pct2(g(wacc, "country_equity_risk_premium"))),
        ("Cost of equity", pct2(g(wacc, "cost_of_equity"))),
        ("Cost of debt after tax", pct2(g(wacc, "after_tax_cost_of_debt"))),
        ("Equity / debt weights", f"{pct(g(wacc, 'equity_pct_capital'))} / {pct(g(wacc, 'debt_pct_capital'))}"),
        ("Discount rate (WACC)", pct2(r)),
        ("Long-run growth after Year 5", pct2(g(dcf, "perpetual_growth_rate"))),
        ("Tax rate after Year 5 (also in WACC)" if german else "Tax rate", pct2(g(dcf, "tax_rate"))),
    ], col_widths=[HALF_W * 0.62, HALF_W * 0.38])
    story.append(side_by_side([P("From cash flows to equity value", STYLES["h3"]), value_rows],
                              [P("Discount rate", STYLES["h3"]), rate_rows]))
    if german:
        story.append(P(f"The terminal value starts from Year-5 free cash flow taxed at the long-run rate of "
                       f"{pct(g(dcf, 'tax_rate'))} ({money(g(dcf, 'terminal_fcf'))}), because German corporate tax "
                       "keeps falling after Year 5 under the enacted schedule.", STYLES["sub"]))
    if g(dcf, "terminal_value_floor_applied"):
        story.append(P("Note: the discount rate is close to the long-run growth rate, so the terminal value uses "
                       "a minimum 2-point gap between them.", STYLES["sub"]))
    return story


def _country_inputs_section(country: str, specific: dict, output: dict) -> list:
    """Germany: the Bund rate, the tax schedule year by year and the stage benchmarks, with sources."""
    story = [P(f"{esc(country)}-specific inputs", STYLES["h3"])]
    wacc = g(output, "wacc", {})
    rf = specific.get("risk_free_rate")
    if rf:
        story.append(P(f"<b>Risk-free rate:</b> {pct2(g(wacc, 'risk_free_rate'))} (as of "
                       f"{esc(format_date(rf.get('as_of')))}). {esc(rf.get('source'))}", STYLES["td_label"], raw=True))
        story.append(P(f"<b>Country risk:</b> {esc(country)} is rated Aaa, so its country risk premium is 0 and "
                       f"the equity risk premium is the mature-market premium of "
                       f"{pct2(g(wacc, 'country_equity_risk_premium'))}.", STYLES["td_label"], raw=True))
        story.append(Spacer(1, 3))

    taxes = _german_taxes(output)
    tax = specific.get("tax")
    if taxes and tax:
        rows = [[str(y.get("year")), pct(y.get("corporate_tax")), pct2(y.get("solidarity_surcharge")),
                 pct2(y.get("trade_tax")), pct2(y.get("combined"))] for y in taxes.get("calendar_years", [])]
        story.append(P(f"Tax by calendar year, with a trade-tax Hebesatz of {_hebesatz_text(taxes)}. The last "
                       "year's rate also applies after Year 5 (terminal value) and in the WACC.", STYLES["td_label"]))
        story.append(data_table(["Year", "Corporate tax", "Solidarity surcharge", "Trade tax", "Combined"], rows,
                                col_widths=[CONTENT_W * 0.12, CONTENT_W * 0.22, CONTENT_W * 0.24,
                                            CONTENT_W * 0.2, CONTENT_W * 0.22]))
        years = g(g(output, "projections", {}), "years", [])
        story.append(P("Projection years span two calendar years, so each uses the two years' rates weighted by "
                       "days: " + ", ".join(f"{y.get('year_label')} {pct2(y.get('tax_rate'))}" for y in years)
                       + ".", STYLES["sub"]))
        for key in ("corporate_tax_source", "solidarity_surcharge_source", "trade_tax_base_rate_source",
                    "average_hebesatz_source", "simplification"):
            if tax.get(key):
                story.append(P(esc(tax[key]), STYLES["sub"]))
        story.append(Spacer(1, 3))
    elif tax:
        story.append(P("Tax: your own flat rate was used instead of the German schedule.", STYLES["td_label"]))

    sb = specific.get("stage_benchmarks")
    if sb:
        flag = (" <b>The German figures are still to be sourced; these are placeholders.</b>"
                if any(r.get("to_be_sourced") for r in sb.get("stages", {}).values()) else "")
        story.append(P(f"<b>Scorecard benchmark (Idea and Development stages):</b> {esc(sb.get('source'))}{flag}",
                       STYLES["td_label"], raw=True))
    return story


# Source sentences that concern one particular country or industry: shown only to that company.
_SPECIFIC_SOURCE_SENTENCES = {"Russia": "country", "Retail (Online)": "industry"}


def _relevant_sentences(text: str, country: Optional[str], industry: Optional[str]) -> str:
    keep = []
    for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z'])", text):
        about = next((name for name in _SPECIFIC_SOURCE_SENTENCES if name in sentence), None)
        if about is None or about in (country, industry):
            keep.append(sentence)
    return " ".join(keep)


def _methodology_page(sources: dict, stage_params: Optional[dict], stage: Optional[str],
                      country: Optional[str] = None, country_inputs: Optional[dict] = None,
                      output: Optional[dict] = None, industry: Optional[str] = None) -> list:
    story = [P("Methods, data sources & disclaimer", STYLES["h2"])]
    story.append(P("Methods", STYLES["h3"]))
    for text in (
        "<b>Scorecard</b> (Bill Payne): compares a pre-revenue company with the typical pre-revenue company in "
        "its region on six weighted criteria. Used only at the Idea and Development stages.",
        "<b>Venture Capital</b>: values a projected exit and discounts it at the annual return an investor at "
        "this stage targets. This is the only place a target return is used.",
        "<b>Comparables</b>: applies public-company EV/EBITDA multiples to the last 12 months' EBITDA, with a "
        "private-company discount, then subtracts debt and adds cash.",
        "<b>Discounted cash flow</b>: discounts projected free cash flow and a terminal value at the cost of "
        "capital (WACC), weights the result by the probability of survival, then subtracts debt and adds cash.",
        "The four results are blended with weights for the company's stage. Methods that can't give a "
        "meaningful value (for example, no positive EBITDA) are left out and the other weights are scaled up.",
    ):
        story.append(P(text, STYLES["td_label"], raw=True))
        story.append(Spacer(1, 3))

    if stage_params:
        story.append(P(f"Assumptions for the {safe(stage).lower()}", STYLES["h3"]))
        story.append(info_table([
            ("Investor's target annual return (VC method)", pct(stage_params.get("vc_target_return"))),
            ("Private-company discount (Comparables)", pct(stage_params.get("private_company_discount"))),
            ("Probability of survival (DCF)", pct(stage_params.get("survival_probability"))),
        ]))

    if country_inputs:
        story += _country_inputs_section(country or "", country_inputs, output or {})

    story.append(P("Data sources", STYLES["h3"]))
    labels = [
        ("industry_benchmarks", "Industry benchmarks"),
        ("country_data", "Country risk and tax"),
        ("risk_free_rate", "Risk-free rate (other countries)" if country_inputs and "risk_free_rate" in country_inputs
         else "Risk-free rate"),
        ("perpetual_growth_rate", "Long-run growth"),
        ("vc_target_return", "VC target returns"),
        ("private_company_discount", "Private-company discount"),
        ("survival_probability", "Survival probability"),
        ("scorecard_benchmark", "Scorecard benchmark"),
        ("scorecard", "Scorecard method"),
        ("method_weights", "Method weights"),
    ]
    for key, label in labels:
        if sources.get(key):
            story.append(P(f"<b>{esc(label)}:</b> {esc(_relevant_sentences(sources[key], country, industry))}",
                           STYLES["td_label"], raw=True))
            story.append(Spacer(1, 3))

    story.append(Spacer(1, 10))
    disclaimer_box = Table([[P(
        "<b>Disclaimer:</b> This tool is not a licensed financial advisor, investment advisor, or "
        "legal entity authorized to provide financial, investment, or legal advice. This report is "
        "intended for informational purposes only and should not be construed as a solicitation or "
        "recommendation for any financial transaction. All valuations are based on information and "
        "assumptions believed to be accurate at the time of preparation, but no guarantee is made "
        "regarding completeness, accuracy, or future performance. Consult a qualified financial, "
        "legal, or investment professional before making decisions based on this analysis. "
        f"{BRAND} by {AUTHOR}.",
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
    sources: Optional[dict] = None, stage_params: Optional[dict] = None,
    country_inputs: Optional[dict] = None,
) -> bytes:
    """
    payload:   a ValuationInput.model_dump(mode="json") dict.
    output:    a dataclasses.asdict(ValuationOutput) dict.
    benchmark: optional resolved industry-benchmark dict, for the reference-only cost lines.
    scenarios: optional {"80%": <asdict ValuationOutput>, ...} for the Scenario page.
    sources:   optional data-source notes (reference_data.json "sources").
    stage_params: optional stage parameters for the company's stage.
    country_inputs: optional country-specific inputs (reference_data.json "country_specific", e.g. Germany).
    """
    cp = g(payload, "company_profile", {}) or {}
    mkt = g(payload, "market_and_team_assessment", {}) or {}
    ops = g(payload, "operating_performance", {}) or {}
    fin = g(payload, "financial_assumptions", {}) or {}
    funding = g(payload, "funding", {}) or {}
    ownership = g(payload, "ownership", []) or []
    sources = sources or {}

    company_name = g(cp, "company_name") or "Untitled company"
    date_str = format_date(g(output, "valuation_date"))
    data_date = format_date(sources.get("data_as_of")) if sources.get("data_as_of") else "January 2026"
    logo_bytes = _decode_logo(g(cp, "logo_base64"))

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=MARGIN_SIDE, rightMargin=MARGIN_SIDE,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
        title=f"{company_name} - Valuation Report ({BRAND})",
        author=BRAND,
    )
    cover_frame = Frame(MARGIN_SIDE, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="cover")
    content_frame = Frame(MARGIN_SIDE, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="content")
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=_draw_nothing),
        PageTemplate(id="Content", frames=[content_frame], onPage=_make_page_decorator(company_name, date_str, logo_bytes)),
    ])

    years = g(output, "projections", {}).get("years", [])
    story: list = []
    story += _cover_page(company_name, date_str, data_date, logo_bytes, _at_a_glance(output, scenarios or {}))
    story.append(NextPageTemplate("Content"))
    story.append(PageBreak())
    story += _company_summary_page(cp, mkt, ops, output)
    story.append(PageBreak())
    story += _projections_page(cp, fin, funding, ownership, output, benchmark or {})
    story.append(PageBreak())
    story += _valuation_summary_page(output)
    story.append(PageBreak())
    story += _warnings_page(output)
    # The scenarios follow on the same page when they fit as a whole, else on the next one.
    story.append(Spacer(1, 10))
    story.append(KeepTogether(_scenario_sensitivity_page(scenarios or {})))
    story.append(PageBreak())
    scorecard_mv = g(output, "method_values", {}).get("scorecard") or {}
    if scorecard_mv.get("status") != "not_used":  # else one line on the Valuation page says why
        story += _scorecard_page(g(output, "scorecard", {}), cp, scorecard_mv)
        story.append(PageBreak())
    story += _vc_page(g(output, "venture_capital", {}), output)
    story.append(PageBreak())
    story += _comparables_page(g(output, "comparables", {}), cp)
    story.append(PageBreak())
    story += _dcf_page(g(output, "dcf", {}), g(output, "wacc", {}), years, _german_taxes(output))
    story.append(PageBreak())
    story += _methodology_page(sources, stage_params, g(cp, "company_stage"), g(cp, "country"),
                               country_inputs, output, g(cp, "industry"))

    doc.build(story)
    return buffer.getvalue()
