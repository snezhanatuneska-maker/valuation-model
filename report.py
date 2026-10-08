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

import valuation_engine as ve

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
    # Source notes and other reference text on the methodology page.
    "td_label_compact": ParagraphStyle("td_label_compact", fontName="Helvetica", fontSize=8.5, leading=10.5,
                                       textColor=TEXT),
    "td_value_compact": ParagraphStyle("td_value_compact", fontName="Helvetica-Bold", fontSize=8.5, leading=10.5,
                                       textColor=TEXT, alignment=2),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10.5, textColor=TEXT),
    "small_note": ParagraphStyle("small_note", fontName="Helvetica-Oblique", fontSize=8, leading=10.5,
                                 textColor=MUTED_LIGHT, spaceAfter=4),
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


def T(en: str, de: str) -> str:
    """The text in the report's language (set by the API from ?lang=)."""
    return ve._t(en, de)


def _german() -> bool:
    return ve.current_language() == "de"


def _n(x: float, decimals: int = 0) -> str:
    """1,234.5 / 1.234,5"""
    return ve._num(x, decimals)


def _eur_text(amount_text: str, sign: str = "") -> str:
    """€1,234 / 1.234 € with the sign in front."""
    return f"{sign}{amount_text}\u00a0\u20ac" if _german() else f"{sign}\u20ac{amount_text}"


def money(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "\u2014"
    sign = "-" if round(n) < 0 else ""  # never "-€0"
    return _eur_text(_n(abs(n)), sign)


def pct(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "\u2014"
    return f"{_n(n * 100, 1)}\u00a0%" if _german() else f"{n * 100:.1f}%"


def money_compact(n: Any) -> str:
    """Short amounts for narrow cells and chart labels: -€5k, €987k, €18.08M (German: 987 Tsd. €, 18,08 Mio. €)."""
    if n is None:
        return "—"
    v = float(n)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}{_n(a / 1_000_000, 2)}\u00a0Mio.\u00a0\u20ac" if _german() else f"{sign}\u20ac{a / 1_000_000:,.2f}M"
    if a >= 1_000:
        return f"{sign}{_n(a / 1_000)}\u00a0Tsd.\u00a0\u20ac" if _german() else f"{sign}\u20ac{a / 1_000:,.0f}k"
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
    return _eur_text(_n(abs(n), 2), sign)


def pct2(n: Any) -> str:
    """Percent with 2 decimals, for rates (WACC, cost of equity, ...)."""
    if n is None or n == "":
        return "\u2014"
    return f"{_n(float(n) * 100, 2)}\u00a0%" if _german() else f"{float(n) * 100:.2f}%"


def multiple(n: Any) -> str:
    if n is None or n == "":
        return "\u2014"
    return f"{_n(float(n), 2)}\u00d7"


def count(n: Any) -> str:
    """A whole number with thousands separators (share counts)."""
    return "\u2014" if n is None else _n(round(float(n)))


def scenario_label(label: str) -> str:
    """'80%' / '80 %'."""
    return label.replace("%", "\u00a0%") if _german() else label


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


def info_table(rows: list[tuple[str, str]], col_widths=None, compact: bool = False) -> Table:
    """Two-column label/value table (mirrors .pr-table used for plain info blocks). `compact`: smaller type and
    padding, for the dense company-summary tables."""
    label_style = STYLES["td_label_compact"] if compact else LABEL_STYLE
    value_style = STYLES["td_value_compact"] if compact else VALUE_STYLE
    data = [[P(safe(label), label_style), P(safe(value), value_style)] for label, value in rows]
    widths = col_widths or [CONTENT_W * 0.55, CONTENT_W * 0.45]
    t = Table(data, colWidths=widths)
    pad = 3 if compact else 4.5
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
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

_SCORECARD_LABELS = {
    "management_team_experience": ("Management team experience", "Erfahrung des Managementteams"),
    "willingness_to_step_aside_for_ceo": ("Willingness to step aside for an experienced CEO",
                                          "Bereit, einer erfahrenen CEO Platz zu machen?"),
    "management_team_completeness": ("How complete is the management team?", "Wie vollständig ist das Team?"),
    "product_development_stage": ("Product development stage", "Entwicklungsstand des Produkts"),
    "product_compelling_to_customers": ("Is the product compelling to customers?", "Überzeugt das Produkt die Kunden?"),
    "product_can_be_duplicated": ("Can this product be duplicated by others?", "Kann das Produkt kopiert werden?"),
    "strength_of_competitors_in_market": ("Strength of competitors in the market", "Stärke der Wettbewerber"),
    "strength_of_competitive_products": ("Strength of competitive products", "Stärke der Konkurrenzprodukte"),
    "target_market_size": ("Target market size", "Größe des Zielmarkts"),
    "revenue_potential_in_5_years": ("Revenue potential in 5 years", "Umsatzpotenzial in 5 Jahren"),
    "sales_channels_partners": ("Sales channels / sales partners", "Vertriebswege / Vertriebspartner"),
    "marketing_partners": ("Marketing partners", "Marketingpartner"),
    "need_for_additional_funding_rounds": ("Need for additional funding rounds", "Weitere Finanzierungsrunden nötig?"),
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
_CRITERIA_LABELS = {
    "strength_of_the_team": ("Strength of the team", "Stärke des Teams"),
    "size_of_the_opportunity": ("Size of the opportunity", "Größe der Chance"),
    "competitive_environment": ("Competitive environment", "Wettbewerbsumfeld"),
    "strength_and_protection_of_product": ("Strength & protection of product", "Stärke und Schutz des Produkts"),
    "strategic_relationships_with_partners": ("Strategic relationships with partners", "Strategische Partnerschaften"),
    "funding_required": ("Funding required", "Kapitalbedarf"),
}
_METHOD_LABELS = {
    "scorecard": ("Scorecard", "Scorecard"),
    "venture_capital": ("Venture Capital", "Venture Capital"),
    "comparables": ("Comparables (revenue or EBITDA multiple)", "Vergleichsmethode (Umsatz- oder EBITDA-Multiplikator)"),
    "dcf": ("Discounted cash flow (DCF)", "Discounted Cashflow (DCF)"),
}
_METHOD_CHART_LABELS = {"scorecard": ("Scorecard", "Scorecard"), "venture_capital": ("VC", "VC"),
                        "comparables": ("Comparables", "Vergleich"), "dcf": ("DCF", "DCF")}
_SOURCE_NOTES = {
    "region": ("", ""),
    "industry_global": ("global figure*", "weltweiter Wert*"),
    "cross_industry": ("all-industry median*", "Median aller Branchen*"),
    "user_override": ("your input", "Ihre Eingabe"),
}
_REGION_SHORT = {
    "Europe (EU, UK, Switzerland & Scandinavia)": ("Europe", "Europa"),
    "Emerging Markets (Asia, Latin America, Eastern Europe, Mid East and Africa)": ("Emerging Markets", "Schwellenländer"),
    "US": ("US", "USA"),
    "India": ("India", "Indien"),
    "Global": ("Global", "Weltweit"),
}


def _pick(table: dict, key: str) -> str:
    en, de = table.get(key, (key, key))
    return T(en, de)


def scorecard_label(key: str) -> str:
    return _pick(_SCORECARD_LABELS, key)


def criteria_label(key: str) -> str:
    return _pick(_CRITERIA_LABELS, key)


def method_label(key: str) -> str:
    return _pick(_METHOD_LABELS, key)


def method_chart_label(key: str) -> str:
    return _pick(_METHOD_CHART_LABELS, key)


def source_note(source: Optional[str]) -> str:
    return _pick(_SOURCE_NOTES, source or "region")


def short_region(region: Optional[str]) -> str:
    return _pick(_REGION_SHORT, region) if region in _REGION_SHORT else (region or "—")


def format_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        return iso
    return ve._long_date(d.date()).lstrip("0")


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
            canvas.drawRightString(PAGE_W - MARGIN_SIDE, header_y, T("COMPANY VALUATION REPORT", "UNTERNEHMENSBEWERTUNG"))
        canvas.setStrokeColor(BORDER)
        canvas.line(MARGIN_SIDE, header_y - 4, PAGE_W - MARGIN_SIDE, header_y - 4)

        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#a7b0bc"))
        canvas.drawCentredString(
            PAGE_W / 2, MARGIN_BOTTOM - 10 * mm,
            T(f"{BRAND} by {AUTHOR} · Valuation as of {date_str} · Page {doc.page} · "
              "Informational estimate, not a certified appraisal",
              f"{BRAND} von {AUTHOR} · Bewertung zum {date_str} · Seite {doc.page} · "
              "Unverbindliche Schätzung, kein zertifiziertes Gutachten"),
        )
        canvas.restoreState()

    return _draw


def _draw_nothing(canvas, doc):
    pass


def _how(text: str) -> list:
    """A shaded 'How this number was reached' box."""
    box = Table([[P(f"<b>{T('How this number was reached.', 'So entsteht diese Zahl.')}</b> {esc(text)}",
                    STYLES["td_label"], raw=True)]],
                colWidths=[CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DISCLAIMER_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    return [box, Spacer(1, 10)]


def _counts_as_zero(reason: Optional[str]) -> list:
    if not reason:
        return []
    return [P(f"<b>{T('Counts as €0 in the blend:', 'Zählt mit 0 € in der Gewichtung:')}</b> {esc(reason)}",
              STYLES["td_label"], raw=True), Spacer(1, 8)]


def _not_meaningful(reason: Optional[str]) -> list:
    if not reason:
        return []
    return [P(f"<b>{T('Not used in the blend:', 'Nicht in der Gewichtung:')}</b> {esc(reason)}",
              STYLES["td_label"], raw=True), Spacer(1, 8)]


# ============================================================================
# Page builders - each returns a list of flowables
# ============================================================================


_METHOD_SHORT = {"scorecard": ("Scorecard", "Scorecard"), "venture_capital": ("Venture Capital", "Venture Capital"),
                 "comparables": ("Comparables", "Vergleichsmethode"), "dcf": ("DCF", "DCF")}


def method_short(key: str) -> str:
    return _pick(_METHOD_SHORT, key)


def _join(items: list[str]) -> str:
    """'A, B and C' / 'A, B und C'."""
    if len(items) <= 1:
        return "".join(items)
    return T(" and ", " und ").join([", ".join(items[:-1]), items[-1]])


def _at_a_glance(output: dict, scenarios: dict) -> list:
    """The result in a few lines: the blend, post-money, how far apart the methods are, what a 20%
    revenue miss does, and how a typical round would price the company. A startup's value is uncertain;
    the ranges say how uncertain."""
    used = [method_short(k) for k, mv in (g(output, "method_values", {}) or {}).items()
            if mv.get("status") == "ok" and mv.get("weight_used")]
    rows = [(T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(output, "blended_pre_money_valuation")),
             T("blend of ", "gewichtet aus ") + _join(used) if used else ""),
            (T("Post-money valuation", "Post-Money-Bewertung"), money(g(output, "post_money_valuation")),
             T(f"after raising {money(g(output, 'capital_needed'))}",
               f"nach Aufnahme von {money(g(output, 'capital_needed'))}"))]
    rng = g(output, "method_range")
    if rng:
        rows.append((T("Range across methods", "Spanne der Methoden"),
                     T(f"{money(rng['low'])} ({method_short(rng['low_method'])}) to "
                       f"{money(rng['high'])} ({method_short(rng['high_method'])})",
                       f"{money(rng['low'])} ({method_short(rng['low_method'])}) bis "
                       f"{money(rng['high'])} ({method_short(rng['high_method'])})"),
                     T("lowest and highest method", "niedrigste und höchste Methode")))
    low, high = (scenarios or {}).get("80%"), (scenarios or {}).get("120%")
    if low and high and abs(high["blended_pre_money_valuation"] - low["blended_pre_money_valuation"]) >= 1:
        rows.append((T("If Year-1 revenue is 20% lower or higher", "Wenn der Umsatz in Jahr 1 20 % niedriger oder höher ist"),
                     T(f"{money(low['blended_pre_money_valuation'])} to {money(high['blended_pre_money_valuation'])}",
                       f"{money(low['blended_pre_money_valuation'])} bis {money(high['blended_pre_money_valuation'])}"),
                     T("blended pre-money", "gewichtete Pre-Money-Bewertung")))
    rl = g(output, "round_logic")
    if rl:
        rows.append((T("Round logic (how a typical round would price it)",
                       "Rundenlogik (so würde eine typische Runde bepreisen)"),
                     T(f"{money_compact(rl['implied_pre_money_low'])} to {money_compact(rl['implied_pre_money_high'])}",
                       f"{money_compact(rl['implied_pre_money_low'])} bis {money_compact(rl['implied_pre_money_high'])}"),
                     T(f"pre-money if {money(rl['capital_needed'])} buys a typical {rl['round_name']} stake of "
                       f"{ve._pct0(rl['dilution_low'])}–{ve._pct0(rl['dilution_high'])}",
                       f"Pre-Money, wenn {money(rl['capital_needed'])} einen typischen {rl['round_name']}-Anteil von "
                       f"{ve._pct0(rl['dilution_low'])}–{ve._pct0(rl['dilution_high'])} kaufen")))
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
    story = [P(T("At a glance", "Auf einen Blick"), STYLES["h3"]), table]
    checks = [w for w in (g(output, "warnings", []) or []) if w.get("severity") == "warning"]
    if checks:
        n = len(checks)
        lead = T(f"<b>Before sharing this report:</b> {n} of your inputs {'needs' if n == 1 else 'need'} a second "
                 "look (see \"Checks on your inputs\").",
                 f"<b>Bevor Sie diesen Bericht weitergeben:</b> {n} Ihrer Angaben "
                 f"{'sollte' if n == 1 else 'sollten'} noch einmal geprüft werden (siehe \"Prüfung Ihrer Angaben\").")
        story.append(P(lead + (" " + esc(next(w["message"] for w in checks if w.get("code") == "implausible_value"))
                               if any(w.get("code") == "implausible_value" for w in checks) else ""),
                       STYLES["sub"], raw=True))
    if rng:
        story.append(P(T("The methods look at the company from different angles and rarely agree; the range shows how "
                         "far apart they are. The revenue line shows how much the result depends on the plan.",
                         "Die Methoden betrachten das Unternehmen aus verschiedenen Blickwinkeln und stimmen selten "
                         "überein; die Spanne zeigt, wie weit sie auseinanderliegen. Die Umsatzzeile zeigt, wie stark "
                         "das Ergebnis vom Plan abhängt."),
                       STYLES["sub"]))
    return story


def _cover_page(company_name: str, date_str: str, data_date: str, logo_bytes: Optional[bytes],
                glance: Optional[list] = None) -> list:
    story = [Spacer(1, 38 * mm)]
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
    story.append(P(T("COMPANY VALUATION REPORT", "UNTERNEHMENSBEWERTUNG"), STYLES["eyebrow"]))
    story.append(P(company_name, STYLES["h1"]))
    meta_rows = [
        [P(T("Prepared for", "Erstellt für"), STYLES["cover_meta_label"]), P(company_name, STYLES["cover_meta_value"])],
        [P(T("Valuation date", "Bewertungsdatum"), STYLES["cover_meta_label"]), P(date_str, STYLES["cover_meta_value"])],
        [P(T("Market data", "Marktdaten"), STYLES["cover_meta_label"]),
         P(f"Damodaran Online (NYU Stern), {data_date}", STYLES["cover_meta_value"])],
        [P(T("Report generated", "Erstellt am"), STYLES["cover_meta_label"]),
         P(format_date(datetime.now().strftime("%Y-%m-%d")), STYLES["cover_meta_value"])],
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
    story.append(P(T(
        "This report is an automated, informational estimate blending four standard valuation "
        "methods. It is not a certified appraisal, and should not be relied on as financial, "
        "investment, or legal advice. It is not a valuation under IDW S1 or the German valuation law (BewG) and "
        "can't be used for tax purposes or employee share plans.",
        "Dieser Bericht ist eine automatisierte, unverbindliche Schätzung aus vier gängigen Bewertungsmethoden. "
        "Er ist kein zertifiziertes Gutachten und keine Finanz-, Anlage- oder Rechtsberatung. Er ist keine "
        "Bewertung nach IDW S1 oder dem Bewertungsgesetz (BewG) und kann nicht für steuerliche Zwecke oder "
        "Mitarbeiterbeteiligungen (VSOP/ESOP) verwendet werden."),
        STYLES["cover_disclaimer"],
    ))
    story.append(Spacer(1, 6))
    story.append(P(T(f"Prepared with {BRAND} by {AUTHOR}.", f"Erstellt mit {BRAND} von {AUTHOR}."),
                   STYLES["cover_disclaimer"]))
    return story


def _german_taxes(output: dict) -> Optional[dict]:
    """The German stepped tax schedule from the output, or None if a flat rate was used."""
    taxes = g(g(output, "projections", {}), "tax_schedule") or {}
    return taxes if taxes.get("basis") == "germany_schedule" else None


def _hebesatz_text(taxes: dict) -> str:
    who = (T("your municipality", "Ihre Gemeinde") if taxes.get("hebesatz_source") == "user"
           else T("national average", "Bundesdurchschnitt"))
    return f"{ve._pct0(taxes.get('hebesatz') or 0)} ({who})"


def _company_summary_page(cp: dict, mkt: dict, ops: dict, output: dict) -> list:
    story = [P(T("Company summary", "Unternehmensübersicht"), STYLES["h2"])]
    proj = g(output, "projections", {})
    tax_note = (T("your input", "Ihre Eingabe") if g(cp, "dcf_tax_rate_override") is not None
                else T("country statutory rate", "gesetzlicher Satz des Landes"))
    german = _german_taxes(output)
    if german:
        rates = [y.get("tax_rate") for y in proj.get("years", [])]
        tax_text = T(f"{pct(rates[0])} in Year 1 falling to {pct(rates[-1])} in Year 5 and "
                     f"{pct(german.get('long_run_rate'))} after (German schedule)",
                     f"{pct(rates[0])} in Jahr 1, sinkend auf {pct(rates[-1])} in Jahr 5 und "
                     f"{pct(german.get('long_run_rate'))} danach (deutscher Steuerverlauf)")
    else:
        tax_text = f"{pct(g(proj, 'tax_rate'))} ({tax_note})"
    years_to_exit = g(cp, "planned_time_to_exit_years")

    general = info_table([
        (T("Company name", "Firmenname"), g(cp, "company_name")),
        (T("Contact name", "Ansprechperson"), g(cp, "contact_name")),
        (T("Contact email", "E-Mail"), g(cp, "contact_email")),
        (T("Company address", "Adresse"), g(cp, "address")),
        (T("Website", "Website"), g(cp, "website")),
        (T("Country", "Land"), ve.country_label(g(cp, "country") or "")),
        (T("Number of founders", "Anzahl der Gründer"), g(cp, "num_founders")),
        (T("Number of employees", "Anzahl der Mitarbeitenden"), g(cp, "num_employees")),
        (T("Year of incorporation", "Gründungsjahr"), g(cp, "year_of_incorporation")),
        (T("Company stage", "Phase"), ve.stage_label(g(cp, "company_stage") or "")),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6], compact=True)
    business = info_table([
        (T("Business activity", "Geschäftstätigkeit"), g(cp, "business_activity")),
        (T("Industry", "Branche"), g(cp, "industry")),
        (T("Business territory", "Region"), short_region(g(cp, "business_territory_region"))),
        (T("Business model", "Geschäftsmodell"), g(cp, "business_model")),
        (T("Committed capital", "Zugesagtes Kapital"), money(g(cp, "committed_capital", 0))),
        (T("Exit strategy", "Exit-Strategie"), g(cp, "exit_strategy")),
        (T("Planned time to exit", "Geplante Zeit bis zum Exit"),
         T(f"{years_to_exit} year{'' if years_to_exit == 1 else 's'}", f"{years_to_exit} Jahr{'' if years_to_exit == 1 else 'e'}")),
        (T("Tax rate", "Steuersatz"), tax_text),
    ], col_widths=[HALF_W * 0.4, HALF_W * 0.6], compact=True)

    story.append(side_by_side([P(T("General info", "Allgemeine Angaben"), STYLES["h3"]), general],
                              [P(T("Business profile", "Geschäftsprofil"), STYLES["h3"]), business]))
    story.append(Spacer(1, 10))

    competitors = [c for c in (g(mkt, "key_competitor_1"), g(mkt, "key_competitor_2"), g(mkt, "key_competitor_3")) if c]
    # Questionnaire answers: only the ones the founder actually gave. Companies with revenue
    # may skip the questionnaire (the Scorecard only counts before revenue).
    answered = any(g(mkt, k) for k in (*MARKET_POTENTIAL_KEYS, *TEAM_PRODUCT_KEYS))
    answer = lambda k: ve.option_label(k, g(mkt, k)) or T("Not answered", "Nicht beantwortet")  # noqa: E731
    if answered:
        market_rows = [(scorecard_label(k), answer(k)) for k in MARKET_POTENTIAL_KEYS]
        team_rows = [(scorecard_label(k), answer(k)) for k in TEAM_PRODUCT_KEYS]
    else:
        market_rows = []
        team_rows = [(T("Questionnaire", "Fragebogen"),
                      T("Not answered (only needed before revenue, for the Scorecard method)",
                        "Nicht beantwortet (nur vor Umsatzbeginn nötig, für die Scorecard-Methode)"))]
    market_rows.append((T("Key competitors", "Wichtigste Wettbewerber"), ", ".join(competitors) if competitors else None))
    market_table = info_table(market_rows, col_widths=[HALF_W * 0.45, HALF_W * 0.55], compact=True)
    team_table = info_table(team_rows, col_widths=[HALF_W * 0.45, HALF_W * 0.55], compact=True)
    rev = g(ops, "current_revenue_last_12_months") or 0
    margin = (g(ops, "current_ebitda") or 0) / rev if rev else None
    perf_rows = [
        (T("Revenue, last 12 months", "Umsatz, letzte 12 Monate"), money(rev)),
        (T("EBITDA, last 12 months", "EBITDA, letzte 12 Monate"), money(g(ops, "current_ebitda"))),
        (T("EBITDA margin", "EBITDA-Marge"), pct(margin)),
    ]
    if g(ops, "annual_recurring_revenue"):
        perf_rows.append((T("Annual recurring revenue (ARR)", "Jährlich wiederkehrender Umsatz (ARR)"),
                          money(g(ops, "annual_recurring_revenue"))))
    perf_rows += [
        (T("Cash available", "Liquide Mittel"), money(g(ops, "cash_available"))),
        (T("PP&E (current value)", "Sachanlagen (aktueller Wert)"), money(g(ops, "current_ppe_value"))),
    ]
    perf_table = info_table(perf_rows, col_widths=[HALF_W * 0.4, HALF_W * 0.6], compact=True)

    story.append(side_by_side(
        [P(T("Market potential", "Marktpotenzial"), STYLES["h3"]), market_table,
         P(T("Latest operating performance", "Aktuelle Geschäftszahlen"), STYLES["h3"]), perf_table],
        [P(T("Team & product", "Team & Produkt"), STYLES["h3"]), team_table],
    ))
    return story


def _projections_page(cp: dict, fin: dict, funding: dict, ownership: list, output: dict, benchmark: dict) -> list:
    story = [P(T("Projected financials & valuation inputs", "Planzahlen & Bewertungsgrundlagen"), STYLES["h2"])]
    proj = g(output, "projections", {})
    years = proj.get("years", [])
    labels = [y.get("year_label", "") for y in years]
    target_name = (T("your target margin", "Ihre Zielmarge") if proj.get("target_margin_source") == "user_override"
                   else T("the industry margin", "die Branchenmarge"))
    start_pct, target_pct = pct(proj.get("starting_ebitda_margin")), pct(proj.get("target_ebitda_margin"))
    if proj.get("starting_margin_source") == "company":
        margin_text = T(f"The EBITDA margin starts from your last-12-month margin ({start_pct}) and moves in equal "
                        f"steps to {target_name} ({target_pct}) by Year 5.",
                        f"Die EBITDA-Marge beginnt bei Ihrer Marge der letzten 12 Monate ({start_pct}) und erreicht "
                        f"in gleichen Schritten bis Jahr 5 {target_name} ({target_pct}).")
    else:
        margin_text = T(f"The EBITDA margin starts from today's EBITDA ÷ Year-1 revenue ({start_pct}) and moves in "
                        f"equal steps to {target_name} ({target_pct}) by Year 5.",
                        f"Die EBITDA-Marge beginnt bei heutigem EBITDA ÷ Umsatz in Jahr 1 ({start_pct}) und erreicht "
                        f"in gleichen Schritten bis Jahr 5 {target_name} ({target_pct}).")
    vdate = format_date(proj.get("valuation_date"))
    story.append(P(T(f"Year 1 is the 12 months after the valuation date ({vdate}). {margin_text}",
                     f"Jahr 1 sind die 12 Monate nach dem Bewertungsdatum ({vdate}). {margin_text}"),
                   STYLES["sub"]))

    def period(y):
        text = y.get("period_label", "")
        for prefix in ("to ", "bis "):
            text = text.replace(prefix, "")
        return text
    headers = [""] + [f"{y.get('year_label')} ({period(y)})" for y in years]
    amt = amount_format([y.get(k) for y in years for k in ("revenue", "ebitda", "capex", "unlevered_fcf")])
    rows = [
        [T("Revenue", "Umsatz")] + [amt(y.get("revenue")) for y in years],
        [T("EBITDA margin", "EBITDA-Marge")] + [pct(y.get("ebitda_margin")) for y in years],
        ["EBITDA"] + [amt(y.get("ebitda")) for y in years],
        [T("Capital expenditure", "Investitionen")] + [amt(y.get("capex")) for y in years],
        [T("Free cash flow", "Freier Cashflow")] + [amt(y.get("unlevered_fcf")) for y in years],
    ]
    n = max(len(years), 1)
    story.append(data_table(headers, rows, col_widths=[CONTENT_W * 0.25] + [CONTENT_W * 0.75 / n] * n))
    story.append(Spacer(1, 8))
    story.append(side_by_side(
        [P(T("Revenue by year", "Umsatz je Jahr"), STYLES["h3"]),
         bar_chart(labels, [y.get("revenue", 0) for y in years], color=NAVY_LINE, height=80)],
        [P(T("EBITDA by year", "EBITDA je Jahr"), STYLES["h3"]),
         bar_chart(labels, [y.get("ebitda", 0) for y in years], color=BLUE_MID, height=80)],
    ))

    region = g(cp, "business_territory_region")
    used = g(output, "benchmarks_used", {})
    wacc = g(output, "wacc", {})

    def bm(metric, fmt=pct):
        b = used.get(metric)
        if not b:
            return "—"
        note = source_note(b.get("source"))
        return f"{fmt(b.get('value'))}" + (f" ({note})" if note else "")

    def info_only(metric):
        table = g(benchmark, metric) or {}
        v = table.get(region)
        return pct(v) if v is not None else "—"

    story.append(P(T(f"Benchmarks used — {safe(g(cp, 'industry'))} / {short_region(region)}",
                     f"Verwendete Vergleichswerte — {safe(g(cp, 'industry'))} / {short_region(region)}"), STYLES["h3"]))
    rows = [
        ((T("Industry EBITDA margin (for reference)", "EBITDA-Marge der Branche (zum Vergleich)"),
          T(f"{info_only('ebitda_margin')} (not used: your own Year-5 target is {pct(proj.get('target_ebitda_margin'))})",
            f"{info_only('ebitda_margin')} (nicht verwendet: Ihre Zielmarge für Jahr 5 ist "
            f"{pct(proj.get('target_ebitda_margin'))})"))
         if proj.get("target_margin_source") == "user_override"
         else (T("Industry EBITDA margin (Year-5 target)", "EBITDA-Marge der Branche (Ziel in Jahr 5)"),
               bm("ebitda_margin"))),
        (T("  of which: COGS / SG&A / R&D (% of revenue, for reference)",
           "  davon: Herstellkosten / Vertrieb & Verw. / F&E (% vom Umsatz)"),
         f"{info_only('cogs_pct_revenue')} / {info_only('sga_pct_revenue')} / {info_only('rd_pct_revenue')}"),
        (T("D&A (% of revenue)", "Abschreibungen (% vom Umsatz)"), bm("da_pct_revenue")),
        (T("Accounts receivable / inventory / payable (% of revenue)",
           "Forderungen / Vorräte / Verbindl. (% vom Umsatz)"),
         f"{bm('acc_receivable_pct_revenue')} / {bm('inventory_pct_revenue')} / {bm('acc_payable_pct_revenue')}"),
        (T("EV/EBITDA multiple (trailing, profitable public companies)",
           "EV/EBITDA-Multiplikator (profitable Börsenunternehmen)"),
         bm("ev_ebitda_multiple", multiple)),
        *([(T("EV/Sales multiple (trailing, all public companies)",
              "EV/Umsatz-Multiplikator (alle Börsenunternehmen)"),
            bm("ev_sales_multiple", multiple))] if used.get("ev_sales_multiple") else []),
        ("Beta", bm("beta", lambda v: _n(float(v), 2))),
        (T("Cost of equity / WACC", "Eigenkapitalkosten / WACC"), f"{pct2(g(wacc, 'cost_of_equity'))} / {pct2(g(wacc, 'wacc'))}"),
    ]
    story.append(info_table(rows, col_widths=[CONTENT_W * 0.62, CONTENT_W * 0.38]))
    if any(b.get("source") in ("industry_global", "cross_industry") for b in used.values()):
        story.append(P(T("* Damodaran has no usable figure for this region, so the industry's global figure "
                         "(or the median across all industries) is used.",
                         "* Damodaran hat für diese Region keinen verwendbaren Wert; deshalb wird der weltweite Wert "
                         "der Branche (oder der Median aller Branchen) verwendet."), STYLES["sub"]))

    funds_entries = [(k, v) for k, v in (g(funding, "use_of_funds") or {}).items() if v]
    ownership_entries = [(o.get("name"), o.get("ownership_pct")) for o in (ownership or []) if o.get("ownership_pct", 0) > 0]
    allocated = sum(v for _, v in ownership_entries)
    if ownership_entries and allocated < 0.995:
        ownership_entries.append((T("Not specified", "Nicht angegeben"), 1 - allocated))
    funds_entries = [(USE_OF_FUNDS_DE.get(k, k) if _german() else k, v) for k, v in funds_entries]

    funds_block = [P(T("Use of funds", "Mittelverwendung"), STYLES["h3"])]
    if funds_entries:
        funds_block += [pie_chart([v for _, v in funds_entries], size=52), Spacer(1, 4),
                        legend_table(funds_entries, fmt=money)]
    else:
        funds_block.append(P("—", STYLES["td_label"]))
    own_block = [P(T("Ownership structure (before this round)", "Beteiligungsstruktur (vor dieser Runde)"), STYLES["h3"])]
    if ownership_entries:
        own_block += [pie_chart([v for _, v in ownership_entries], size=52), Spacer(1, 4),
                      legend_table(ownership_entries, fmt=pct)]
    else:
        own_block.append(P("—", STYLES["td_label"]))
    story.append(side_by_side(funds_block, own_block))
    return story


# The wizard's use-of-funds categories (the English names are the keys stored in the input).
USE_OF_FUNDS_DE = {
    "Product and R&D": "Produkt und F&E", "Product & R&D": "Produkt und F&E",
    "Sales and marketing": "Vertrieb und Marketing", "Sales & marketing": "Vertrieb und Marketing",
    "Inventory": "Vorräte", "Operations": "Betrieb", "Capital expenditures": "Investitionen", "Others": "Sonstiges",
}


def zero_value_reason(output: dict) -> str:
    """Why the pre-money value is zero (the same wording as on the results page)."""
    debt = g(g(output, "equity_bridge", {}), "debt") or 0
    if debt > 0:
        return T(f"The value is €0 because the company's debt ({money(debt)}) is larger than the value the methods "
                 "find for the business, so the shares are worth about nothing before the new money comes in.",
                 f"Der Wert ist 0 €, weil die Schulden des Unternehmens ({money(debt)}) höher sind als der Wert, den "
                 "die Methoden für das Geschäft ermitteln; die Anteile sind vor dem neuen Geld also etwa nichts wert.")
    return T("The value rounds to €0: the methods used leave essentially no value for the shares before the new money.",
             "Der Wert rundet auf 0 €: Die verwendeten Methoden lassen vor dem neuen Geld praktisch keinen Wert für "
             "die Anteile übrig.")


def _round_logic_section(output: dict) -> list:
    """How a typical financing round at this stage would price the company: a cross-check, not in the blend."""
    rl = g(output, "round_logic")
    if not rl:
        return []
    story = [P(T("Round logic: how a typical round would price the company",
                 "Rundenlogik: So würde eine typische Finanzierungsrunde bewerten"), STYLES["h3"])]
    story.append(P(T(
        f"Early rounds are usually priced by the share of the company investors buy. A typical {rl['round_name']} "
        f"round sells {ve._pct0(rl['dilution_low'])}–{ve._pct0(rl['dilution_high'])} of the company (median "
        f"{pct(rl['dilution_median'])}). Pre-money = amount raised × (1 − share) ÷ share. This is a cross-check "
        "and is not part of the blended value.",
        f"Frühe Runden werden meist über den Anteil bepreist, den Investoren kaufen. Eine typische "
        f"{rl['round_name']}-Runde verkauft {ve._pct0(rl['dilution_low'])}–{ve._pct0(rl['dilution_high'])} des Unternehmens "
        f"(Median {pct(rl['dilution_median'])}). Pre-Money = eingeworbener Betrag × (1 − Anteil) ÷ Anteil. Das ist "
        "eine Gegenprobe und fließt nicht in den gewichteten Wert ein."), STYLES["sub"]))
    position = {"below": T("below the usual range", "unter der üblichen Spanne"),
                "above": T("above the usual range", "über der üblichen Spanne"),
                "within": T("within the usual range", "in der üblichen Spanne")}[rl["raise_position"]]
    rows = [
        (T(f"Typical {rl['round_name']} round size", f"Typische Größe einer {rl['round_name']}-Runde"),
         T(f"{money_compact(rl['typical_round_low'])} to {money_compact(rl['typical_round_high'])}",
           f"{money_compact(rl['typical_round_low'])} bis {money_compact(rl['typical_round_high'])}")),
        (T("Your round", "Ihre Runde"), f"{money(rl['capital_needed'])} ({position})"),
        (T("Implied pre-money (typical stake, low to high)", "Daraus folgende Pre-Money-Bewertung (typischer Anteil)"),
         T(f"{money_compact(rl['implied_pre_money_low'])} to {money_compact(rl['implied_pre_money_high'])} "
           f"(median {money_compact(rl['implied_pre_money_median'])})",
           f"{money_compact(rl['implied_pre_money_low'])} bis {money_compact(rl['implied_pre_money_high'])} "
           f"(Median {money_compact(rl['implied_pre_money_median'])})")),
    ]
    if rl.get("dilution_at_blend") is not None:
        rows.append((T("Share the round buys at the blended value", "Anteil, den die Runde beim gewichteten Wert kauft"),
                     pct(rl["dilution_at_blend"])))
    story.append(info_table(rows, col_widths=[CONTENT_W * 0.55, CONTENT_W * 0.45]))
    return story


def _valuation_summary_page(output: dict) -> list:
    story = [P(T("Valuation", "Bewertung"), STYLES["h2"])]
    story.append(P(T(
        "Each method estimates the company's equity value before the new investment (pre-money). "
        "They are blended using weights for the company's stage; a method that doesn't apply to this company "
        "is left out and the other weights are scaled up, and a method that finds no value counts as €0.",
        "Jede Methode schätzt den Wert des Eigenkapitals vor der neuen Investition (Pre-Money). Die Ergebnisse werden "
        "mit Gewichten für die Phase des Unternehmens kombiniert; eine Methode, die nicht zum Unternehmen passt, "
        "entfällt und die anderen Gewichte werden hochskaliert, und eine Methode, die keinen Wert findet, zählt mit "
        "0 €."), STYLES["sub"]))

    method_values = g(output, "method_values", {})
    rows, cats, vals = [], [], []
    for key, mv in method_values.items():
        status = mv.get("status")
        if status == "ok":
            value, weighted = money(mv.get("pre_money_value")), money(mv.get("weighted_value"))
            cats.append(method_chart_label(key))
            vals.append(mv.get("pre_money_value") or 0)
        elif status == "not_used":
            value, weighted = T("not used at this stage", "in dieser Phase nicht verwendet"), "—"
        else:
            value, weighted = T("not meaningful", "nicht aussagekräftig"), "—"
        rows.append([method_label(key), pct(mv.get("weight")), pct(mv.get("weight_used")), value, weighted])
    cats.append(T("Blended", "Gewichtet"))
    vals.append(g(output, "blended_pre_money_valuation") or 0)

    total_row = [T("Blended pre-money valuation", "Gewichtete Pre-Money-Bewertung"), "", "", "",
                 money(g(output, "blended_pre_money_valuation"))]
    story.append(data_table(
        [T("Method", "Methode"), T("Stage weight", "Gewicht der Phase"), T("Weight used", "Verwendetes Gewicht"),
         T("Pre-money value", "Pre-Money-Wert"), T("Weighted value", "Gewichteter Wert")], rows,
        total_row=total_row, total_label_span=4,
        col_widths=[CONTENT_W * 0.32, CONTENT_W * 0.13, CONTENT_W * 0.13, CONTENT_W * 0.22, CONTENT_W * 0.20],
    ))
    for key, mv in method_values.items():
        if mv.get("status") == "not_meaningful" or (mv.get("status") == "ok" and mv.get("note")):
            story.append(P(f"{method_label(key)}: {mv.get('note')}", STYLES["sub"]))
        elif key == "scorecard" and mv.get("status") == "not_used":
            story.append(P(T(f"Scorecard: not used at this stage. {scorecard_not_used()}",
                             f"Scorecard: in dieser Phase nicht verwendet. {scorecard_not_used()}"), STYLES["sub"]))
    story.append(Spacer(1, 8))
    story.append(bar_chart(cats, vals, color=NAVY_LINE, width=CONTENT_W, height=100))
    story.append(Spacer(1, 8))

    def callout(label, value, emphasize=False):
        t = Table([[P(label, STYLES["callout_label"]), P(value, STYLES["callout_value"])]],
                  colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TOTAL_BG if emphasize else ROW_STRIPE),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    story.append(callout(T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(output, "blended_pre_money_valuation"))))
    if (g(output, "blended_pre_money_valuation") or 0) < 0.5:
        story.append(Spacer(1, 4))
        story.append(P(zero_value_reason(output), STYLES["sub"]))
    story.append(Spacer(1, 4))
    story.append(callout(T("Capital being raised", "Eingeworbenes Kapital"), money(g(output, "capital_needed"))))
    story.append(Spacer(1, 4))
    story.append(callout(T("Post-money valuation", "Post-Money-Bewertung"), money(g(output, "post_money_valuation")),
                         emphasize=True))
    story += _round_logic_section(output)
    return story


def _warnings_page(output: dict) -> list:
    warnings = g(output, "warnings", []) or []
    has_checks = any(w.get("severity") == "warning" for w in warnings)
    story = [P(T("Checks on your inputs", "Prüfung Ihrer Angaben") if has_checks
               else T("Notes on how the data was used", "Hinweise zur Verwendung der Daten"), STYLES["h2"])]
    if not warnings:
        story.append(P(T("No issues were found in the inputs.", "In den Angaben wurden keine Probleme gefunden."),
                       STYLES["td_label"]))
        return story
    story.append(P(T("The calculation ran, but the points marked as warnings deserve a second look before the numbers "
                     "are shared; notes explain how the data was used.",
                     "Die Berechnung ist gelaufen, aber die als Warnung markierten Punkte sollten Sie prüfen, bevor Sie "
                     "die Zahlen weitergeben; Hinweise erklären, wie die Daten verwendet wurden.") if has_checks else
                   T("Nothing here needs fixing: these notes explain how your figures and the market data were used.",
                     "Hier muss nichts korrigiert werden: Die Hinweise erklären, wie Ihre Zahlen und die Marktdaten "
                     "verwendet wurden."), STYLES["sub"]))
    rows = [[T("Warning", "Warnung") if w.get("severity") == "warning" else T("Note", "Hinweis"), w.get("message")]
            for w in warnings]
    t = data_table([T("Type", "Art"), T("Detail", "Details")], rows,
                   col_widths=[CONTENT_W * 0.14, CONTENT_W * 0.86], text_table=True)
    story.append(t)
    return story


def scenario_falls_note() -> str:
    return T("Here a higher Year-1 revenue gives a lower value: in your plan, extra revenue costs more cash than it "
             "brings in (for example losses at the planned margin, or the working capital it ties up), so the "
             "cash-flow based values go down.",
             "Hier ergibt ein höherer Umsatz in Jahr 1 einen niedrigeren Wert: In Ihrem Plan kostet zusätzlicher Umsatz "
             "mehr Geld, als er einbringt (zum Beispiel Verluste bei der geplanten Marge oder gebundenes Working "
             "Capital); die Cashflow-basierten Werte sinken deshalb.")


def _scenario_sensitivity_page(scenarios: dict) -> list:
    story = [P(T("Scenario & sensitivity", "Szenarien & Sensitivität"), STYLES["h2"])]
    story.append(P(T(
        "How the valuation shifts if Year-1 revenue comes in above or below plan (later years scale with "
        "it). Scorecard and Comparables don't move: they use your questionnaire answers and the last 12 months' "
        "figures, not projected revenue.",
        "Wie sich die Bewertung verschiebt, wenn der Umsatz in Jahr 1 über oder unter dem Plan liegt (die späteren "
        "Jahre skalieren mit). Scorecard und Vergleichsmethode bewegen sich nicht: Sie verwenden Ihre Antworten im "
        "Fragebogen und die Zahlen der letzten 12 Monate, nicht den geplanten Umsatz."), STYLES["sub"],
    ))
    if not scenarios:
        story.append(P(T("Scenario data wasn't available when this report was generated.",
                         "Szenariodaten waren bei der Erstellung dieses Berichts nicht verfügbar."), STYLES["td_label"]))
        return story

    labels = list(scenarios.keys())
    n = len(labels)
    values = [scenarios[l].get("blended_pre_money_valuation") or 0 for l in labels]
    if max(values) - min(values) < 1:
        story.append(P(T(
            f"The value is the same in every scenario ({money(values[0])}): only methods that don't use projected "
            "revenue give a value for this company, so a higher or lower Year-1 revenue doesn't change it.",
            f"Der Wert ist in jedem Szenario gleich ({money(values[0])}): Nur Methoden, die den geplanten Umsatz nicht "
            "verwenden, ergeben einen Wert für dieses Unternehmen; ein höherer oder niedrigerer Umsatz in Jahr 1 ändert "
            "ihn deshalb nicht."), STYLES["td_label"]))
        return story

    def val(label, key):
        mv = scenarios[label].get("method_values", {}).get(key, {})
        if mv.get("status") == "ok":
            return money_compact(mv.get("pre_money_value"))
        return T("not used", "nicht verw.") if mv.get("status") == "not_used" else "n/m"

    rows = [[method_chart_label(k)] + [val(l, k) for l in labels]
            for k in ("scorecard", "venture_capital", "comparables", "dcf")]
    total_row = [T("Blended pre-money", "Gewichtete Pre-Money")] + [
        money_compact(scenarios[l].get("blended_pre_money_valuation")) for l in labels]
    col_w = [CONTENT_W * 0.28] + [CONTENT_W * 0.72 / n] * n
    story.append(data_table([T("Method", "Methode")] + [scenario_label(l) for l in labels], rows,
                            total_row=total_row, col_widths=col_w))
    any_nm = any(scenarios[l].get("method_values", {}).get(k, {}).get("status") == "not_meaningful"
                 for l in labels for k in ("scorecard", "venture_capital", "comparables", "dcf"))
    story.append(P(T("The weights stay as in the main result; a method that gives no positive value in a scenario "
                     "counts as €0.", "Die Gewichte bleiben wie im Hauptergebnis; eine Methode, die in einem Szenario "
                     "keinen positiven Wert ergibt, zählt mit 0 €.")
                   + (T(" n/m = left out of the blend, as in the main result.",
                        " n/m = nicht in der Gewichtung, wie im Hauptergebnis.") if any_nm else "")
                   + (" " + scenario_falls_note() if any(b < a - 1 for a, b in zip(values, values[1:])) else ""),
                   STYLES["sub"]))
    story.append(Spacer(1, 10))
    story.append(P(T("Blended pre-money valuation across scenarios", "Gewichtete Pre-Money-Bewertung je Szenario"),
                   STYLES["h3"]))
    story.append(bar_chart([scenario_label(l) for l in labels],
                           [scenarios[l].get("blended_pre_money_valuation") or 0 for l in labels],
                           color=NAVY_LINE, width=CONTENT_W, height=115))
    return story


def scorecard_not_used() -> str:
    return T("The Scorecard compares a pre-revenue company with the typical pre-revenue company in its region, so it "
             "applies only at the Idea and Development stages; companies with revenue are valued on their numbers by "
             "the other methods.",
             "Die Scorecard vergleicht ein Unternehmen vor Umsatzbeginn mit dem typischen Unternehmen vor Umsatzbeginn "
             "in seiner Region; sie gilt deshalb nur in der Ideen- und Entwicklungsphase. Unternehmen mit Umsatz "
             "werden von den anderen Methoden anhand ihrer Zahlen bewertet.")


def _scorecard_page(sc: dict, cp: dict, mv: Optional[dict] = None) -> list:
    story = [P(T("Scorecard method", "Scorecard-Methode"), STYLES["h2"])]
    if mv and mv.get("status") == "not_used":
        story.append(P(T(f"Not used for this company. {scorecard_not_used()}",
                         f"Für dieses Unternehmen nicht verwendet. {scorecard_not_used()}"), STYLES["td_label"]))
        return story
    story += _method_value_header(T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(sc, "pre_money_valuation")))
    bench = g(sc, "benchmark_pre_money_valuation")
    factor = g(sc, "total_factor")
    source = g(sc, "benchmark_source")
    basis = g(sc, "benchmark_basis") or ""
    basis = basis[:1].lower() + basis[1:] if basis.startswith("Average") else basis
    bench_src = (T("your own benchmark", "Ihr eigener Vergleichswert") if source == "user_override"
                 else basis if source == "country_table"
                 else T(f"{basis}, converted to euros", f"{basis}, umgerechnet in Euro"))
    region = g(cp, "business_territory_region")
    country = g(cp, "country") or ""
    where = (T("like yours", "wie Ihres") if source == "user_override"
             else T(f"in {country}", f"in {ve.country_in_text(country)}") if source == "country_table"
             else T("in the US", "in den USA") if region == "US"
             else T("anywhere (all-region figure)", "irgendwo (Wert aller Regionen)") if region == "Global"
             else T(f"in {short_region(region)}", f"in {short_region(region)}"))
    pre = money(g(sc, "pre_money_valuation"))
    story += _how(T(
        f"A typical pre-revenue company {where} is valued at about {money(bench)} before investment ({bench_src}). "
        f"Your answers score this company at {pct(factor)} of that typical company overall, so {money(bench)} × "
        f"{pct(factor)} = {pre}.",
        f"Ein typisches Unternehmen vor Umsatzbeginn {where} wird vor der Investition mit etwa {money(bench)} bewertet "
        f"({bench_src}). Nach Ihren Antworten erreicht dieses Unternehmen insgesamt {pct(factor)} dieses typischen "
        f"Unternehmens, also {money(bench)} × {pct(factor)} = {pre}."))
    criteria = g(sc, "criteria", {})
    rows = [[criteria_label(k), pct(c.get("weight")), pct(c.get("score")), money(c.get("amount_assigned"))]
            for k, c in criteria.items()]
    total_row = [T("Pre-money valuation", "Pre-Money-Bewertung"), "", pct(factor), pre]
    story.append(data_table(
        [T("Criterion", "Kriterium"), T("Weight", "Gewicht"), T("Score vs typical", "Wert ggü. typisch"),
         T("Benchmark × weight × score", "Vergleichswert × Gewicht × Wert")], rows,
        total_row=total_row, col_widths=[CONTENT_W * 0.38, CONTENT_W * 0.14, CONTENT_W * 0.2, CONTENT_W * 0.28],
    ))
    story.append(P(T("Method: Bill Payne's Scorecard (Ohio TechAngels). A score above 100% means stronger than "
                     "the typical company on that criterion.",
                     "Methode: Scorecard nach Bill Payne (Ohio TechAngels). Ein Wert über 100 % bedeutet stärker als "
                     "das typische Unternehmen bei diesem Kriterium."), STYLES["sub"]))
    if g(sc, "benchmark_to_be_sourced"):
        story.append(P(T(f"Note: a {safe(country)}-specific benchmark by stage is still to be sourced; "
                         "the Europe figure is used as a placeholder until it is.",
                         f"Hinweis: Ein Vergleichswert je Phase für {ve.country_in_text(country)} muss noch belegt "
                         "werden; bis dahin wird der Wert für Europa verwendet."), STYLES["sub"]))
    return story


def _method_value_header(label: str, value: str) -> list:
    return [P(label, STYLES["metric_label"]), P(value, STYLES["metric_value"])]


def _benchmark_source_note(source: str, label: Optional[str] = None) -> Optional[str]:
    label = label or T("EV/EBITDA multiple", "EV/EBITDA-Multiplikator")
    if source == "industry_global":
        return T(f"Note: no usable regional {label} — this industry's global figure is used.",
                 f"Hinweis: kein verwendbarer regionaler {label} — der weltweite Wert dieser Branche wird verwendet.")
    if source == "cross_industry":
        return T(f"Note: no usable {label} for this industry — the median across all industries is used.",
                 f"Hinweis: kein verwendbarer {label} für diese Branche — der Median aller Branchen wird verwendet.")
    return None


def _vc_page(vc: dict, output: Optional[dict] = None) -> list:
    story = [P(T("Venture Capital method", "Venture-Capital-Methode"), STYLES["h2"])]
    story += _method_value_header(T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(vc, "pre_money_valuation")))
    T_ = g(vc, "time_to_exit")
    r = g(vc, "target_return")
    story += _not_meaningful(g(vc, "not_meaningful_reason"))
    story += _counts_as_zero(g(vc, "no_value_reason"))
    if g(vc, "post_money_valuation") is not None:
        y, ebitda, mult, exit_v = (g(vc, "exit_year_label"), money(g(vc, "exit_year_ebitda")),
                                   multiple(g(vc, "ev_ebitda_multiple")), money(g(vc, "exit_value")))
        post, invest, pre = (money(g(vc, "post_money_valuation")), money(g(vc, "investment_amount")),
                             money(g(vc, "pre_money_valuation")))
        no_value = g(vc, "no_value_reason")
        story += _how(T(
            f"Projected EBITDA in the exit year ({y}) of {ebitda} × the industry EV/EBITDA multiple of {mult} gives an "
            f"exit value of {exit_v}. An investor who needs a {pct(r)} annual return values that today at {exit_v} ÷ "
            f"(1 + {pct(r)})^{T_} = {post} "
            + (f"after the investment. Minus the {invest} being raised = {pre} before it." if not no_value else
               f"after the investment. That is less than the {invest} being raised, so this method leaves no value "
               "before the investment and counts as €0 in the blend."),
            f"Das geplante EBITDA im Exit-Jahr ({y}) von {ebitda} × der EV/EBITDA-Multiplikator der Branche von {mult} "
            f"ergibt einen Exit-Wert von {exit_v}. Ein Investor, der {pct(r)} Rendite pro Jahr braucht, bewertet das "
            f"heute mit {exit_v} ÷ (1 + {pct(r)})^{T_} = {post} "
            + (f"nach der Investition. Abzüglich der eingeworbenen {invest} = {pre} davor." if not no_value else
               f"nach der Investition. Das ist weniger als die eingeworbenen {invest}; diese Methode lässt vor der "
               "Investition also keinen Wert übrig und zählt mit 0 € in der Gewichtung.")))
    story.append(P(T("Exit value", "Exit-Wert"), STYLES["h3"]))
    y = g(vc, "exit_year_label")
    story.append(info_table([
        (T(f"Exit-year ({y}) revenue", f"Umsatz im Exit-Jahr ({y})"), money(g(vc, "exit_year_revenue"))),
        (T(f"Exit-year ({y}) EBITDA", f"EBITDA im Exit-Jahr ({y})"), money(g(vc, "exit_year_ebitda"))),
        (T("EV/EBITDA multiple", "EV/EBITDA-Multiplikator"), multiple(g(vc, "ev_ebitda_multiple"))),
        # A non-positive exit value has no meaning; show a dash rather than a negative amount.
        (T("Exit value (enterprise value)", "Exit-Wert (Unternehmenswert)"),
         money(g(vc, "exit_value")) if (g(vc, "exit_value") or 0) > 0 else "—"),
        (T("Less: debt (assumed still outstanding at exit)", "Abzüglich Schulden (beim Exit noch offen angenommen)"),
         money(-(g(vc, "debt") or 0))),
        (T("Exit value for shareholders", "Exit-Wert für die Gesellschafter"),
         money(g(vc, "exit_equity_value")) if (g(vc, "exit_equity_value") or 0) > 0 else "—"),
    ]))
    note = _benchmark_source_note(g(vc, "ev_ebitda_multiple_source"))
    if note:
        story.append(P(note, STYLES["sub"]))
    story.append(P(T("Value today and the new investor's stake", "Wert heute und Anteil des neuen Investors"), STYLES["h3"]))
    story.append(info_table([
        (T("Years to exit", "Jahre bis zum Exit"), T_),
        (T("Investor's target annual return (for this stage)", "Zielrendite des Investors pro Jahr (für diese Phase)"),
         pct(r)),
        (T("Post-money valuation", "Post-Money-Bewertung"), money(g(vc, "post_money_valuation"))),
        (T("Investment", "Investition"), money(g(vc, "investment_amount"))),
        (T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(vc, "pre_money_valuation"))),
        (T("Investor ownership after the round (this method alone)",
           "Anteil des Investors nach der Runde (nur diese Methode)"), pct(g(vc, "ownership_fraction_investors"))),
        (T("Existing shareholders after the round (this method alone)",
           "Bisherige Gesellschafter nach der Runde (nur diese Methode)"), pct(g(vc, "ownership_fraction_entrepreneurs"))),
        (T("Existing shares (count)", "Bestehende Anteile (Anzahl)"), count(g(vc, "number_of_existing_shares", 0))),
        (T("New shares to issue (count)", "Neue Anteile (Anzahl)"), count(g(vc, "number_of_new_shares"))),
        (T("Price per new share", "Preis je neuem Anteil"), money2(g(vc, "price_per_share"))),
    ]))
    post = g(output or {}, "post_money_valuation")
    capital = g(output or {}, "capital_needed")
    if post and capital and g(vc, "ownership_fraction_investors") is not None:
        story.append(P(T(
            f"These shares and stakes are what this method alone implies. At the blended valuation used in the rest "
            f"of this report (post-money {money(post)}), the {money(capital)} being raised buys "
            f"{pct(capital / post)} of the company.",
            f"Diese Anteile ergeben sich aus dieser Methode allein. Beim gewichteten Wert, der im übrigen Bericht "
            f"verwendet wird (Post-Money {money(post)}), kaufen die eingeworbenen {money(capital)} "
            f"{pct(capital / post)} des Unternehmens."), STYLES["sub"]))
    return story


def _comparables_page(cm: dict, cp: dict) -> list:
    story = [P(T("Comparables method (revenue or EBITDA multiple)",
                 "Vergleichsmethode (Umsatz- oder EBITDA-Multiplikator)"), STYLES["h2"])]
    story += _method_value_header(T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(cm, "pre_money_valuation")))
    story += _not_meaningful(g(cm, "not_meaningful_reason"))
    arr = g(cm, "revenue_basis") == "arr"
    basis_used = g(cm, "basis_used")
    rev_label = (T("ARR (annual recurring revenue)", "ARR (jährlich wiederkehrender Umsatz)") if arr
                 else T("revenue, last 12 months", "Umsatz der letzten 12 Monate"))
    rev_mult_label = (T("SaaS ARR multiple (listed SaaS companies)", "SaaS-ARR-Multiplikator (börsennotierte SaaS-Firmen)")
                      if arr else T("EV/Sales multiple (trailing)", "EV/Umsatz-Multiplikator (letzte 12 Monate)"))
    if g(cm, "pre_money_valuation") is not None:
        ind = safe(g(cp, "industry"))
        ebitda_v, rev_v = money(g(cm, "ebitda_based_ev")), money(g(cm, "revenue_based_ev"))
        used_text = (T(f"Your EBITDA gives more ({ebitda_v} against {rev_v} on revenue), so EBITDA is used.",
                       f"Ihr EBITDA ergibt mehr ({ebitda_v} gegenüber {rev_v} über den Umsatz), deshalb wird das "
                       "EBITDA verwendet.")
                     if basis_used == "ebitda" else
                     T(f"Your {rev_label} gives more ({rev_v} against {ebitda_v} on EBITDA), so revenue is used: young "
                       "companies are compared on revenue until their profit is the bigger value driver.",
                       f"Ihr {rev_label} ergibt mehr ({rev_v} gegenüber {ebitda_v} über das EBITDA), deshalb wird der "
                       "Umsatz verwendet: Junge Unternehmen werden über den Umsatz verglichen, bis ihr Gewinn der "
                       "größere Werttreiber ist."))
        story += _how(T(
            f"Listed {ind} companies are valued at about {multiple(g(cm, 'ev_ebitda_multiple'))} their last-12-month "
            f"EBITDA and {multiple(g(cm, 'revenue_multiple'))} their "
            + ("annual recurring revenue (listed SaaS companies)" if arr else "revenue")
            + f". The method takes the higher of the two values. {used_text} A private company of this stage is "
            f"harder to sell than a listed one, so a {pct(g(cm, 'private_company_discount'))} discount gives an "
            f"enterprise value of {money(g(cm, 'enterprise_value'))}; minus debt and plus cash gives the equity value. "
            "This method looks at today's figures, not at your growth plan; the Venture Capital and DCF methods use the "
            "plan.",
            f"Börsennotierte Unternehmen der Branche {ind} werden mit etwa dem {multiple(g(cm, 'ev_ebitda_multiple'))} "
            f"ihres EBITDA der letzten 12 Monate und dem {multiple(g(cm, 'revenue_multiple'))} ihres "
            + ("jährlich wiederkehrenden Umsatzes (börsennotierte SaaS-Firmen)" if arr else "Umsatzes")
            + f" bewertet. Die Methode nimmt den höheren der beiden Werte. {used_text} Ein nicht börsennotiertes "
            f"Unternehmen dieser Phase ist schwerer zu verkaufen, deshalb ergibt ein Abschlag von "
            f"{pct(g(cm, 'private_company_discount'))} einen Unternehmenswert von {money(g(cm, 'enterprise_value'))}; "
            "abzüglich Schulden und zuzüglich liquider Mittel ergibt sich der Wert des Eigenkapitals. Diese Methode "
            "betrachtet die heutigen Zahlen, nicht Ihren Wachstumsplan; die Venture-Capital- und die DCF-Methode "
            "verwenden den Plan."))
    used = g(cm, "pre_money_valuation") is not None
    # When the method doesn't apply (e.g. no revenue), the inputs are shown but not
    # the meaningless results of multiplying them.
    shown = lambda v: v if used else "—"  # noqa: E731
    story.append(info_table([
        (T("EBITDA, last 12 months", "EBITDA, letzte 12 Monate"), money(g(cm, "trailing_ebitda"))),
        (T("× EV/EBITDA multiple (trailing)", "× EV/EBITDA-Multiplikator (letzte 12 Monate)"),
         multiple(g(cm, "ev_ebitda_multiple"))),
        (T("= Value on EBITDA (zero if EBITDA is negative)", "= Wert über das EBITDA (null bei negativem EBITDA)"),
         shown(money(g(cm, "ebitda_based_ev")))),
        (rev_label[:1].upper() + rev_label[1:], money(g(cm, "revenue_amount"))),
        ("× " + rev_mult_label, multiple(g(cm, "revenue_multiple"))),
        (T("= Value on revenue", "= Wert über den Umsatz"), shown(money(g(cm, "revenue_based_ev")))),
        (T("Higher of the two (public-company value)", "Höherer der beiden Werte (Börsenwert)"),
         shown(money(g(cm, "public_company_ev")))),
        (T("Private-company discount (for this stage)", "Abschlag für nicht börsennotierte Unternehmen (diese Phase)"),
         f"−{pct(g(cm, 'private_company_discount'))}"),
        (T("Enterprise value", "Unternehmenswert"), shown(money(g(cm, "enterprise_value")))),
        (T("Less: debt", "Abzüglich Schulden"), money(-(g(cm, "debt") or 0))),
        (T("Plus: cash", "Zuzüglich liquider Mittel"), money(g(cm, "cash"))),
        (T("Equity value (pre-money)", "Wert des Eigenkapitals (Pre-Money)"), money(g(cm, "equity_value"))),
    ]))
    note = _benchmark_source_note(g(cm, "ev_ebitda_multiple_source"))
    if note:
        story.append(P(note, STYLES["sub"]))
    if basis_used == "revenue":
        note = _benchmark_source_note(g(cm, "revenue_multiple_source"), T("EV/Sales multiple", "EV/Umsatz-Multiplikator"))
        if note:
            story.append(P(note, STYLES["sub"]))
    return story


def _dcf_page(dcf: dict, wacc: dict, years: list, german: Optional[dict] = None) -> list:
    story = [P(T("Discounted cash flow (DCF) method", "Discounted-Cashflow-Methode (DCF)"), STYLES["h2"])]
    story += _method_value_header(T("Pre-money valuation", "Pre-Money-Bewertung"), money(g(dcf, "pre_money_valuation")))
    story += _not_meaningful(g(dcf, "not_meaningful_reason"))
    story += _counts_as_zero(g(dcf, "no_value_reason"))
    r = g(dcf, "discount_rate")
    if g(dcf, "pre_money_valuation") is not None and not g(dcf, "no_value_reason"):
        ev, p_, risk_ev, pre = (money(g(dcf, "enterprise_value")), pct(g(dcf, "survival_probability")),
                                money(g(dcf, "risk_adjusted_enterprise_value")), money(g(dcf, "pre_money_valuation")))
        story += _how(T(
            f"Five years of projected free cash flow plus a terminal value for the years after, discounted at the "
            f"company's cost of capital ({pct2(r)}), are worth {ev} if the business keeps going. About {p_} of "
            f"companies at this stage survive, so the expected value is {risk_ev}; minus debt and plus cash gives {pre}.",
            f"Fünf Jahre geplanter freier Cashflow plus ein Endwert für die Jahre danach, abgezinst mit den "
            f"Kapitalkosten des Unternehmens ({pct2(r)}), sind {ev} wert, wenn das Geschäft weiterläuft. Etwa {p_} der "
            f"Unternehmen in dieser Phase überleben, der erwartete Wert ist also {risk_ev}; abzüglich Schulden und "
            f"zuzüglich liquider Mittel ergibt das {pre}."))

    headers = [""] + [y.get("year_label", "") for y in years]
    amt = amount_format([y.get(k) for y in years for k in ("ebit", "tax_on_ebit", "da", "capex", "change_in_working_capital",
                                                          "unlevered_fcf")])
    rows = [
        ["EBIT"] + [amt(y.get("ebit")) for y in years],
        *([[T("Tax rate (German schedule)", "Steuersatz (deutscher Verlauf)")] + [pct(y.get("tax_rate")) for y in years]]
          if german else []),
        [T("Tax on EBIT", "Steuern auf das EBIT")] + [amt(-y.get("tax_on_ebit", 0)) for y in years],
        [T("Add back D&A", "Zuzüglich Abschreibungen")] + [amt(y.get("da")) for y in years],
        [T("Capital expenditure", "Investitionen")] + [amt(-y.get("capex", 0)) for y in years],
        [T("Change in working capital", "Veränderung Working Capital")]
        + [amt(-y.get("change_in_working_capital", 0)) for y in years],
    ]
    total_row = [T("Free cash flow", "Freier Cashflow")] + [amt(v) for v in g(dcf, "unlevered_fcf_by_year", [])]
    n = max(len(years), 1)
    story.append(P(T("Unlevered free cash flow (negative numbers reduce cash)",
                     "Freier Cashflow vor Finanzierung (negative Zahlen verringern die liquiden Mittel)"), STYLES["h3"]))
    story.append(data_table(headers, rows, total_row=total_row,
                            col_widths=[CONTENT_W * 0.28] + [CONTENT_W * 0.72 / n] * n))
    story.append(Spacer(1, 8))

    tv_share = g(dcf, "terminal_value_share")
    no_value = g(dcf, "no_value_reason")
    # When the method isn't used, the values derived from the cash flows have no meaning: show dashes.
    used = g(dcf, "pre_money_valuation") is not None and not no_value
    # Half-width table: compact amounts from a billion euros up, so nothing wraps.
    big = amount_format([g(dcf, k) for k in ("pv_of_fcf", "pv_of_terminal_value", "enterprise_value",
                                             "risk_adjusted_enterprise_value", "debt", "cash", "equity_value")],
                        limit=1_000_000_000)
    shown = lambda v: big(v) if (used or no_value) else "—"  # noqa: E731
    value_rows = info_table([
        (T("PV of 5 years of free cash flow", "Barwert der Cashflows aus 5 Jahren"), shown(g(dcf, "pv_of_fcf"))),
        (T("PV of terminal value", "Barwert des Endwerts"), shown(g(dcf, "pv_of_terminal_value"))),
        (T("Enterprise value (if the business survives)", "Unternehmenswert (wenn das Geschäft überlebt)"),
         shown(g(dcf, "enterprise_value"))),
        (T("Terminal value share of that value", "Anteil des Endwerts daran"),
         "—" if not used else
         T("over 100% (Years 1-5 burn cash)", "über 100 % (Jahre 1-5 verbrauchen Geld)")
         if tv_share is not None and tv_share > 1 else pct(tv_share)),
        (T("Probability of survival (for this stage)", "Überlebenswahrscheinlichkeit (diese Phase)"),
         pct(g(dcf, "survival_probability"))),
        (T("Risk-adjusted enterprise value", "Risikobereinigter Unternehmenswert"),
         big(0) if no_value else shown(g(dcf, "risk_adjusted_enterprise_value"))),
        (T("Less: debt", "Abzüglich Schulden"), big(-(g(dcf, "debt") or 0))),
        (T("Plus: cash", "Zuzüglich liquider Mittel"), big(g(dcf, "cash"))),
        (T("Equity value (pre-money)", "Wert des Eigenkapitals (Pre-Money)"), big(g(dcf, "equity_value"))),
    ], col_widths=[HALF_W * 0.68, HALF_W * 0.32])
    rate_rows = info_table([
        (T("Risk-free rate (10-year Bund)", "Risikofreier Zins (10-jährige Bundesanleihe)") if german
         else T("Risk-free rate", "Risikofreier Zins"), pct2(g(wacc, "risk_free_rate"))),
        (T("Beta (industry)", "Beta (Branche)"), _n(g(wacc, "beta", 0), 2)),
        (T("Country equity risk premium", "Marktrisikoprämie des Landes"), pct2(g(wacc, "country_equity_risk_premium"))),
        (T("Cost of equity", "Eigenkapitalkosten"), pct2(g(wacc, "cost_of_equity"))),
        (T("Cost of debt after tax", "Fremdkapitalkosten nach Steuern"), pct2(g(wacc, "after_tax_cost_of_debt"))),
        (T("Equity / debt weights", "Gewichte Eigen- / Fremdkapital"),
         f"{pct(g(wacc, 'equity_pct_capital'))} / {pct(g(wacc, 'debt_pct_capital'))}"),
        (T("Discount rate (WACC)", "Diskontsatz (WACC)"), pct2(r)),
        (T("Long-run growth after Year 5", "Langfristiges Wachstum nach Jahr 5"), pct2(g(dcf, "perpetual_growth_rate"))),
        (T("Tax rate after Year 5 (also in WACC)", "Steuersatz nach Jahr 5 (auch im WACC)") if german
         else T("Tax rate", "Steuersatz"), pct2(g(dcf, "tax_rate"))),
    ], col_widths=[HALF_W * 0.62, HALF_W * 0.38])
    story.append(side_by_side([P(T("From cash flows to equity value", "Von den Cashflows zum Eigenkapitalwert"),
                                 STYLES["h3"]), value_rows],
                              [P(T("Discount rate", "Diskontsatz"), STYLES["h3"]), rate_rows]))
    if used:
        g_ = pct(g(dcf, "perpetual_growth_rate"))
        story.append(P(T(
            f"The terminal value treats the years after Year 5 as a business growing {g_} a year: Year-5 profit fully "
            "taxed, capex at least D&A and working capital growing at that rate"
            + (f", taxed at the long-run rate of {pct(g(dcf, 'tax_rate'))} because German corporate tax keeps falling "
               "after Year 5 under the enacted schedule" if german else "")
            + f" (starting cash flow {money(g(dcf, 'terminal_fcf'))}).",
            f"Der Endwert behandelt die Jahre nach Jahr 5 als ein Geschäft, das jährlich um {g_} wächst: Gewinn aus "
            "Jahr 5 voll versteuert, Investitionen mindestens in Höhe der Abschreibungen und Working Capital mit dieser "
            "Rate wachsend"
            + (f", versteuert mit dem langfristigen Satz von {pct(g(dcf, 'tax_rate'))}, weil die Körperschaftsteuer "
               "nach dem beschlossenen Verlauf auch nach Jahr 5 weiter sinkt" if german else "")
            + f" (Ausgangs-Cashflow {money(g(dcf, 'terminal_fcf'))})."), STYLES["sub"]))
    if g(dcf, "terminal_value_floor_applied"):
        story.append(P(T("Note: the discount rate is close to the long-run growth rate, so the terminal value uses "
                         "a minimum 2-point gap between them.",
                         "Hinweis: Der Diskontsatz liegt nahe an der langfristigen Wachstumsrate; der Endwert verwendet "
                         "deshalb einen Mindestabstand von 2 Prozentpunkten."), STYLES["sub"]))
    return story


def _de(d: dict, key: str) -> Optional[str]:
    """A source text in the report's language (German version stored under key + '_de')."""
    return (d.get(f"{key}_de") or d.get(key)) if _german() else d.get(key)


def _country_inputs_section(country: str, specific: dict, output: dict) -> list:
    """Germany: the Bund rate, the tax schedule year by year and the stage benchmarks, with sources."""
    story = [P(T(f"{esc(country)}-specific inputs", f"Besondere Angaben für {esc(ve.country_in_text(country))}"),
               STYLES["h3"])]
    wacc = g(output, "wacc", {})
    rf = specific.get("risk_free_rate")
    if rf:
        story.append(P(T(f"<b>Risk-free rate:</b> {pct2(g(wacc, 'risk_free_rate'))} (as of "
                         f"{esc(format_date(rf.get('as_of')))}). {esc(_de(rf, 'source'))}",
                         f"<b>Risikofreier Zins:</b> {pct2(g(wacc, 'risk_free_rate'))} (Stand "
                         f"{esc(format_date(rf.get('as_of')))}). {esc(_de(rf, 'source'))}"), STYLES["small"], raw=True))
        story.append(P(T(f"<b>Country risk:</b> {esc(country)} is rated Aaa, so its country risk premium is 0 and "
                         f"the equity risk premium is the mature-market premium of "
                         f"{pct2(g(wacc, 'country_equity_risk_premium'))}.",
                         f"<b>Länderrisiko:</b> {esc(ve.country_in_text(country))} hat das Rating Aaa; die "
                         f"Länderrisikoprämie ist daher 0 und die Marktrisikoprämie entspricht der Prämie reifer Märkte "
                         f"von {pct2(g(wacc, 'country_equity_risk_premium'))}."), STYLES["small"], raw=True))
        story.append(Spacer(1, 3))

    taxes = _german_taxes(output)
    tax = specific.get("tax")
    if taxes and tax:
        rows = [[str(y.get("year")), pct(y.get("corporate_tax")), pct2(y.get("solidarity_surcharge")),
                 pct2(y.get("trade_tax")), pct2(y.get("combined"))] for y in taxes.get("calendar_years", [])]
        story.append(P(T(f"Tax by calendar year, with a trade-tax Hebesatz of {_hebesatz_text(taxes)}. The last "
                         "year's rate also applies after Year 5 (terminal value) and in the WACC.",
                         f"Steuern je Kalenderjahr, mit einem Gewerbesteuer-Hebesatz von {_hebesatz_text(taxes)}. Der "
                         "Satz des letzten Jahres gilt auch nach Jahr 5 (Endwert) und im WACC."), STYLES["small"]))
        story.append(data_table([T("Year", "Jahr"), T("Corporate tax", "Körperschaftsteuer"),
                                 T("Solidarity surcharge", "Solidaritätszuschlag"), T("Trade tax", "Gewerbesteuer"),
                                 T("Combined", "Gesamt")], rows,
                                col_widths=[CONTENT_W * 0.12, CONTENT_W * 0.22, CONTENT_W * 0.24,
                                            CONTENT_W * 0.2, CONTENT_W * 0.22]))
        years = g(g(output, "projections", {}), "years", [])
        story.append(P(T("Projection years span two calendar years, so each uses the two years' rates weighted by "
                         "days: ", "Planjahre umfassen zwei Kalenderjahre; jedes verwendet die Sätze beider Jahre, "
                         "nach Tagen gewichtet: ")
                       + ", ".join(f"{y.get('year_label')} {pct2(y.get('tax_rate'))}" for y in years)
                       + ".", STYLES["small_note"]))
        for key in ("corporate_tax_source", "solidarity_surcharge_source", "trade_tax_base_rate_source",
                    "average_hebesatz_source", "simplification"):
            if tax.get(key):
                story.append(P(esc(_de(tax, key)), STYLES["small_note"]))
        story.append(Spacer(1, 3))
    elif tax:
        story.append(P(T("Tax: your own flat rate was used instead of the German schedule.",
                         "Steuern: Statt des deutschen Steuerverlaufs wurde Ihr eigener pauschaler Satz verwendet."),
                       STYLES["small"]))

    sb = specific.get("stage_benchmarks")
    if sb:
        flag = (T(" <b>The German figures are still to be sourced; these are placeholders.</b>",
                  " <b>Die deutschen Werte müssen noch belegt werden; dies sind Platzhalter.</b>")
                if any(r.get("to_be_sourced") for r in sb.get("stages", {}).values()) else "")
        story.append(P(T(f"<b>Scorecard benchmark (Idea and Development stages):</b> {esc(_de(sb, 'source'))}{flag}",
                         f"<b>Scorecard-Vergleichswert (Ideen- und Entwicklungsphase):</b> {esc(_de(sb, 'source'))}{flag}"),
                       STYLES["small"], raw=True))
    return story


# Source sentences that concern one particular country or industry: shown only to that company.
# Words that mark such a sentence -> the country or industry it is about.
_SPECIFIC_SOURCE_SENTENCES = {"Russia": "Russia", "Russland": "Russia", "Retail (Online)": "Retail (Online)"}


def _relevant_sentences(text: str, country: Optional[str], industry: Optional[str]) -> str:
    keep = []
    # A full stop after a digit is a German date ("1. Januar"), not the end of a sentence.
    for sentence in re.split(r"(?<=[.;])(?<!\d\.)\s+(?=[A-ZÄÖÜ'])", text):
        about = next((subject for word, subject in _SPECIFIC_SOURCE_SENTENCES.items() if word in sentence), None)
        if about is None or about in (country, industry):
            keep.append(sentence)
    return " ".join(keep)


def _methodology_page(sources: dict, stage_params: Optional[dict], stage: Optional[str],
                      country: Optional[str] = None, country_inputs: Optional[dict] = None,
                      output: Optional[dict] = None, industry: Optional[str] = None) -> list:
    story = [P(T("Methods, data sources & disclaimer", "Methoden, Datenquellen & Haftungsausschluss"), STYLES["h2"])]
    story.append(P(T("Methods", "Methoden"), STYLES["h3"]))
    for text in (
        T("<b>Scorecard</b> (Bill Payne): compares a pre-revenue company with the typical pre-revenue company in "
          "its region on six weighted criteria. Used only at the Idea and Development stages.",
          "<b>Scorecard</b> (Bill Payne): vergleicht ein Unternehmen vor Umsatzbeginn anhand von sechs gewichteten "
          "Kriterien mit dem typischen Unternehmen vor Umsatzbeginn in seiner Region. Nur in der Ideen- und "
          "Entwicklungsphase."),
        T("<b>Venture Capital</b>: values a projected exit and discounts it at the annual return an investor at "
          "this stage targets. This is the only place a target return is used.",
          "<b>Venture Capital</b>: bewertet einen geplanten Exit und zinst ihn mit der Jahresrendite ab, die ein "
          "Investor in dieser Phase anstrebt. Nur hier wird eine Zielrendite verwendet."),
        T("<b>Comparables</b>: applies public-company multiples to the last 12 months, taking the higher of EBITDA × "
          "EV/EBITDA and revenue × EV/Sales (ARR × the SaaS ARR multiple for subscription companies), with a "
          "private-company discount, then subtracts debt and adds cash.",
          "<b>Vergleichsmethode</b>: wendet Multiplikatoren börsennotierter Unternehmen auf die letzten 12 Monate an und "
          "nimmt den höheren Wert aus EBITDA × EV/EBITDA und Umsatz × EV/Umsatz (bei Abo-Unternehmen ARR × "
          "SaaS-ARR-Multiplikator), mit einem Abschlag für nicht börsennotierte Unternehmen; danach werden Schulden "
          "abgezogen und liquide Mittel addiert."),
        T("<b>Discounted cash flow</b>: discounts projected free cash flow and a terminal value at the cost of "
          "capital (WACC), weights the result by the probability of survival, then subtracts debt and adds cash.",
          "<b>Discounted Cashflow</b>: zinst geplante freie Cashflows und einen Endwert mit den Kapitalkosten (WACC) "
          "ab, gewichtet das Ergebnis mit der Überlebenswahrscheinlichkeit, zieht Schulden ab und addiert liquide "
          "Mittel."),
        T("The four results are blended with weights for the company's stage. A method that doesn't apply to the "
          "company (for example Comparables without revenue) is left out and the other weights are scaled "
          "up; a method that applies but finds no value for the shares counts as €0, so a weaker plan never raises "
          "the result.",
          "Die vier Ergebnisse werden mit Gewichten für die Phase des Unternehmens kombiniert. Eine Methode, die nicht "
          "zum Unternehmen passt (etwa die Vergleichsmethode ohne Umsatz), entfällt und die anderen Gewichte werden "
          "hochskaliert; eine Methode, die passt, aber keinen Wert für die Anteile findet, zählt mit 0 €, sodass ein "
          "schwächerer Plan das Ergebnis nie erhöht."),
        T("<b>Round logic</b> (cross-check, not in the blend): the pre-money at which the amount raised buys the "
          "share of the company typically sold in a round at this stage.",
          "<b>Rundenlogik</b> (Gegenprobe, nicht in der Gewichtung): die Pre-Money-Bewertung, bei der der eingeworbene "
          "Betrag den Anteil kauft, der in einer Runde dieser Phase typischerweise verkauft wird."),
    ):
        story.append(P(text, STYLES["small"], raw=True))
        story.append(Spacer(1, 2))

    if stage_params:
        story.append(P(T(f"Assumptions for the {safe(stage).lower()}", f"Annahmen für die {ve.stage_label(stage or '')}"),
                       STYLES["h3"]))
        story.append(info_table([
            (T("Investor's target annual return (VC method)", "Zielrendite des Investors pro Jahr (VC-Methode)"),
             pct(stage_params.get("vc_target_return"))),
            (T("Private-company discount (Comparables)", "Abschlag für nicht börsennotierte Unternehmen (Vergleich)"),
             pct(stage_params.get("private_company_discount"))),
            (T("Probability of survival (DCF)", "Überlebenswahrscheinlichkeit (DCF)"),
             pct(stage_params.get("survival_probability"))),
        ]))

    if country_inputs:
        story += _country_inputs_section(country or "", country_inputs, output or {})

    story.append(P(T("Data sources", "Datenquellen"), STYLES["h3"]))
    rf_other = country_inputs and "risk_free_rate" in country_inputs
    labels = [
        ("industry_benchmarks", T("Industry benchmarks", "Branchenvergleichswerte")),
        ("ev_sales_multiple", T("Revenue multiple", "Umsatz-Multiplikator")),
        ("saas_arr_multiple", T("SaaS ARR multiple", "SaaS-ARR-Multiplikator")),
        ("country_data", T("Country risk and tax", "Länderrisiko und Steuern")),
        ("risk_free_rate", T("Risk-free rate (other countries)", "Risikofreier Zins (andere Länder)") if rf_other
         else T("Risk-free rate", "Risikofreier Zins")),
        ("perpetual_growth_rate", T("Long-run growth", "Langfristiges Wachstum")),
        ("vc_target_return", T("VC target returns", "Zielrenditen VC")),
        ("private_company_discount", T("Private-company discount", "Abschlag für nicht börsennotierte Unternehmen")),
        ("survival_probability", T("Survival probability", "Überlebenswahrscheinlichkeit")),
        ("scorecard_benchmark", T("Scorecard benchmark", "Scorecard-Vergleichswert")),
        ("scorecard", T("Scorecard method", "Scorecard-Methode")),
        ("method_weights", T("Method weights", "Gewichte der Methoden")),
        ("round_benchmarks", T("Round logic", "Rundenlogik")),
    ]
    comparables = g(output or {}, "comparables", {}) or {}
    used_basis = comparables.get("basis_used") if comparables.get("pre_money_valuation") is not None else None
    shown = {"ev_sales_multiple": used_basis == "revenue", "saas_arr_multiple": used_basis == "arr"}
    for key, label in labels:
        if sources.get(key) and shown.get(key, True):  # a multiple's source only where it was used
            story.append(P(f"<b>{esc(label)}:</b> {esc(_relevant_sentences(sources[key], country, industry))}",
                           STYLES["small"], raw=True))
            story.append(Spacer(1, 2))

    story.append(Spacer(1, 10))
    disclaimer_box = Table([[P(T(
        "<b>Disclaimer:</b> This tool is not a licensed financial advisor, investment advisor, or "
        "legal entity authorized to provide financial, investment, or legal advice. This report is "
        "intended for informational purposes only and should not be construed as a solicitation or "
        "recommendation for any financial transaction. It is not a valuation under IDW S1 or the German valuation "
        "law (Bewertungsgesetz) and can't be used for tax purposes, share transfers between shareholders or "
        "employee share plans (VSOP/ESOP). All valuations are based on information and "
        "assumptions believed to be accurate at the time of preparation, but no guarantee is made "
        "regarding completeness, accuracy, or future performance. Consult a qualified financial, "
        "legal, or investment professional before making decisions based on this analysis. "
        f"{BRAND} by {AUTHOR}.",
        "<b>Haftungsausschluss:</b> Dieses Tool ist kein zugelassener Finanz- oder Anlageberater und keine Stelle, "
        "die Finanz-, Anlage- oder Rechtsberatung erbringen darf. Dieser Bericht dient nur der Information und ist "
        "keine Aufforderung oder Empfehlung zu einer Finanztransaktion. Er ist keine Bewertung nach IDW S1 oder dem "
        "Bewertungsgesetz und kann nicht für steuerliche Zwecke, Anteilsübertragungen zwischen Gesellschaftern oder "
        "Mitarbeiterbeteiligungen (VSOP/ESOP) verwendet werden. Alle Bewertungen beruhen auf Angaben und Annahmen, "
        "die bei der Erstellung für zutreffend gehalten wurden; für Vollständigkeit, Richtigkeit oder künftige "
        "Entwicklung wird keine Gewähr übernommen. Ziehen Sie vor Entscheidungen auf Basis dieser Analyse "
        "qualifizierte Finanz-, Rechts- oder Anlageberatung hinzu. "
        f"{BRAND} von {AUTHOR}."),
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

    company_name = g(cp, "company_name") or T("Untitled company", "Unbenanntes Unternehmen")
    date_str = format_date(g(output, "valuation_date"))
    data_date = format_date(sources.get("data_as_of")) if sources.get("data_as_of") else T("January 2026", "Januar 2026")
    logo_bytes = _decode_logo(g(cp, "logo_base64"))

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=MARGIN_SIDE, rightMargin=MARGIN_SIDE,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
        title=f"{company_name} - {T('Valuation Report', 'Bewertungsbericht')} ({BRAND})",
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
    comparables_mv = g(output, "method_values", {}).get("comparables") or {}
    if comparables_mv.get("status") != "not_meaningful":  # else the Valuation page gives the reason in one line
        story += _comparables_page(g(output, "comparables", {}), cp)
        story.append(PageBreak())
    story += _dcf_page(g(output, "dcf", {}), g(output, "wacc", {}), years, _german_taxes(output))
    story.append(PageBreak())
    story += _methodology_page(sources, stage_params, g(cp, "company_stage"), g(cp, "country"),
                               country_inputs, output, g(cp, "industry"))

    doc.build(story)
    return buffer.getvalue()
