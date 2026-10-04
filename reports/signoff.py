"""One-page audit sign-off sheet (A4 portrait, ReportLab canvas).

Built from the stored audit snapshot (Audit, AuditLine, Observation, FollowUp) and core.calc.
Nothing here computes business numbers: totals, variance, status, comparison, change in value,
next steps and the "Other (N more)" row all come from core.calc.

Everything is drawn on a single page. Each block gets a height budget and shrinks its rows,
font size or number of items to fit; what does not fit is replaced by "+ N more in the portal".
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

from audits import services
from core import calc
from core.exports import pdf_fonts
from core.formatting import fmt_date, inr, pct, plain_pct, qty

# ---------------------------------------------------------------- design tokens (static/css/app.css, light)
INK = colors.HexColor("#12180F")
MUTED = colors.HexColor("#5A6556")
LINE = colors.HexColor("#DADFD2")
RAISE = colors.HexColor("#F7F9F3")
BG = colors.HexColor("#F3F5EF")
SIDEBAR = colors.HexColor("#12170F")
LIME = colors.HexColor("#B9E10C")
LIME_INK = colors.HexColor("#5F7A00")
SHORT = colors.HexColor("#C23B22")
EXCESS = colors.HexColor("#1F6FB5")
GOOD = colors.HexColor("#1B7F52")
WARN = colors.HexColor("#9A6200")
TONE = {"good": GOOD, "warn": WARN, "bad": SHORT, "neutral": MUTED}
STATUS_TONE = {"Healthy": GOOD, "Watch": WARN, "Review": SHORT}

PAGE_W, PAGE_H = A4
MM = 72 / 25.4
M = 26  # page margin
W = PAGE_W - 2 * M
ELLIPSIS = "…"


# ---------------------------------------------------------------- data


@dataclass
class SheetData:
    audit: object
    client: object
    store: object
    totals: calc.Numbers
    lines: list
    thresholds: calc.Thresholds
    previous: object = None
    comparison: object = None
    rows: list = field(default_factory=list)
    total_row: object = None
    observations: list = field(default_factory=list)
    next_steps: list = field(default_factory=list)
    followup_line: str = ""
    sale_line: str = ""
    portal_url: str = ""
    draft: bool = False


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_data(audit, portal_url: str) -> SheetData:
    th = audit.client.thresholds()
    previous = services.audit_previous(audit)
    lines = audit.numbers_by_category()
    totals = audit.totals  # stored snapshot
    data = SheetData(audit=audit, client=audit.client, store=audit.store, totals=totals, lines=lines,
                     thresholds=th, previous=previous, portal_url=portal_url, draft=not audit.is_published)
    obs = list(audit.observations.select_related("category"))
    data.observations = sorted(obs, key=lambda o: (SEVERITY_ORDER.get(o.severity, 3), o.sort_order))
    this_recs = [f"{o.category_label}: {o.recommendation}" for o in obs
                 if o.recommendation.strip() and o.kind != calc.GOOD_PRACTICE]
    if previous is not None:
        data.comparison = services.comparison_for(previous, audit)
        prev_lines = data.comparison.a.lines
        data.rows = calc.sheet_categories(lines, prev_lines)
        data.total_row = calc.sheet_total(lines, data.comparison.a.totals)
        data.next_steps = calc.improvement_points(data.comparison.a, data.comparison.b, th)
        counts = {"done": 0, "in_progress": 0, "not_done": 0}
        for fu in audit.followups.all():
            counts[fu.status] = counts.get(fu.status, 0) + 1
        if sum(counts.values()):
            data.followup_line = (f"Last audit's recommendations: {counts['done']} done, "
                                  f"{counts['in_progress']} in progress, {counts['not_done']} not done")
    else:
        data.rows = calc.sheet_categories(lines, None)
        data.total_row = calc.sheet_total(lines)
        data.next_steps = list(dict.fromkeys(this_recs))
    if calc.has_sale(audit.sale_value):
        if previous is not None:
            data.sale_line = (f"Sale value {inr(audit.sale_value)} ({fmt_date(previous.audit_date)} to "
                              f"{fmt_date(audit.audit_date)})")
        else:
            data.sale_line = f"Sale value {inr(audit.sale_value)}"
    else:
        data.sale_line = "Sale value not given for this audit"
    return data


# ---------------------------------------------------------------- text helpers


class Pen:
    def __init__(self, c):
        self.c = c
        self.reg, self.bold, _ = pdf_fonts()

    def font(self, bold=False):
        return self.bold if bold else self.reg

    def width(self, text, size, bold=False):
        return stringWidth(str(text), self.font(bold), size)

    def fit(self, text, size, width, bold=False):
        """Truncate with an ellipsis so the text fits the width."""
        text = str(text)
        if self.width(text, size, bold) <= width:
            return text
        while text and self.width(text + ELLIPSIS, size, bold) > width:
            text = text[:-1]
        return text.rstrip() + ELLIPSIS

    def wrap(self, text, size, width, bold=False):
        words, lines, cur = str(text).split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if self.width(trial, size, bold) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            while self.width(w, size, bold) > width:  # one very long word
                cut = len(w)
                while cut > 1 and self.width(w[:cut], size, bold) > width:
                    cut -= 1
                lines.append(w[:cut])
                w = w[cut:]
            cur = w
        if cur:
            lines.append(cur)
        return lines

    def clip(self, lines, max_lines, size, width, bold=False):
        if len(lines) <= max_lines:
            return lines
        kept = lines[:max_lines]
        last = kept[-1]
        while last and self.width(last + "...", size, bold) > width:
            last = last[:-1]
        kept[-1] = last.rstrip() + "..."
        return kept

    def text(self, x, y, text, size, bold=False, color=INK, align="left"):
        c = self.c
        c.setFont(self.font(bold), size)
        c.setFillColor(color)
        if align == "right":
            c.drawRightString(x, y, str(text))
        elif align == "center":
            c.drawCentredString(x, y, str(text))
        else:
            c.drawString(x, y, str(text))


def box(c, x, y_top, w, h, fill=None, stroke=LINE, radius=4):
    c.setStrokeColor(stroke)
    c.setLineWidth(0.6)
    if fill is not None:
        c.setFillColor(fill)
    c.roundRect(x, y_top - h, w, h, radius, stroke=1 if stroke is not None else 0, fill=1 if fill is not None else 0)


def pill(p: Pen, x, y, label, tone, size=6.5):
    """Status pill; returns its width. y is the text baseline."""
    w = p.width(label, size, True) + 14
    c = p.c
    c.setFillColor(tone)
    c.setFillAlpha(0.14)
    c.roundRect(x, y - 2.6, w, size + 4.2, (size + 4.2) / 2, stroke=0, fill=1)
    c.setFillAlpha(1)
    c.circle(x + 5, y + size * 0.36, 1.7, stroke=0, fill=1)
    p.text(x + 9, y, label, size, True, tone)
    return w


def xmark(c, x, y, h, dark=colors.white):
    s = h / 100.0

    def poly(pts, col):
        c.setFillColor(col)
        path = c.beginPath()
        path.moveTo(x + pts[0][0] * s, y + (100 - pts[0][1]) * s)
        for px, py in pts[1:]:
            path.lineTo(x + px * s, y + (100 - py) * s)
        path.close()
        c.drawPath(path, stroke=0, fill=1)

    poly([(0, 0), (34, 0), (68, 50), (34, 100), (0, 100), (34, 50)], LIME)
    poly([(113, 0), (79, 0), (45, 50), (79, 100), (113, 100), (79, 50)], dark)
    return 113 * s


# ---------------------------------------------------------------- blocks


def draw_header(p: Pen, d: SheetData, y):
    c, h = p.c, 58
    c.setFillColor(SIDEBAR)
    c.roundRect(M, y - h, W, h, 6, stroke=0, fill=1)
    x = M + 14
    p.text(x, y - 27, "Audi", 20, True, colors.white)
    xw = x + p.width("Audi", 20, True) + 0.5
    xw += xmark(c, xw, y - 27.5, 14.5) + 5
    p.text(xw, y - 27, "Vault", 14, False, colors.HexColor("#8E9B88"))
    p.text(x, y - 44, "Where Audits Meet ", 8.5, False, colors.white)
    p.text(x + p.width("Where Audits Meet ", 8.5), y - 44, "Exceptionalism", 8.5, True, LIME)
    right = M + W - 14
    p.text(right, y - 22, "Audit sign-off sheet", 14, True, colors.white, "right")
    ref = d.audit.reference
    p.text(right, y - 36, f"Reference {ref}", 8.5, False, colors.HexColor("#E6EBE0"), "right")
    p.text(right, y - 48, f"Sheet ID SO-{ref.removeprefix('AUD-')}", 8.5, False, colors.HexColor("#8E9B88"), "right")
    return y - h


def draw_name_boxes(p: Pen, d: SheetData, y):
    h, gap = 44, 6
    w = (W - 3 * gap) / 4
    a = d.audit
    store = f"{d.store.code} · {d.store.name}" + (f", {d.store.city}" if d.store.city else "")
    items = [
        ("Company", "Audix Solutions & Co", []),
        ("Client", d.client.name, []),
        ("Store", store, []),
        ("Audit date", fmt_date(a.audit_date), [a.get_audit_type_display(), f"{a.get_shift_display()} shift"]),
    ]
    for i, (label, value, subs) in enumerate(items):
        x = M + i * (w + gap)
        inner = w - 14
        box(p.c, x, y, w, h, fill=colors.white)
        p.text(x + 7, y - 11, label, 6.5, False, MUTED)
        lines = p.clip(p.wrap(value, 8.3, inner, True), 1 if subs else 2, 8.3, inner, True)
        for k, line in enumerate(lines):
            p.text(x + 7, y - 23 - k * 10, line, 8.3, True)
        for k, sub in enumerate(subs[:2]):
            p.text(x + 7, y - 32 - k * 7.6, p.fit(sub, 6.3, inner), 6.3, False, MUTED)
    return y - h


def draw_kpis(p: Pen, d: SheetData, y):
    h, gap = 48, 6
    w = (W - 3 * gap) / 4
    t, a = d.totals, d.audit
    var_sub = "of stock value"
    if a.var_pct_sale is not None:
        var_sub += f" · {pct(a.var_pct_sale)} of sale value"
    tiles = [
        ("Stock value", inr(t.stock_value), f"{qty(t.stock_qty)} units", None),
        ("Total physical value", inr(t.total_physical_value), f"{qty(t.total_physical_qty)} units", None),
        ("Difference", inr(t.diff_value), f"{qty(t.diff_qty)} units", SHORT if t.diff_value < 0 else (EXCESS if t.diff_value > 0 else INK)),
        ("Variance % of stock value", pct(a.var_pct_stock), var_sub, None),
    ]
    for i, (label, value, sub, col) in enumerate(tiles):
        x = M + i * (w + gap)
        box(p.c, x, y, w, h, fill=RAISE)
        p.text(x + 7, y - 11, label, 6.5, False, MUTED)
        if i == 3:
            p.text(x + 7, y - 24.5, p.fit(value, 12.5, w * 0.5, True), 12.5, True, col or INK)
            pill(p, x + w - 7 - p.width(a.status_label, 6.5, True) - 14, y - 23, a.status_label,
                 STATUS_TONE.get(a.status_label, MUTED))
            for k, line in enumerate(p.clip(p.wrap(sub, 6.2, w - 14), 2, 6.2, w - 14)):
                p.text(x + 7, y - 34.5 - k * 7.4, line, 6.2, False, MUTED)
            continue
        p.text(x + 7, y - 26, p.fit(value, 12.5, w - 14, True), 12.5, True, col or INK)
        p.text(x + 7, y - 40, p.fit(sub, 6.5, w - 14), 6.5, False, MUTED)
    return y - h


def draw_summary_and_side(p: Pen, d: SheetData, y):
    h = 130
    gap = 8
    lw = W * 0.47
    rw = W - lw - gap
    c, t = p.c, d.totals
    # store summary
    box(c, M, y, lw, h, fill=colors.white)
    p.text(M + 8, y - 13, "Store summary", 8.5, True)
    qx, vx = M + lw - 92, M + lw - 8
    p.text(qx, y - 25, "Quantity", 6.5, False, MUTED, "right")
    p.text(vx, y - 25, "Value", 6.5, False, MUTED, "right")
    rows = [
        ("Stock", t.stock_qty, t.stock_value, False),
        ("Physical (good stock)", t.physical_qty, t.physical_value, False),
        ("Damage", t.damage_qty, t.damage_value, False),
        ("WBC (Without Barcode)", t.wbc_qty, t.wbc_value, False),
        ("Total physical", t.total_physical_qty, t.total_physical_value, True),
        ("Difference", t.diff_qty, t.diff_value, True),
    ]
    ry = y - 37
    for label, q, v, bold in rows:
        col = INK
        if label == "Difference":
            col = SHORT if v < 0 else (EXCESS if v > 0 else INK)
        p.text(M + 8, ry, label, 7.2, bold)
        p.text(qx, ry, qty(q), 7.2, bold, col, "right")
        p.text(vx, ry, inr(v), 7.2, bold, col, "right")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        c.line(M + 8, ry - 3.5, M + lw - 8, ry - 3.5)
        ry -= 12.5
    p.text(M + 8, ry - 2, p.fit(d.sale_line, 7, lw - 16), 7, False, MUTED)

    # right box
    x = M + lw + gap
    box(c, x, y, rw, h, fill=colors.white)
    if d.comparison is not None:
        draw_compare(p, d, x, y, rw)
    else:
        draw_glance(p, d, x, y, rw)
    return y - h


COMPARE_LABELS = ["Net variance", "Difference, units", "Variance % of stock value", "Variance % of sale value", "Damage",
                  "WBC (Without Barcode)"]


def draw_compare(p: Pen, d: SheetData, x, y, w):
    cmp = d.comparison
    p.text(x + 8, y - 13, "Compared with last audit", 8.5, True)
    p.text(x + w - 8, y - 13, f"Last audit {fmt_date(d.previous.audit_date)}", 6.5, False, MUTED, "right")
    both_sale = calc.has_sale(cmp.a.sale_value) and calc.has_sale(cmp.b.sale_value)
    rows = []
    for r in cmp.overall:
        if r.label in COMPARE_LABELS or r.label.startswith("Categories above"):
            if r.label == "Variance % of sale value" and not both_sale:
                continue
            rows.append(r)
    c1, c2, c3 = x + w * 0.555, x + w * 0.785, x + w - 8
    p.text(c1, y - 25, "Last", 6.5, False, MUTED, "right")
    p.text(c2, y - 25, "This audit", 6.5, False, MUTED, "right")
    p.text(c3, y - 25, "Change", 6.5, False, MUTED, "right")
    ry = y - 36
    size = 6.4
    step = 11 if len(rows) > 6 else 12
    for r in rows:
        label = "Variance % of stock" if r.label == "Variance % of stock value" else (
            "Variance % of sale" if r.label == "Variance % of sale value" else r.label)
        p.text(x + 8, ry, p.fit(label, size, w * 0.28), size)
        p.text(c1, ry, p.fit(r.a, size, w * 0.25), size, False, INK, "right")
        p.text(c2, ry, p.fit(r.b, size, w * 0.22), size, False, INK, "right")
        p.text(c3, ry, p.fit(r.change.text, size, w * 0.19), size, True, TONE.get(r.change.tone, MUTED), "right")
        p.c.setStrokeColor(LINE)
        p.c.setLineWidth(0.4)
        p.c.line(x + 8, ry - 3.5, x + w - 8, ry - 3.5)
        ry -= step
    sentence = next((s for s in cmp.sentences if s.startswith("Net variance is")), None)
    if sentence is None:
        sentence = "Net variance is about the same as at the last audit."
    for i, line in enumerate(p.clip(p.wrap(sentence, 6.8, w - 16), 2, 6.8, w - 16)):
        p.text(x + 8, ry - 2 - i * 8.6, line, 6.8, True)


def draw_glance(p: Pen, d: SheetData, x, y, w):
    t, a, th = d.totals, d.audit, d.thresholds
    p.text(x + 8, y - 13, "This audit at a glance", 8.5, True)
    p.text(x + w - 8, y - 13, "First audit for this store", 6.5, False, MUTED, "right")
    worst = calc.largest_shortage(d.lines)
    above = [n.category for n in d.lines if abs(n.var_pct) > th.warn_pct]
    size, value_w = 6.8, w * 0.66
    worst_text = "None"
    if worst:
        tail = f", {qty(worst.diff_qty, signed=True)} units, {inr(worst.diff_value)} ({pct(worst.var_pct)})"
        name_w = max(value_w - p.width(tail, size, True), 30)
        worst_text = p.fit(worst.category, size, name_w, True) + tail
    rows = [
        ("Largest shortage", worst_text),
        ("Difference", f"{qty(t.diff_qty, signed=True)} units, {inr(t.diff_value)} ({pct(t.var_pct)})"),
        ("Damage", f"{qty(t.damage_qty)} units, {inr(t.damage_value)} ({pct(t.damage_pct)})"),
        ("WBC (Without Barcode)", f"{qty(t.wbc_qty)} units, {inr(t.wbc_value)} ({pct(t.wbc_pct)})"),
        (f"Categories above {plain_pct(th.warn_pct)}", f"{len(above)} of {len(d.lines)}"),
        ("Audit type and shift", f"{a.get_audit_type_display()}, {a.get_shift_display()}"),
    ]
    ry = y - 30
    for label, value in rows:
        p.text(x + 8, ry, label, size, False, MUTED)
        p.text(x + w - 8, ry, p.fit(value, size, value_w, True), size, True, INK, "right")
        p.c.setStrokeColor(LINE)
        p.c.setLineWidth(0.4)
        p.c.line(x + 8, ry - 3.5, x + w - 8, ry - 3.5)
        ry -= 14
    p.text(x + 8, ry - 1, "Percentages are of stock value.", 6.3, False, MUTED)


# ---------------------------------------------------------------- category table


ROW_MAX, ROW_MIN = 5.2 * MM, 3.4 * MM  # row height range
FONT_MAX, FONT_MIN = 7.0, 6.0
CAT_TITLE_H = 13  # title and caption line
CAT_HEAD_H = 20  # two header rows
CATEGORY_CAPTION = "Quantity and value, amounts in rupees. Total physical = Physical + Damage + WBC."


def category_layout(n_rows: int, budget: float):
    """(row_height, font_size) for n category rows plus the Total row within the height budget.
    Rows shrink from 5.2 mm to 3.4 mm and the font from 7.0 to 6.0 pt as the number of rows grows."""
    row_h = (budget - CAT_TITLE_H - CAT_HEAD_H - 4) / max(n_rows + 1, 1)
    row_h = max(ROW_MIN, min(ROW_MAX, row_h))
    size = FONT_MIN + (FONT_MAX - FONT_MIN) * (row_h - ROW_MIN) / (ROW_MAX - ROW_MIN)
    return row_h, round(size, 2)


def category_height(n_rows: int, row_h: float) -> float:
    return CAT_TITLE_H + CAT_HEAD_H + (n_rows + 1) * row_h + 4


def draw_categories(p: Pen, d: SheetData, y, budget):
    rows = d.rows
    row_h, size = category_layout(len(rows), budget)
    p.text(M, y - 9, "Category-wise summary", 8.5, True)
    p.text(M + W, y - 9, CATEGORY_CAPTION, 6.3, False, MUTED, "right")
    draw_category_table(p, d, M, y - CAT_TITLE_H, W, row_h, size)
    return y - category_height(len(rows), row_h)


def _diff_colour(value):
    return SHORT if value < 0 else (EXCESS if value > 0 else INK)


def draw_category_table(p: Pen, d: SheetData, x, top, w, row_h, size):
    """One full-width table: Stock, Total physical and Difference as quantity and value, Var %, then
    Change vs last audit when there is a previous audit, or a small Var % bar for a first audit."""
    c = p.c
    has_prev = d.comparison is not None
    body = list(d.rows) + [d.total_row]

    def cells(r):
        n = r.numbers
        out = [qty(n.stock_qty), inr(n.stock_value), qty(n.total_physical_qty), inr(n.total_physical_value),
               qty(n.diff_qty, signed=True), inr(n.diff_value), pct(n.var_pct)]
        if has_prev:
            out.append(r.change.text if r.change else "New")
        return out

    table = [cells(r) for r in body]
    sub_heads = ["Qty", "Value", "Qty", "Value", "Qty", "Value", "Var %"] + (["vs last audit"] if has_prev else [])
    max_var = max([abs(r.numbers.var_pct) for r in d.rows] + [Decimal("0.5")])
    pad = 8
    while True:
        hsize = size - 0.6
        widths = [max(p.width(h, hsize), *(p.width(row[i], size, True) for row in table)) + pad
                  for i, h in enumerate(sub_heads)]
        bar_w = 0 if has_prev else 64
        name_w = w - sum(widths) - bar_w - 6
        need = max([62] + [p.width(r.name, size, True) + 8 for r in body if r.is_other])
        if name_w >= need or size <= 5.2:
            break
        size = round(size - 0.2, 2)
    # column right edges
    rights, cx = [], x + 4 + name_w
    for wd in widths:
        cx += wd
        rights.append(cx)
    lefts = [r - wd for r, wd in zip(rights, widths, strict=True)]

    # header: group row and sub row
    head_top = top
    c.setFillColor(RAISE)
    c.rect(x, head_top - CAT_HEAD_H, w, CAT_HEAD_H, stroke=0, fill=1)
    hsize = size - 0.6
    g_y, s_y = head_top - 8, head_top - 17
    groups = [("Stock", 0, 1), ("Total physical", 2, 3), ("Difference", 4, 5)]
    if has_prev:
        groups.append(("Change", 7, 7))
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    for label, i0, i1 in groups:
        gx0, gx1 = lefts[i0] + 3, rights[i1]
        p.text((gx0 + gx1) / 2, g_y, label, hsize + 0.3, True, INK, "center")
        c.line(gx0, g_y - 2.6, gx1, g_y - 2.6)
    p.text(x + 4, s_y, "Category", hsize, False, MUTED)
    for i, h in enumerate(sub_heads):
        p.text(rights[i], s_y, h, hsize, False, MUTED, "right")
    bar_x = rights[-1] + 10
    if bar_w:
        p.text(bar_x, s_y, "Var % bar", hsize, False, MUTED)

    ry = head_top - CAT_HEAD_H - row_h
    for r, row in zip(body, table, strict=True):
        n = r.numbers
        bold = r.is_other or r.is_total
        if r.is_total:
            c.setFillColor(RAISE)
            c.rect(x, ry, w, row_h, stroke=0, fill=1)
            c.setStrokeColor(INK)
            c.setLineWidth(0.7)
            c.line(x, ry + row_h, x + w, ry + row_h)
        base = ry + (row_h - size) / 2 + 0.7
        p.text(x + 4, base, p.fit(r.name, size, name_w - 6, bold), size, bold)
        dc = _diff_colour(n.diff_value)
        for i, val in enumerate(row):
            col = dc if i in (4, 5) else INK
            if i == 7 and r.change is not None:
                col = TONE.get(r.change.tone, MUTED)
            p.text(rights[i], base, val, size, r.is_total, col, "right")
        if bar_w and not r.is_total:
            bw = float(abs(n.var_pct) / max_var) * (bar_w - 12)
            c.setFillColor(LINE)
            c.rect(bar_x, base + 0.4, bar_w - 12, 2.6, stroke=0, fill=1)
            c.setFillColor(dc if n.diff_value != 0 else MUTED)
            c.rect(bar_x, base + 0.4, max(bw, 0.8), 2.6, stroke=0, fill=1)
        if not r.is_total:
            c.setStrokeColor(LINE)
            c.setLineWidth(0.35)
            c.line(x, ry, x + w, ry)
        ry -= row_h


# ---------------------------------------------------------------- observations and next steps


SCALE_MAX, SCALE_MIN = 1.22, 0.9
SCALES = [round(SCALE_MAX - i * 0.02, 2) for i in range(int((SCALE_MAX - SCALE_MIN) / 0.02) + 1)]
OBS_SHOWN = 5
STEP_LINES = 3


def _obs_items(p: Pen, d: SheetData, inner, size):
    out = []
    for o in d.observations[:OBS_SHOWN]:
        text_lines = p.wrap(o.text, size, inner)
        rec_lines = p.wrap(f"Recommendation: {o.recommendation}", size, inner) if o.recommendation else []
        out.append((o, text_lines, rec_lines))
    return out


def obs_fit_scale(p: Pen, d: SheetData, inner, avail):
    """Largest text scale at which every shown observation fits in full, or None."""
    more = len(d.observations) > OBS_SHOWN
    for sc in SCALES:
        size, lead = 6.8 * sc, 8.2 * sc
        need = sum(lead * (1 + len(t) + len(r)) + 3 for _, t, r in _obs_items(p, d, inner, size))
        if need + (10 if more else 0) <= avail:
            return sc
    return None


def _steps(d: SheetData):
    steps = list(d.next_steps)
    if d.followup_line:
        steps.append(d.followup_line)
    return steps


def steps_fit_scale(p: Pen, steps, inner, avail):
    for sc in SCALES:
        size, lead = 6.8 * sc, 8.2 * sc
        need = sum(lead * min(STEP_LINES, len(p.wrap(st, size, inner - 10 * sc))) + 2 for st in steps)
        if need <= avail:
            return sc
    return None


def draw_observations(p: Pen, d: SheetData, y, budget):
    """Observations (left) and next steps (right). Text is scaled up to 1.22x when everything fits,
    otherwise scaled down and clipped with "+ N more in the portal"."""
    gap = 10
    lw = W * 0.58
    rw = W - lw - gap
    c = p.c
    box(c, M, y, lw, budget, fill=colors.white)
    box(c, M + lw + gap, y, rw, budget, fill=colors.white)
    p.text(M + 8, y - 12, "Observations and recommendations", 8.5, True)
    p.text(M + lw + gap + 8, y - 12, "Next steps", 8.5, True)
    bottom = y - budget + 6
    top = y - 24
    inner = lw - 16

    sc = obs_fit_scale(p, d, inner, top - bottom + 2)
    full = sc is not None
    sc = sc or SCALE_MIN
    size, lead, tag_size = 6.8 * sc, 8.2 * sc, 6.3 * min(sc, 1.1)
    ry = top
    items = _obs_items(p, d, inner, size)
    more = max(0, len(d.observations) - OBS_SHOWN)
    for i, (o, text_lines, rec_lines) in enumerate(items):
        if full:
            t_max, r_max = len(text_lines), len(rec_lines)
        else:
            remaining = len(items) - i
            avail = ry - bottom - (10 if (remaining > 1 or more) else 0)
            share = max(3, int(avail / remaining / lead))
            t_max = max(1, min(len(text_lines), share - 1 - min(len(rec_lines), 2)))
            r_max = max(0, min(len(rec_lines), share - 1 - t_max))
            if lead * (1 + t_max + r_max) + 3 > ry - bottom:
                more += len(items) - i
                break
        tag = p.fit(o.category_label, tag_size, 120 * sc, True)
        tw = p.width(tag, tag_size, True) + 8
        c.setFillColor(RAISE)
        c.setStrokeColor(LINE)
        c.roundRect(M + 8, ry - 2.4, tw, tag_size + 3.1, 2, stroke=1, fill=1)
        p.text(M + 12, ry, tag, tag_size, True)
        pill(p, M + 12 + tw, ry, o.get_severity_display(), {"high": SHORT, "medium": WARN}.get(o.severity, MUTED),
             tag_size - 0.3)
        p.text(M + lw - 8, ry, o.get_kind_display(), tag_size, False, MUTED, "right")
        ry -= lead
        for line in p.clip(text_lines, t_max, size, inner):
            p.text(M + 8, ry, line, size)
            ry -= lead
        for line in p.clip(rec_lines, r_max, size, inner) if r_max else []:
            p.text(M + 8, ry, line, size, False, MUTED)
            ry -= lead
        ry -= 3
    if not items:
        p.text(M + 8, ry, "No observation recorded.", size, False, MUTED)
    if more:
        p.text(M + 8, bottom + 1, f"+ {more} more in the portal", 6.5, True, LIME_INK)

    # next steps
    x = M + lw + gap + 8
    inner = rw - 16
    steps = _steps(d)
    if not steps:
        p.text(x, top, "Keep the current counting and receiving routine.", 6.8, False, MUTED)
        return y - budget
    sc = steps_fit_scale(p, steps, inner, top - bottom + 2)
    full = sc is not None
    sc = sc or SCALE_MIN
    size, lead = 6.8 * sc, 8.2 * sc
    num_w = 10 * sc
    ry = top
    for i, step in enumerate(steps):
        lines = p.wrap(step, size, inner - num_w)
        if full:
            max_lines = min(len(lines), STEP_LINES)
        else:
            remaining = len(steps) - i
            avail = ry - bottom - (10 if remaining > 1 else 0)
            max_lines = min(len(lines), STEP_LINES, int(avail / remaining / lead) or 1)
            if lead * max_lines > ry - bottom - (10 if remaining > 1 else 0):
                p.text(x, bottom + 1, f"+ {remaining} more in the portal", 6.5, True, LIME_INK)
                break
        p.text(x, ry, f"{i + 1}.", size, True, LIME_INK)
        for line in p.clip(lines, max_lines, size, inner - num_w):
            p.text(x + num_w, ry, line, size)
            ry -= lead
        ry -= 2
    return y - budget


# ---------------------------------------------------------------- signatures and footer


def draw_signatures(p: Pen, d: SheetData, y):
    c = p.c
    decl = ("We confirm that the physical count was carried out in our presence and that the figures above "
            "were explained to us.")
    for i, line in enumerate(p.wrap(decl, 7, W)):
        p.text(M, y - 9 - i * 8.5, line, 7, False, MUTED)
    top = y - 22
    h, gap = 76, 8
    w = (W - 2 * gap) / 3
    titles = [("Store manager", d.store.name), ("Audit lead", "Audix Solutions & Co"),
              ("Client acknowledgement", d.client.name)]
    for i, (title, sub) in enumerate(titles):
        x = M + i * (w + gap)
        box(c, x, top, w, h, fill=colors.white)
        p.text(x + 8, top - 12, title, 8, True)
        p.text(x + 8, top - 21, p.fit(sub, 6.5, w - 16), 6.5, False, MUTED)
        line_w = w - 16 if i else w - 76
        for k, lbl in enumerate(("Name", "Signature", "Date")):
            ly = top - 36 - k * 14
            p.text(x + 8, ly, lbl, 6.5, False, MUTED)
            c.setStrokeColor(LINE)
            c.setLineWidth(0.6)
            c.line(x + 44, ly - 1.5, x + 8 + line_w, ly - 1.5)
        if i == 0:  # dashed stamp area
            c.setDash(2, 2)
            c.setStrokeColor(MUTED)
            c.roundRect(x + w - 62, top - h + 8, 54, 46, 4, stroke=1, fill=0)
            c.setDash()
            p.text(x + w - 35, top - h + 28, "Stamp", 6.5, False, MUTED, "center")
    return top - h


def draw_footer(p: Pen, d: SheetData, y):
    c = p.c
    h = 50
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(M, y, M + W, y)
    # associated-with chip (white)
    img = settings.BASE_DIR / "assets" / "associated-with-ca-india-vkna.png"
    p.text(M, y - 12, "Associated with", 6.5, False, MUTED)
    chip_w, chip_h = 132, 30
    c.setFillColor(colors.white)
    c.setStrokeColor(LINE)
    c.roundRect(M, y - 16 - chip_h, chip_w, chip_h, 4, stroke=1, fill=1)
    if img.exists():
        c.drawImage(str(img), M + 4, y - 14 - chip_h + 2, width=chip_w - 8, height=chip_h - 4,
                    preserveAspectRatio=True, mask="auto")
    mid = M + chip_w + 14
    now = timezone.localtime()
    p.text(mid, y - 14, "Powered by Audix Solutions & Co", 7.5, True)
    p.text(mid, y - 25, f"Generated {fmt_date(now)} {now:%H:%M} IST", 6.8, False, MUTED)
    p.text(mid, y - 36, "Signed copy: scan and upload as 'Signoff copy' in Audix Vault.", 6.8, False, MUTED)
    # QR code to the audit in the portal
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    qr = QrCodeWidget(d.portal_url)
    x0, y0, x1, y1 = qr.getBounds()
    size = 44
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, c, M + W - size, y - h + 2)
    p.text(M + W - size - 6, y - 40, "Open in the portal", 6.3, False, MUTED, "right")
    return y - h


def draw_watermark(p: Pen):
    c = p.c
    c.saveState()
    c.setFillColor(SHORT)
    c.setFillAlpha(0.11)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(35)
    c.setFont(p.bold, 64)
    c.drawCentredString(0, -20, "DRAFT, not final")
    c.restoreState()


# ---------------------------------------------------------------- page


FIXED_BELOW = 22 + 76 + 8 + 50  # declaration + signatures + gap + footer
OBS_MIN = 90  # the observations block keeps at least this height while the table can still shrink


def render(d: SheetData) -> bytes:
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle(f"Audit sign-off sheet {d.audit.reference}")
    c.setAuthor("Audix Solutions & Co")
    c.setSubject(f"{d.client.name}, {d.store.code} {d.store.name}")
    p = Pen(c)
    y = PAGE_H - M
    y = draw_header(p, d, y) - 7
    y = draw_name_boxes(p, d, y) - 6
    y = draw_kpis(p, d, y) - 7
    y = draw_summary_and_side(p, d, y) - 8
    bottom_limit = M + FIXED_BELOW + 8
    space = y - bottom_limit
    n = len(d.rows)
    cat_budget = min(category_height(n, ROW_MAX), space - OBS_MIN)
    y = draw_categories(p, d, y, cat_budget) - 4
    obs_budget = y - bottom_limit - 2
    y = draw_observations(p, d, y, obs_budget) - 8
    y = draw_signatures(p, d, y) - 8
    draw_footer(p, d, y)
    if d.draft:
        draw_watermark(p)
    c.showPage()
    c.save()
    return buf.getvalue()


def signoff_pdf(audit, portal_url: str) -> bytes:
    return render(build_data(audit, portal_url))


def filename(audit) -> str:
    from django.utils.text import slugify

    return f"audix-signoff-{audit.client.slug}-{slugify(audit.store.code)}-{audit.reference}.pdf"
