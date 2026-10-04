import re
from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core import formatting as fm
from core.calc import STATUS_TONE

register = template.Library()

ICONS = {
    "dashboard": '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    "compare": '<path d="M16 3h5v5M21 3l-7 7M8 21H3v-5M3 21l7-7"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "home": '<path d="M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
}


@register.simple_tag
def icon(name, label=""):
    path = ICONS.get(name, "")
    aria = format_html(' role="img" aria-label="{}"', label) if label else mark_safe(' aria-hidden="true"')
    return mark_safe(f'<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"{aria}>{path}</svg>')


XMARK_FILE = Path(settings.BASE_DIR) / "assets" / "x-mark.svg"
XMARK_FALLBACK = ('<polygon fill="#12180F" points="113,0 79,0 45,50 79,100 113,100 79,50"/>'
                  '<polygon fill="#B9E10C" points="0,0 34,0 68,50 34,100 0,100 34,50"/>')


@lru_cache(maxsize=1)
def xmark_svg() -> str:
    """The two-tone x from assets/x-mark.svg, inline so the dark half can turn white on dark backgrounds."""
    try:
        raw = XMARK_FILE.read_text()
    except OSError:
        raw = XMARK_FALLBACK
    polys = re.findall(r"<polygon\b[^>]*/>", raw) or re.findall(r"<polygon\b[^>]*/>", XMARK_FALLBACK)
    out = []
    for poly in polys:
        poly = re.sub(r'\s(?:fill|class)="[^"]*"', "", poly)
        cls = "xl" if "0,0 34,0" in poly else "xd"
        out.append(poly.replace("<polygon", f'<polygon class="{cls}"', 1))
    return '<svg class="xmark" viewBox="0 0 113 100" aria-hidden="true">' + "".join(out) + "</svg>"


@register.simple_tag
def xmark():
    return mark_safe(xmark_svg())


@register.simple_tag
def wordmark(vault=True):
    v = '<span class="vault">Vault</span>' if vault else ""
    return mark_safe(f'<span class="audix" aria-label="Audix">Audi{xmark_svg()}</span>{v}')


@register.filter
def inr(value, decimals=0):
    if value is None or value == "":
        return "—"
    return fm.inr(value, int(decimals))


@register.filter
def inr_signed(value):
    if value is None or value == "":
        return "—"
    return fm.inr(value, signed=True)


@register.filter
def cinr(value):
    if value is None or value == "":
        return "—"
    return fm.compact_inr(value)


@register.filter
def num(value, decimals=0):
    if value is None or value == "":
        return "—"
    return fm.num(value, int(decimals))


@register.filter
def qty(value):
    if value is None or value == "":
        return "—"
    return fm.qty(value)


@register.filter
def pct(value, decimals=2):
    if value is None or value == "":
        return "—"
    return fm.pct(value, int(decimals))


@register.filter
def pct_signed(value, decimals=2):
    if value is None or value == "":
        return "—"
    return fm.pct(value, int(decimals), signed=True)


@register.filter
def plain_pct(value):
    return fm.plain_pct(value)


@register.filter
def fdate(value):
    return fm.fmt_date(value)


@register.filter
def money_class(value):
    try:
        v = fm.to_decimal(value)
    except Exception:
        return ""
    return "shortage" if v < 0 else ("excess" if v > 0 else "")


@register.filter
def filesize(value):
    return fm.file_size(value)


@register.filter
def absval(value):
    try:
        return abs(value)
    except TypeError:
        return value


@register.simple_tag
def status_pill(label, tone=None):
    tone = tone or STATUS_TONE.get(label, "neutral")
    return format_html('<span class="pill {}">{}</span>', tone, label or "—")


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag(takes_context=True)
def nav_current(context, *prefixes):
    path = context["request"].path
    if any(path.startswith(p[1:]) for p in prefixes if p.startswith("!")):
        return ""
    for p in prefixes:
        if not p.startswith("!") and (path == p or (p != "/" and path.startswith(p))):
            return mark_safe('aria-current="page"')
    return ""


@register.simple_tag(takes_context=True)
def querystring_with(context, **kwargs):
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v is None:
            q.pop(k, None)
        else:
            q[k] = v
    return "?" + q.urlencode()


@register.filter
def initials(name):
    """Client monogram: first letters of the first two words ("Greenfield Retail" -> "GR")."""
    return "".join(w[0] for w in str(name or "").split()[:2]).upper() or "C"


@register.simple_tag
def detail_without_ref(entry):
    """Activity detail with the audit reference removed (the reference is shown as a link beside it)."""
    detail = entry.detail or ""
    ref = entry.audit.reference if entry.audit_id and entry.audit else ""
    if ref:
        detail = detail.replace(ref, "", 1).strip()
        detail = detail.lstrip(":;,· ").strip()
    return detail
