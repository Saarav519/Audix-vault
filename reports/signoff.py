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
        data.next_steps = calc.improvement_points(data.comparison.a, data.comparison.b, th)
        counts = {"done": 0, "in_progress": 0, "not_done": 0}
        for fu in audit.followups.all():
            counts[fu.status] = counts.get(fu.status, 0) + 1
        if sum(counts.values()):
            data.followup_line = (f"Last audit's recommendations: {counts['done']} done, "
                                  f"{counts['in_progress']} in progress, {counts['not_done']} not done")
    else:
        data.rows = calc.sheet_categories(lines, None)
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
        ("Total physical value", inr(t.total_physical_value), "Physical + damage + WBC", None),
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
    h = 126
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


COMPARE_LABELS = ["Net variance", "Variance % of stock value", "Variance % of sale value", "Damage",
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
    ry = y - 37
    size = 6.4
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
        ry -= 12
    sentence = next((s for s in cmp.sentences if s.startswith("Net variance is")), None)
    if sentence is None:
        sentence = "Net variance is about the same as at the last audit."
    for i, line in enumerate(p.clip(p.wrap(sentence, 7, w - 16), 2, 7, w - 16)):
        p.text(x + 8, ry - 3 - i * 9, line, 7, True)


def draw_glance(p: Pen, d: SheetData, x, y, w):
    t, a, th = d.totals, d.audit, d.thresholds
    p.text(x + 8, y - 13, "This audit at a glance", 8.5, True)
    p.text(x + w - 8, y - 13, "First audit for this store", 6.5, False, MUTED, "right")
    worst = calc.largest_shortage(d.lines)
    above = [n.category for n in d.lines if abs(n.var_pct) > th.warn_pct]
    rows = [
        ("Largest shortage", f"{worst.category}, {inr(worst.diff_value)} ({pct(worst.var_pct)})" if worst else "None"),
        ("Damage", f"{inr(t.damage_value)} ({pct(t.damage_pct)} of stock value)"),
        ("WBC (Without Barcode)", f"{inr(t.wbc_value)} ({pct(t.wbc_pct)} of stock value)"),
        (f"Categories above {plain_pct(th.warn_pct)}", f"{len(above)} of {len(d.lines)}"),
        ("Audit type", a.get_audit_type_display()),
        ("Shift", a.get_shift_display()),
    ]
    ry = y - 30
    for label, value in rows:
        p.text(x + 8, ry, label, 6.8, False, MUTED)
        p.text(x + w - 8, ry, p.fit(value, 6.8, w * 0.62), 6.8, True, INK, "right")
        p.c.setStrokeColor(LINE)
        p.c.setLineWidth(0.4)
        p.c.line(x + 8, ry - 3.5, x + w - 8, ry - 3.5)
        ry -= 13.5


# ---------------------------------------------------------------- category table


def category_layout(n_rows: int, budget: float):
    """(columns, rows_per_column, row_height, font_size). Two tables side by side from 14 rows."""
    cols = 1 if n_rows <= 13 else 2
    per_col = n_rows if cols == 1 else -(-n_rows // 2)
    head = 13 + 12  # title + header row
    row_h = max(8.6, min(13.0, (budget - head) / max(per_col, 1)))
    size = max(5.6, min(7.4, row_h * 0.58))
    return cols, per_col, row_h, size


def draw_categories(p: Pen, d: SheetData, y, budget):
    rows = d.rows
    has_prev = d.comparison is not None
    cols, per_col, row_h, size = category_layout(len(rows), budget)
    title = "Category-wise summary" + (" (compact two-column view)" if cols == 2 else "")
    p.text(M, y - 10, title, 8.5, True)
    if cols == 2:
        p.text(M + W, y - 10, "Value and % of stock value", 6.5, False, MUTED, "right")
    top = y - 14
    gap = 10
    tw = (W - gap * (cols - 1)) / cols
    max_var = max([abs(r.numbers.var_pct) for r in rows] + [Decimal("0.5")])
    for ci in range(cols):
        chunk = rows[ci * per_col:(ci + 1) * per_col]
        if not chunk:
            continue
        draw_category_table(p, chunk, M + ci * (tw + gap), top, tw, row_h, size, has_prev, cols == 2, max_var)
    used = 14 + 12 + per_col * row_h + 4
    return y - used


def draw_category_table(p: Pen, chunk, x, top, w, row_h, size, has_prev, compact, max_var):
    c = p.c
    headers = ["Category", "Stock", "Total physical", "Difference", "Var %"]
    cells = []
    for r in chunk:
        n = r.numbers
        row = [r.name, inr(n.stock_value), inr(n.total_physical_value), inr(n.diff_value), pct(n.var_pct)]
        if has_prev:
            row.append(r.change.text if r.change else "New")
        elif not compact:
            row += [f"{inr(n.damage_value)} ({pct(n.damage_pct)})", f"{inr(n.wbc_value)} ({pct(n.wbc_pct)})"]
        cells.append(row)
    if has_prev:
        headers.append("Change vs last audit" if not compact else "Change")
    elif not compact:
        headers += ["Damage", "WBC"]
    bar_w = 0 if compact else 46
    # fit the numeric columns at this font size, shrink the font if needed; the "Other (N more)"
    # label must always be readable in full
    while True:
        widths = [max(p.width(h, size - 0.6, False), *(p.width(r[i], size, False) for r in cells)) + 7
                  for i, h in enumerate(headers)][1:]
        name_w = w - sum(widths) - bar_w - 8
        need = max([52] + [p.width(r.name, size, True) + 6 for r in chunk if r.is_other])
        if name_w >= need or size <= 5.0:
            break
        size -= 0.2
    hy = top - 9
    c.setFillColor(RAISE)
    c.rect(x, top - 12, w, 12, stroke=0, fill=1)
    p.text(x + 4, hy, "Category", size - 0.6, False, MUTED)
    cx = x + 4 + name_w
    col_right = []
    for i, h in enumerate(headers[1:5]):
        cx += widths[i]
        col_right.append(cx)
        p.text(cx, hy, h, size - 0.6, False, MUTED, "right")
    bar_x = cx + 6
    if bar_w:
        cx += bar_w
    for j, h in enumerate(headers[5:], start=4):
        cx += widths[j]
        col_right.append(cx)
        p.text(cx, hy, h, size - 0.6, False, MUTED, "right")
    ry = top - 12 - row_h
    for r, row in zip(chunk, cells, strict=True):
        base = ry + (row_h - size) / 2 + 0.6
        n = r.numbers
        p.text(x + 4, base, p.fit(row[0], size, name_w - 4, r.is_other), size, r.is_other)
        diff_col = SHORT if n.diff_value < 0 else (EXCESS if n.diff_value > 0 else INK)
        for i, val in enumerate(row[1:]):
            col = diff_col if i in (2, 3) else INK
            if i == 4 and has_prev and r.change is not None:
                col = TONE.get(r.change.tone, MUTED)
            p.text(col_right[i], base, val, size, False, col, "right")
        if bar_w:
            bw = float(abs(n.var_pct) / max_var) * (bar_w - 8)
            c.setFillColor(LINE)
            c.rect(bar_x, base + 0.6, bar_w - 8, 2.6, stroke=0, fill=1)
            c.setFillColor(diff_col if n.diff_value != 0 else MUTED)
            c.rect(bar_x, base + 0.6, max(bw, 0.8), 2.6, stroke=0, fill=1)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.35)
        c.line(x, ry, x + w, ry)
        ry -= row_h


# ---------------------------------------------------------------- observations and next steps


def draw_observations(p: Pen, d: SheetData, y, budget):
    gap = 10
    lw = W * 0.58
    rw = W - lw - gap
    c = p.c
    box(c, M, y, lw, budget, fill=colors.white)
    box(c, M + lw + gap, y, rw, budget, fill=colors.white)
    p.text(M + 8, y - 12, "Observations and recommendations", 8.5, True)
    p.text(M + lw + gap + 8, y - 12, "Next steps", 8.5, True)
    size, lead = 6.8, 8.2
    inner = lw - 16
    bottom = y - budget + 6
    ry = y - 24
    obs = d.observations[:5]
    more = max(0, len(d.observations) - 5)
    shown = 0
    for i, o in enumerate(obs):
        remaining = len(obs) - i
        avail = ry - bottom - (10 if (remaining > 1 or more) else 0)
        # lines this item may use: share what is left, at least header + 1 + 1
        share = max(3, int(avail / remaining / lead))
        text_lines = p.wrap(o.text, size, inner)
        rec_lines = p.wrap(f"Recommendation: {o.recommendation}", size, inner) if o.recommendation else []
        t_max = max(1, min(len(text_lines), share - 1 - min(len(rec_lines), 2)))
        r_max = max(0, min(len(rec_lines), share - 1 - t_max))
        need = lead * (1 + t_max + r_max) + 3
        if need > ry - bottom:
            more += len(obs) - i
            break
        tag = p.fit(o.category_label, 6.3, 120, True)
        tw = p.width(tag, 6.3, True) + 8
        c.setFillColor(RAISE)
        c.setStrokeColor(LINE)
        c.roundRect(M + 8, ry - 2.4, tw, 9.4, 2, stroke=1, fill=1)
        p.text(M + 12, ry, tag, 6.3, True)
        sev = o.get_severity_display()
        pill(p, M + 12 + tw, ry, sev, {"high": SHORT, "medium": WARN}.get(o.severity, MUTED), 6.0)
        p.text(M + lw - 8, ry, o.get_kind_display(), 6.3, False, MUTED, "right")
        ry -= lead
        for line in p.clip(text_lines, t_max, size, inner):
            p.text(M + 8, ry, line, size)
            ry -= lead
        for line in p.clip(rec_lines, r_max, size, inner) if r_max else []:
            p.text(M + 8, ry, line, size, False, MUTED)
            ry -= lead
        ry -= 3
        shown += 1
    if not obs:
        p.text(M + 8, ry, "No observation recorded.", size, False, MUTED)
    if more:
        p.text(M + 8, bottom + 1, f"+ {more} more in the portal", 6.5, True, LIME_INK)

    # next steps
    x = M + lw + gap + 8
    inner = rw - 22
    steps = list(d.next_steps)
    if d.followup_line:
        steps.append(d.followup_line)
    ry = y - 24
    if not steps:
        p.text(x, ry, "Keep the current counting and receiving routine.", size, False, MUTED)
        return y - budget
    for i, step in enumerate(steps):
        remaining = len(steps) - i
        avail = ry - bottom - (10 if remaining > 1 else 0)
        lines = p.wrap(step, size, inner)
        max_lines = max(1, min(len(lines), int(avail / remaining / lead), 4))
        if lead * max_lines > ry - bottom - (10 if remaining > 1 else 0) or max_lines < 1:
            p.text(x, bottom + 1, f"+ {remaining} more in the portal", 6.5, True, LIME_INK)
            break
        p.text(x, ry, f"{i + 1}.", size, True, LIME_INK)
        for line in p.clip(lines, max_lines, size, inner):
            p.text(x + 10, ry, line, size)
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
    cat_budget = min(206.0, space - 116)  # observations keep at least ~116pt
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
