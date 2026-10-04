"""Server-rendered inline SVG charts. Labels are escaped; numbers come from core.calc."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from django.utils.safestring import mark_safe

from core.formatting import MINUS, compact, inr, pct


def _f(v) -> float:
    return float(v or 0)


def diverging_bars(buckets, height=250, width=640):
    """buckets: [{"label", "shortage" (<=0), "excess" (>=0), "tip"}]. Shortage below zero (red), excess above (blue).

    Same geometry as the reference prototype: one scale for both sides, at least 14px above the zero line.
    """
    n = max(len(buckets), 1)
    pad_l, pad_r, pad_t, pad_b = 58, 8, 16, 30
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    m_ex = max([_f(b["excess"]) for b in buckets] + [0])
    m_sh = max([abs(_f(b["shortage"])) for b in buckets] + [0])
    total = (m_ex + m_sh) or 1
    up_h = max(plot_h * m_ex / total, 14)
    dn_h = plot_h - up_h
    zero_y = pad_t + up_h
    unit = min(up_h / m_ex if m_ex else 1e18, dn_h / m_sh if m_sh else 1e18)
    slot = plot_w / n
    bar_w = min(slot * 0.62, 46)
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Shortage and excess value over time">']
    parts.append(f'<line class="zero" x1="{pad_l}" x2="{width - pad_r}" y1="{zero_y:.1f}" y2="{zero_y:.1f}"/>')
    parts.append(f'<text x="{pad_l - 8}" y="{zero_y + 4:.1f}" text-anchor="end">0</text>')
    if m_ex:
        parts.append(f'<text x="{pad_l - 8}" y="{pad_t + 4}" text-anchor="end">+{escape(compact(m_ex))}</text>')
    if m_sh:
        parts.append(f'<text x="{pad_l - 8}" y="{height - pad_b + 2}" text-anchor="end">{MINUS}{escape(compact(m_sh))}</text>')
    for i, b in enumerate(buckets):
        cx = pad_l + slot * i + slot / 2
        x = cx - bar_w / 2
        tip = escape(b.get("tip", ""), quote=True)
        ex, sh = _f(b["excess"]), abs(_f(b["shortage"]))
        parts.append(f'<g data-tip="{tip}" tabindex="0"><rect x="{cx - slot / 2:.1f}" y="{pad_t}" width="{slot:.1f}" '
                     f'height="{plot_h}" fill="transparent"/>')
        if ex > 0:
            h = ex * unit
            parts.append(f'<rect class="bar-excess" x="{x:.1f}" y="{zero_y - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="2"/>')
        if sh > 0:
            h = sh * unit
            parts.append(f'<rect class="bar-short" x="{x:.1f}" y="{zero_y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="2"/>')
        if not b.get("audits"):
            parts.append(f'<line x1="{cx - 4:.1f}" x2="{cx + 4:.1f}" y1="{zero_y:.1f}" y2="{zero_y:.1f}" stroke="var(--muted)" stroke-width="2"/>')
        parts.append("</g>")
        if n <= 12 or i % 2 == (n - 1) % 2:
            parts.append(f'<text x="{cx:.1f}" y="{height - 8}" text-anchor="middle">{escape(b["label"])}</text>')
    parts.append("</svg>")
    return mark_safe("".join(parts))


def paired_bars(rows, a_label="A", b_label="B", width=680):
    """rows: [{"category", "a_pct", "b_pct", "a_diff", "b_diff"}]. Horizontal bars around zero; A light, B solid."""
    row_h, label_w, pad_r = 46, 170, 150
    height = max(len(rows), 1) * row_h + 10
    plot_w = width - label_w - pad_r
    m = max([abs(_f(r["a_pct"])) for r in rows] + [abs(_f(r["b_pct"])) for r in rows] + [0.5])
    zero_x = label_w + plot_w / 2
    scale = (plot_w / 2) / m
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Variance % by category, {escape(a_label)} and {escape(b_label)}">']
    parts.append(f'<line class="zero" x1="{zero_x:.1f}" x2="{zero_x:.1f}" y1="0" y2="{height}"/>')
    for i, r in enumerate(rows):
        y = 6 + i * row_h
        parts.append(f'<text x="0" y="{y + 20}" style="font-size:11px" class="val">{escape(r["category"][:26])}</text>')
        for j, (key, dkey, cls, lab) in enumerate((("a_pct", "a_diff", "bar-a", a_label), ("b_pct", "b_diff", "bar-b", b_label))):
            v = _f(r[key])
            w = abs(v) * scale
            x = zero_x - w if v < 0 else zero_x
            by = y + j * 17
            tone = "short" if v < 0 else "exc"
            tip = escape(f"{r['category']} · {lab}: {inr(r[dkey])} ({pct(r[key])})", quote=True)
            parts.append(f'<rect class="{cls} {tone}" x="{x:.1f}" y="{by:.1f}" width="{max(w, 1.5):.1f}" height="14" rx="2" data-tip="{tip}" tabindex="0"/>')
            parts.append(f'<text x="{label_w + plot_w + 6}" y="{by + 11:.1f}">{escape(lab)}: {escape(inr(r[dkey]))} ({escape(pct(r[key]))})</text>')
    parts.append("</svg>")
    return mark_safe("".join(parts))


def bar_width(value, max_value, max_px=90) -> int:
    m = abs(_f(max_value))
    if m == 0:
        return 0
    return max(2, int(abs(_f(value)) / m * max_px))


def as_decimal(v) -> Decimal:
    return Decimal(str(v or 0))
