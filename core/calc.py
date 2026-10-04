"""Audix Vault business rules (brief Part B, section 5).

Pure Python: no Django imports. Every business number shown anywhere in the
portal (screens, exports, emails) comes from these functions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from core.formatting import (
    MINUS,
    fmt_date,
    inr,
    num,
    pct,
    plain_pct,
    qty,
    round_dec,
    short_date,
    to_decimal,
)

D0 = Decimal(0)
HUNDRED = Decimal(100)

FIELDS = (
    "stock_qty",
    "stock_value",
    "physical_qty",
    "physical_value",
    "damage_qty",
    "damage_value",
    "wbc_qty",
    "wbc_value",
)

HEALTHY, WATCH, REVIEW = "Healthy", "Watch", "Review"

FULL_TYPE = "physical"
AUDIT_TYPE_LABELS = {
    "physical": "Physical stock audit",
    "surprise": "Surprise audit",
    "cycle": "Cycle count",
    "closing": "Closing audit",
}

# Observation kinds and severities
SHORTAGE, DAMAGE, WBC, PROCESS_GAP, GOOD_PRACTICE = "shortage", "damage", "wbc", "process_gap", "good_practice"
KIND_LABELS = {
    SHORTAGE: "Shortage",
    DAMAGE: "Damage",
    WBC: "WBC",
    PROCESS_GAP: "Process gap",
    GOOD_PRACTICE: "Good practice",
}
HIGH, MEDIUM, LOW = "high", "medium", "low"

# Follow-up statuses (comparison) and entry-form presets
RESOLVED, IMPROVING, STILL_OPEN = "Resolved", "Improving", "Still open"
MAINTAINED, SLIPPED, CHECK_ON_SITE = "Maintained", "Slipped", "Check on site"
FOLLOWUP_PRESET = {RESOLVED: "done", IMPROVING: "in_progress", STILL_OPEN: "not_done"}

# Comparison verdicts
WITHIN_LIMIT, REPEAT_ISSUE, NO_CHANGE, IMPROVED, WORSE = (
    "Within limit",
    "Repeat issue",
    "No change",
    "Improved",
    "Worse",
)

VERDICT_PTS = Decimal("0.15")
REPEAT_PTS = Decimal("0.3")
NEGLIGIBLE_MONEY = Decimal(1)
NEGLIGIBLE_PTS = Decimal("0.005")


def D(value) -> Decimal:
    return to_decimal(value)


# --------------------------------------------------------------------------- thresholds


@dataclass(frozen=True)
class Thresholds:
    good_pct: Decimal = Decimal("1.0")
    warn_pct: Decimal = Decimal("2.0")
    cycle_days: int = 90
    soon_days: int = 15

    def __post_init__(self):
        object.__setattr__(self, "good_pct", D(self.good_pct))
        object.__setattr__(self, "warn_pct", D(self.warn_pct))


def status_for(var_pct, th: Thresholds | None = None) -> str:
    """Healthy / Watch / Review from abs(variance % of stock value), boundaries inclusive."""
    th = th or Thresholds()
    a = abs(D(var_pct))
    if a <= th.good_pct:
        return HEALTHY
    if a <= th.warn_pct:
        return WATCH
    return REVIEW


STATUS_TONE = {HEALTHY: "good", WATCH: "warn", REVIEW: "bad"}


# --------------------------------------------------------------------------- numbers


def pct_of(part, base) -> Decimal:
    base = D(base)
    if base == 0:
        return D0
    return D(part) / base * HUNDRED


def sale_pct(diff_value, sale_value) -> Decimal | None:
    """Variance % of sale value, or None when there is no sale value."""
    sale = D(sale_value) if sale_value is not None else D0
    if sale <= 0:
        return None
    return D(diff_value) / sale * HUNDRED


def has_sale(sale_value) -> bool:
    return sale_value is not None and D(sale_value) > 0


@dataclass
class SaleBasis:
    """Totals for a set of audits, keeping each percentage on a base the reader can reconcile.

    The "all audits" figures use every audit (difference / stock value). The sale figures use only
    the audits that have a sale value: their own difference divided by their own sale total.
    """

    n_total: int
    n_with_sale: int
    net_diff_all: Decimal
    stock_total: Decimal
    net_diff_with_sale: Decimal
    sale_total: Decimal

    @property
    def pct_stock_all(self) -> Decimal:
        return pct_of(self.net_diff_all, self.stock_total)

    @property
    def pct_sale(self) -> Decimal | None:
        return sale_pct(self.net_diff_with_sale, self.sale_total) if self.n_with_sale else None

    @property
    def has_any_sale(self) -> bool:
        return self.n_with_sale > 0 and self.sale_total > 0

    @property
    def all_have_sale(self) -> bool:
        return self.has_any_sale and self.n_with_sale == self.n_total

    @property
    def partial_sale(self) -> bool:
        return self.has_any_sale and self.n_with_sale < self.n_total

    def sale_text(self) -> str | None:
        """'-0.27% of sale value', with its own rupee base when only some audits have a sale value."""
        if not self.has_any_sale:
            return None
        text = f"{pct(self.pct_sale)} of sale value"
        if self.partial_sale:
            noun = "audit that has" if self.n_with_sale == 1 else "audits that have"
            text += (f" ({inr(self.net_diff_with_sale)} on the {self.n_with_sale} of {self.n_total} "
                     f"{noun} a sale value)")
        return text

    def stock_text(self) -> str:
        return f"{pct(self.pct_stock_all)} of stock value"


def sale_basis(audits) -> SaleBasis:
    """audits: objects or dicts with diff_value, stock_value, sale_value."""
    def get(a, k):
        return a.get(k) if isinstance(a, dict) else getattr(a, k)

    n = n_sale = 0
    diff_all = stock = diff_sale = sale = D0
    for a in audits:
        n += 1
        diff_all += D(get(a, "diff_value"))
        stock += D(get(a, "stock_value"))
        if has_sale(get(a, "sale_value")):
            n_sale += 1
            diff_sale += D(get(a, "diff_value"))
            sale += D(get(a, "sale_value"))
    return SaleBasis(n, n_sale, diff_all, stock, diff_sale, sale)


def sale_basis_from_totals(n_total, n_with_sale, net_diff_all, stock_total, net_diff_with_sale, sale_total) -> SaleBasis:
    """Same as sale_basis() from database aggregates."""
    return SaleBasis(int(n_total or 0), int(n_with_sale or 0), D(net_diff_all), D(stock_total),
                     D(net_diff_with_sale), D(sale_total))


@dataclass
class Numbers:
    """The eight entered numbers for one category (or an audit total)."""

    stock_qty: Decimal = D0
    stock_value: Decimal = D0
    physical_qty: Decimal = D0
    physical_value: Decimal = D0
    damage_qty: Decimal = D0
    damage_value: Decimal = D0
    wbc_qty: Decimal = D0
    wbc_value: Decimal = D0
    category: str = ""

    def __post_init__(self):
        for f in FIELDS:
            setattr(self, f, D(getattr(self, f)))

    @classmethod
    def from_mapping(cls, data, category: str = "") -> Numbers:
        if isinstance(data, dict):
            get = data.get
        else:

            def get(k, default=None):
                return getattr(data, k, default)

        return cls(**{f: D(get(f, 0)) for f in FIELDS}, category=category)

    # 5.1
    @property
    def total_physical_qty(self) -> Decimal:
        return self.physical_qty + self.damage_qty + self.wbc_qty

    @property
    def total_physical_value(self) -> Decimal:
        return self.physical_value + self.damage_value + self.wbc_value

    @property
    def diff_qty(self) -> Decimal:
        return self.total_physical_qty - self.stock_qty

    @property
    def diff_value(self) -> Decimal:
        return self.total_physical_value - self.stock_value

    @property
    def var_pct(self) -> Decimal:
        """Variance % of stock value (0 when stock value is 0)."""
        return pct_of(self.diff_value, self.stock_value)

    def var_pct_sale(self, sale_value) -> Decimal | None:
        return sale_pct(self.diff_value, sale_value)

    @property
    def damage_pct(self) -> Decimal:
        return pct_of(self.damage_value, self.stock_value)

    @property
    def wbc_pct(self) -> Decimal:
        return pct_of(self.wbc_value, self.stock_value)

    @property
    def shortage_pct(self) -> Decimal:
        v = self.var_pct
        return -v if v < 0 else D0

    @property
    def is_matched(self) -> bool:
        return self.diff_qty == 0 and self.diff_value == 0

    @property
    def is_shortage(self) -> bool:
        return self.diff_value < 0 or (self.diff_value == 0 and self.diff_qty < 0)

    def as_dict(self) -> dict:
        d = {f: getattr(self, f) for f in FIELDS}
        d.update(
            total_physical_qty=self.total_physical_qty,
            total_physical_value=self.total_physical_value,
            diff_qty=self.diff_qty,
            diff_value=self.diff_value,
            var_pct=self.var_pct,
        )
        return d


def audit_totals(lines) -> Numbers:
    """5.2: audit totals are the sum of the category lines for every field."""
    total = Numbers()
    for line in lines:
        for f in FIELDS:
            setattr(total, f, getattr(total, f) + D(getattr(line, f)))
    return total


def variance_word(n: Numbers) -> str:
    return "Shortage" if n.is_shortage else "Excess"


# --------------------------------------------------------------------------- remark (5.5)


def auto_remark(totals: Numbers, lines, sale_value=None, th: Thresholds | None = None) -> str:
    th = th or Thresholds()
    parts: list[str] = []
    var = totals.var_pct
    if totals.is_matched:
        parts.append("Stock matched, no variance.")
    else:
        text = (
            f"{variance_word(totals)} of {qty(abs(totals.diff_qty))} units ({inr(abs(totals.diff_value))}), "
            f"{round_dec(abs(var), 1)}% of stock value"
        )
        if has_sale(sale_value):
            sp = abs(totals.diff_value) / D(sale_value) * HUNDRED
            text += f" and {round_dec(sp, 2)}% of sale value"
        parts.append(text + ".")
    if totals.damage_qty > 0 or totals.wbc_qty > 0:
        text = f"Total physical {qty(totals.total_physical_qty)} = Physical {qty(totals.physical_qty)}"
        if totals.damage_qty > 0:
            text += f" + Damage {qty(totals.damage_qty)}"
        if totals.wbc_qty > 0:
            text += f" + WBC (Without Barcode) {qty(totals.wbc_qty)}"
        parts.append(text + ".")
    if not totals.is_matched and totals.is_shortage:
        worst = largest_shortage(lines)
        if worst is not None:
            parts.append(f"Largest shortage in {worst.category} ({inr(abs(worst.diff_value))}).")
    if abs(var) > th.warn_pct:
        parts.append("High variance, review required.")
    return " ".join(parts)


def largest_shortage(lines) -> Numbers | None:
    lines = list(lines)
    if not lines:
        return None
    worst = min(lines, key=lambda n: n.diff_value)
    return worst if worst.diff_value < 0 else None


# --------------------------------------------------------------------------- previous audit (5.6)


def series_for(audit_type: str) -> str:
    return "full" if audit_type in (FULL_TYPE, AUDIT_TYPE_LABELS[FULL_TYPE]) else "other"


def pick_previous(target, candidates):
    """Latest published audit, same client, store and series, earlier than target.

    Objects need: id, client_id, store_id, series, audit_date, created_at, status.
    Same-day ties are broken by creation time.
    """
    key_t = (target.audit_date, target.created_at)
    best = None
    for c in candidates:
        if c.id == target.id or getattr(c, "status", "published") != "published":
            continue
        if (c.client_id, c.store_id, c.series) != (target.client_id, target.store_id, target.series):
            continue
        key_c = (c.audit_date, c.created_at)
        if key_c >= key_t:
            continue
        if best is None or key_c > (best.audit_date, best.created_at):
            best = c
    return best


# --------------------------------------------------------------------------- aging and due (5.7)


def aging_days(audit_date: date, previous_date: date | None) -> int | None:
    if previous_date is None:
        return None
    return (audit_date - previous_date).days


def delay_days(aging: int | None, cycle_days: int = 90) -> int | None:
    if aging is None:
        return None
    return max(0, aging - cycle_days)


def aging_chain(dates, cycle_days: int = 90):
    """[(date, aging, delay)] for a store's full audits in date order."""
    out = []
    prev = None
    for d in sorted(dates):
        a = aging_days(d, prev)
        out.append((d, a, delay_days(a, cycle_days)))
        prev = d
    return out


def aging_text(aging: int | None) -> str:
    return "First audit" if aging is None else f"{aging} days"


def delay_text(aging: int | None, delay: int | None) -> str:
    if aging is None:
        return "Not applicable"
    if not delay:
        return "On time"
    return f"{delay} days late" if delay != 1 else "1 day late"


def aging_chip(aging: int | None, delay: int | None) -> tuple[str, str]:
    """(label, tone) used in the quarter calendar."""
    if aging is None:
        return "First audit", "neutral"
    if delay:
        return f"Delayed {delay} {'day' if delay == 1 else 'days'}", "bad"
    return "On time", "good"


@dataclass
class DueStatus:
    last_date: date | None
    next_due: date | None
    days_since: int | None
    label: str
    kind: str  # overdue / soon / ok / none
    overdue_days: int = 0

    @property
    def tone(self):
        return {"overdue": "bad", "soon": "warn", "ok": "good"}.get(self.kind, "neutral")


def due_status(last_full_date: date | None, today: date, cycle_days: int = 90, soon_days: int = 15) -> DueStatus:
    if last_full_date is None:
        return DueStatus(None, None, None, "No full audit yet", "none")
    days = (today - last_full_date).days
    next_due = last_full_date + timedelta(days=cycle_days)
    if days > cycle_days:
        over = days - cycle_days
        return DueStatus(
            last_full_date, next_due, days, f"Overdue by {over} {'day' if over == 1 else 'days'}", "overdue", over
        )
    if days > cycle_days - soon_days:
        return DueStatus(last_full_date, next_due, days, "Due soon", "soon")
    return DueStatus(last_full_date, next_due, days, "On schedule", "ok")


# --------------------------------------------------------------------------- periods and quarters (5.13)


def current_fy(today: date) -> int:
    """Start year of the financial year (April to March) containing today."""
    return today.year if today.month >= 4 else today.year - 1


@dataclass(frozen=True)
class Period:
    kind: str  # fy / cy
    year: int

    @property
    def start(self) -> date:
        return date(self.year, 4, 1) if self.kind == "fy" else date(self.year, 1, 1)

    @property
    def end(self) -> date:
        return date(self.year + 1, 3, 31) if self.kind == "fy" else date(self.year, 12, 31)

    @property
    def label(self) -> str:
        if self.kind == "fy":
            return f"FY {self.year}-{str(self.year + 1)[-2:]}"
        return f"Calendar year {self.year}"

    @property
    def long_label(self) -> str:
        if self.kind == "fy":
            return f"{self.label} (Apr {self.year} to Mar {self.year + 1})"
        return self.label

    @property
    def key(self) -> str:
        return f"{self.kind}-{self.year}"

    @classmethod
    def parse(cls, key: str | None, today: date) -> Period:
        try:
            kind, year = (key or "").split("-")
            if kind in ("fy", "cy"):
                return cls(kind, int(year))
        except (ValueError, TypeError):
            pass
        return cls("fy", current_fy(today))

    def quarters(self) -> list[Quarter]:
        if self.kind == "fy":
            spans = [((self.year, 4), (self.year, 6)), ((self.year, 7), (self.year, 9)),
                     ((self.year, 10), (self.year, 12)), ((self.year + 1, 1), (self.year + 1, 3))]
        else:
            spans = [((self.year, 1), (self.year, 3)), ((self.year, 4), (self.year, 6)),
                     ((self.year, 7), (self.year, 9)), ((self.year, 10), (self.year, 12))]
        out = []
        for i, ((y1, m1), (y2, m2)) in enumerate(spans, start=1):
            start = date(y1, m1, 1)
            end = _month_end(y2, m2)
            out.append(Quarter(f"Q{i}", f"{start:%b} to {end:%b}", start, end))
        return out


@dataclass(frozen=True)
class Quarter:
    name: str
    months: str
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def period_choices(today: date, first_year: int | None = None) -> list[Period]:
    fy = current_fy(today)
    first = min(first_year or fy, fy)
    out = [Period("fy", y) for y in range(fy, first - 1, -1)]
    out += [Period("cy", y) for y in range(today.year, min(first_year or today.year, today.year) - 1, -1)]
    return out


def quarter_empty_state(q: Quarter, today: date) -> str:
    if today < q.start:
        return "Not due yet"
    if q.start <= today <= q.end:
        return "Pending"
    return "Not audited"


def register_remark(totals: Numbers, sale_value, aging, delay, status: str, largest_cat: str | None) -> str:
    parts = []
    if totals.is_matched:
        parts.append("Stock matched.")
    else:
        sp = sale_pct(totals.diff_value, sale_value)
        base, p = ("sale", sp) if sp is not None else ("stock", totals.var_pct)
        parts.append(f"{variance_word(totals)} {round_dec(abs(p), 2)}% of {base} value.")
    if aging is None:
        parts.append("First audit on record.")
    elif delay:
        parts.append(f"Audit delayed by {delay} {'day' if delay == 1 else 'days'}.")
    else:
        parts.append("Audit done on time.")
    if not totals.is_matched and totals.is_shortage and largest_cat:
        parts.append(f"Largest shortage in {largest_cat}.")
    if status == REVIEW:
        parts.append("High variance, review required.")
    return " ".join(parts)


# --------------------------------------------------------------------------- comparison (5.8)


@dataclass
class Obs:
    kind: str
    category: str | None  # None = whole store
    severity: str = MEDIUM
    text: str = ""
    recommendation: str = ""
    id: object = None

    @property
    def category_label(self):
        return self.category or "Whole store"

    @property
    def kind_label(self):
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class AuditData:
    """Everything the comparison needs about one audit."""

    id: object
    audit_date: date
    store_id: object
    store_name: str
    city: str
    totals: Numbers
    lines: dict  # category name -> Numbers, in category order
    sale_value: Decimal | None = None
    observations: list = field(default_factory=list)
    reference: str = ""

    @property
    def place(self):
        return self.city or self.store_name

    def var_sale(self):
        return sale_pct(self.totals.diff_value, self.sale_value)


def verdict(pa, pb, th: Thresholds) -> str:
    pa, pb = abs(D(pa)), abs(D(pb))
    d = pb - pa
    if pa <= th.good_pct and pb <= th.good_pct:
        return WITHIN_LIMIT
    if abs(d) < VERDICT_PTS:
        return REPEAT_ISSUE if pb > th.warn_pct else NO_CHANGE
    if d < 0:
        return IMPROVED
    return WORSE


VERDICT_TONE = {WITHIN_LIMIT: "good", IMPROVED: "good", NO_CHANGE: "neutral", REPEAT_ISSUE: "bad", WORSE: "bad"}


def trend_label(prev_pct, latest_pct) -> str:
    """Store health table: last audit to latest audit."""
    d = abs(D(latest_pct)) - abs(D(prev_pct))
    if abs(d) < VERDICT_PTS:
        return "About the same"
    return "Better" if d < 0 else "Worse"


@dataclass
class Change:
    text: str
    direction: str  # down / up / none / na
    tone: str  # good / bad / neutral


def _arrow(delta) -> str:
    return "▼" if delta < 0 else "▲"


def change_money_neutral(a, b) -> Change:
    a, b = D(a), D(b)
    delta = b - a
    if abs(delta) < NEGLIGIBLE_MONEY:
        return Change("No change", "none", "neutral")
    rel = f" ({round_dec(abs(delta) / abs(a) * HUNDRED, 1)}%)" if a else ""
    return Change(f"{_arrow(delta)} {inr(abs(delta))}{rel}", "down" if delta < 0 else "up", "neutral")


def change_abs_relative(a, b) -> Change:
    """Net variance: compare absolute values, show relative % of A's absolute value."""
    a_abs, b_abs = abs(D(a)), abs(D(b))
    delta = b_abs - a_abs
    if abs(delta) < NEGLIGIBLE_MONEY:
        return Change("No change", "none", "neutral")
    tone = "good" if delta < 0 else "bad"
    if a_abs:
        text = f"{_arrow(delta)} {round_dec(abs(delta) / a_abs * HUNDRED, 1)}%"
    else:
        text = f"{_arrow(delta)} {inr(abs(delta))}"
    return Change(text, "down" if delta < 0 else "up", tone)


def change_pts(a, b, lower_is_better: bool = True, absolute: bool = True) -> Change:
    a, b = D(a), D(b)
    if absolute:
        a, b = abs(a), abs(b)
    delta = b - a
    if abs(delta) < NEGLIGIBLE_PTS:
        return Change("No change", "none", "neutral")
    if lower_is_better:
        tone = "good" if delta < 0 else "bad"
    else:
        tone = "neutral"
    return Change(f"{_arrow(delta)} {round_dec(abs(delta), 2)} pts", "down" if delta < 0 else "up", tone)


def change_count(a: int, b: int) -> Change:
    delta = b - a
    if delta == 0:
        return Change("No change", "none", "neutral")
    return Change(f"{_arrow(delta)} {abs(delta)}", "down" if delta < 0 else "up", "good" if delta < 0 else "bad")


def money_pct_text(value, pct_value) -> str:
    return f"{inr(value)} ({pct(pct_value)})"


def variance_phrase(n: Numbers, with_pct: bool = True) -> str:
    if n.is_matched:
        return "no variance"
    article = "a shortage" if n.is_shortage else "an excess"
    text = f"{article} of {inr(abs(n.diff_value))}"
    if with_pct:
        text += f" ({pct(abs(n.var_pct))} of stock value)"
    return text


def categories_above(data: AuditData, th: Thresholds) -> list[str]:
    return [name for name, n in data.lines.items() if abs(n.var_pct) > th.warn_pct]


NEUTRAL_ROWS = ("Stock value audited", "Total physical value", "Sale value")


@dataclass
class OverallRow:
    label: str
    a: str
    b: str
    change: Change

    @property
    def is_variance_row(self):
        """Rows shown in the short 'Compared with last audit' table on the audit page."""
        return self.label not in NEUTRAL_ROWS


@dataclass
class CategoryRow:
    category: str
    a: Numbers
    b: Numbers
    change_value: Change
    verdict: str

    @property
    def verdict_tone(self):
        return VERDICT_TONE[self.verdict]


@dataclass
class FollowUpItem:
    observation: Obs
    status: str

    @property
    def tone(self):
        return {RESOLVED: "good", MAINTAINED: "good", IMPROVING: "warn", CHECK_ON_SITE: "neutral"}.get(self.status, "bad")


@dataclass
class Comparison:
    a: AuditData
    b: AuditData
    same_store: bool
    overall: list
    categories: list
    sentences: list
    followups: list
    improvements: list
    any_sale: bool


def followup_status(obs: Obs, a: AuditData, b: AuditData, th: Thresholds) -> str:
    if obs.kind == GOOD_PRACTICE:
        n = b.totals if obs.category is None else b.lines.get(obs.category)
        if n is None:
            return SLIPPED
        return MAINTAINED if abs(n.var_pct) <= th.good_pct else SLIPPED
    if obs.category is None:
        return CHECK_ON_SITE
    la, lb = a.lines.get(obs.category), b.lines.get(obs.category)
    if la is None or lb is None:
        return CHECK_ON_SITE
    metric = {DAMAGE: "damage_pct", WBC: "wbc_pct"}.get(obs.kind, "shortage_pct")
    m0, m1 = getattr(la, metric), getattr(lb, metric)
    if m1 <= Decimal("0.7") * m0 or m1 <= th.good_pct:
        return RESOLVED
    if m1 < Decimal("0.95") * m0:
        return IMPROVING
    return STILL_OPEN


def improvement_points(a: AuditData, b: AuditData, th: Thresholds) -> list[str]:
    out: list[str] = []
    for o in b.observations:
        if o.recommendation and o.kind != GOOD_PRACTICE:
            out.append(f"{o.category_label}: {o.recommendation}")
    above_a = set(categories_above(a, th))
    for name in categories_above(b, th):
        if name in above_a:
            out.append(f"{name} was above {plain_pct(th.warn_pct)} in both audits. Count it first at the next audit.")
    if b.totals.wbc_pct - a.totals.wbc_pct > VERDICT_PTS:
        out.append("WBC is rising. Check barcode labels on new stock at receiving.")
    return list(dict.fromkeys(out))


def compare(a: AuditData, b: AuditData, th: Thresholds | None = None) -> Comparison:
    th = th or Thresholds()
    ta, tb = a.totals, b.totals
    same_store = a.store_id == b.store_id
    any_sale = has_sale(a.sale_value) or has_sale(b.sale_value)

    overall = [
        OverallRow("Stock value audited", inr(ta.stock_value), inr(tb.stock_value),
                   change_money_neutral(ta.stock_value, tb.stock_value)),
        OverallRow("Total physical value", inr(ta.total_physical_value), inr(tb.total_physical_value),
                   change_money_neutral(ta.total_physical_value, tb.total_physical_value)),
    ]
    if any_sale:
        sa = inr(a.sale_value) if has_sale(a.sale_value) else "Not given"
        sb = inr(b.sale_value) if has_sale(b.sale_value) else "Not given"
        ch = (change_money_neutral(a.sale_value, b.sale_value)
              if has_sale(a.sale_value) and has_sale(b.sale_value) else Change("Not comparable", "na", "neutral"))
        overall.append(OverallRow("Sale value", sa, sb, ch))
    overall.append(OverallRow("Net variance", inr(ta.diff_value), inr(tb.diff_value),
                              change_abs_relative(ta.diff_value, tb.diff_value)))
    overall.append(OverallRow("Variance % of stock value", pct(ta.var_pct), pct(tb.var_pct),
                              change_pts(ta.var_pct, tb.var_pct)))
    if any_sale:
        va, vb = a.var_sale(), b.var_sale()
        ch = change_pts(va, vb) if va is not None and vb is not None else Change("Not comparable", "na", "neutral")
        overall.append(OverallRow("Variance % of sale value", pct(va) if va is not None else "Not given",
                                  pct(vb) if vb is not None else "Not given", ch))
    overall.append(OverallRow("Damage", money_pct_text(ta.damage_value, ta.damage_pct),
                              money_pct_text(tb.damage_value, tb.damage_pct), change_pts(ta.damage_pct, tb.damage_pct)))
    overall.append(OverallRow("WBC (Without Barcode)", money_pct_text(ta.wbc_value, ta.wbc_pct),
                              money_pct_text(tb.wbc_value, tb.wbc_pct), change_pts(ta.wbc_pct, tb.wbc_pct)))
    ca, cb = len(categories_above(a, th)), len(categories_above(b, th))
    overall.append(OverallRow(f"Categories above {plain_pct(th.warn_pct)}", str(ca), str(cb), change_count(ca, cb)))

    rows = []
    for name, lb in b.lines.items():
        la = a.lines.get(name)
        if la is None:
            continue
        rows.append(CategoryRow(name, la, lb, change_in_value(la, lb), verdict(la.var_pct, lb.var_pct, th)))

    sentences = comparison_sentences(a, b, rows, th, same_store)

    followups = []
    if same_store and a.id != b.id and a.audit_date <= b.audit_date:
        for o in a.observations:
            if o.recommendation:
                followups.append(FollowUpItem(o, followup_status(o, a, b, th)))

    return Comparison(a, b, same_store, overall, rows, sentences, followups, improvement_points(a, b, th), any_sale)


def change_in_value(a: Numbers, b: Numbers) -> Change:
    """Category 'change in value': abs(B difference) - abs(A difference); lower is better."""
    d = abs(b.diff_value) - abs(a.diff_value)
    if abs(d) < NEGLIGIBLE_MONEY:
        return Change("No change", "none", "neutral")
    return Change(f"{_arrow(d)} {inr(abs(d))}", "down" if d < 0 else "up", "good" if d < 0 else "bad")


@dataclass
class SheetCategory:
    """One row of the sign-off sheet's category table."""

    numbers: Numbers
    previous: Numbers | None = None
    change: Change | None = None
    is_other: bool = False

    @property
    def name(self):
        return self.numbers.category


def sheet_categories(lines, previous_lines=None, max_rows: int = 26, keep: int = 24) -> list[SheetCategory]:
    """Category rows for a one-page sheet.

    Up to max_rows categories are all shown. Beyond that, the `keep` categories with the largest
    shortage are shown (in their normal order) plus one "Other (N more)" row that sums the rest,
    and sums the same categories of the previous audit for the change column.
    """
    lines = list(lines)
    prev = previous_lines
    if len(lines) > max_rows:
        worst = {id(n) for n in sorted(lines, key=lambda n: n.diff_value)[:keep]}
        shown = [n for n in lines if id(n) in worst]
        rest = [n for n in lines if id(n) not in worst]
        other = audit_totals(rest)
        other.category = f"Other ({len(rest)} more)"
        prev_other = None
        if prev is not None:
            prev_rest = [prev[n.category] for n in rest if n.category in prev]
            if prev_rest:
                prev_other = audit_totals(prev_rest)
        rows = [SheetCategory(n) for n in shown] + [SheetCategory(other, prev_other, is_other=True)]
    else:
        rows = [SheetCategory(n) for n in lines]
    if prev is not None:
        for r in rows:
            if not r.is_other:
                r.previous = prev.get(r.name)
            if r.previous is not None:
                r.change = change_in_value(r.previous, r.numbers)
    return rows


def comparison_sentences(a: AuditData, b: AuditData, rows, th: Thresholds, same_store: bool) -> list[str]:
    ta, tb = a.totals, b.totals
    out = []
    if same_store:
        out.append(f"{a.place} had {variance_phrase(ta)} on {fmt_date(a.audit_date)} and "
                   f"{variance_phrase(tb)} on {fmt_date(b.audit_date)}.")
    else:
        pa, pb = a.place, b.place
        if pa == pb:
            pa, pb = a.store_name, b.store_name
        out.append(f"{pa} had {variance_phrase(ta)} and {pb} had {variance_phrase(tb)}.")
    a_abs, b_abs = abs(ta.diff_value), abs(tb.diff_value)
    if a_abs > 0:
        rel = (b_abs - a_abs) / a_abs * HUNDRED
        if abs(rel) >= 1:
            word = "down" if rel < 0 else "up"
            n = round_dec(abs(rel), 0)
            if same_store:
                out.append(f"Net variance is {word} {n}% since the last audit.")
            else:
                out.append(f"The second store's variance is {word} {n}% compared with the first.")
    if rows:
        deltas = [(abs(r.b.var_pct) - abs(r.a.var_pct), r) for r in rows]
        best_d, best = min(deltas, key=lambda x: x[0])
        if best_d < -VERDICT_PTS:
            out.append(f"{best.category} improved the most, from {_diff_text(best.a)} to {_diff_text(best.b)}.")
        worst_d, worst = max(deltas, key=lambda x: x[0])
        if worst_d > VERDICT_PTS:
            out.append(f"{worst.category} got worse, from {_diff_text(worst.a)} to {_diff_text(worst.b)}.")
        repeat = [r.category for d, r in deltas
                  if abs(r.a.var_pct) > th.warn_pct and abs(r.b.var_pct) > th.warn_pct and abs(d) < REPEAT_PTS]
        if repeat:
            verb = "was" if len(repeat) == 1 else "were"
            out.append(f"Repeat issue: {', '.join(repeat)} {verb} above {plain_pct(th.warn_pct)} in both audits.")
    if abs(tb.damage_pct - ta.damage_pct) >= VERDICT_PTS:
        word = "fell" if tb.damage_pct < ta.damage_pct else "rose"
        out.append(f"Damage {word} from {money_pct_text(ta.damage_value, ta.damage_pct)} to "
                   f"{money_pct_text(tb.damage_value, tb.damage_pct)}.")
    if abs(tb.wbc_pct - ta.wbc_pct) >= VERDICT_PTS:
        word = "fell" if tb.wbc_pct < ta.wbc_pct else "rose"
        out.append(f"WBC (without barcode) {word} from {money_pct_text(ta.wbc_value, ta.wbc_pct)} to "
                   f"{money_pct_text(tb.wbc_value, tb.wbc_pct)}.")
    return out


def _diff_text(n: Numbers) -> str:
    return f"{inr(n.diff_value)} ({pct(n.var_pct)})"


# --------------------------------------------------------------------------- observation drafts (5.9)

DEFAULT_SHORTAGE_TEMPLATES = [
    ("The shortage sits on fast-moving items, which points to billing or receiving gaps.",
     "Count this category at close every day for a week and match the count with billing."),
    ("Items were found in the wrong section and some were not billed.",
     "Fix section labels and run a weekly spot check on this category."),
    ("Stock moved between sections without a record.",
     "Record every transfer in the stock register on the same day."),
]
DEFAULT_DAMAGE_TEMPLATE = (
    "Damage is {pct}% of stock value in {category}, mostly from storage and handling.",
    "Keep this stock off the floor and return damaged items to the supplier every Friday.",
)
DEFAULT_WBC_TEMPLATE = (
    "{units} units without barcode in {category} ({pct}% of stock value).",
    "Barcode loose items at receiving and block billing of unlabelled items.",
)
DEFAULT_GOOD_TEMPLATE = (
    "Stock was well controlled across all categories.",
    "Continue the current counting and receiving routine.",
)


def stable_index(audit_id, category: str, n: int = 3) -> int:
    h = hashlib.sha256(f"{audit_id}|{category}".encode()).hexdigest()
    return int(h, 16) % n


def suggest_drafts(audit_id, lines, templates: dict | None = None) -> list[dict]:
    """Editable observation drafts built from the numbers."""
    t = templates or {}
    shortage_t = t.get("shortage") or DEFAULT_SHORTAGE_TEMPLATES
    damage_t = t.get("damage") or DEFAULT_DAMAGE_TEMPLATE
    wbc_t = t.get("wbc") or DEFAULT_WBC_TEMPLATE
    good_t = t.get("good_practice") or DEFAULT_GOOD_TEMPLATE
    lines = [n for n in lines if n.stock_value > 0]
    drafts = []
    short = sorted((n for n in lines if n.shortage_pct > Decimal("1.5")), key=lambda n: -n.shortage_pct)[:3]
    for n in short:
        cause, rec = shortage_t[stable_index(audit_id, n.category, len(shortage_t))]
        drafts.append({
            "category": n.category,
            "kind": SHORTAGE,
            "severity": HIGH if n.shortage_pct > 3 else MEDIUM,
            "text": f"Shortage of {qty(abs(n.diff_qty))} units ({inr(abs(n.diff_value))}, "
                    f"{round_dec(n.shortage_pct, 2)}% of stock value). {cause}",
            "recommendation": rec,
        })
    if lines:
        worst_damage = max(lines, key=lambda n: n.damage_pct)
        if worst_damage.damage_pct > Decimal("0.95"):
            drafts.append({
                "category": worst_damage.category, "kind": DAMAGE, "severity": MEDIUM,
                "text": damage_t[0].format(pct=round_dec(worst_damage.damage_pct, 2), category=worst_damage.category,
                                           units=qty(worst_damage.damage_qty)),
                "recommendation": damage_t[1],
            })
        worst_wbc = max(lines, key=lambda n: n.wbc_pct)
        if worst_wbc.wbc_pct > Decimal("0.95"):
            drafts.append({
                "category": worst_wbc.category, "kind": WBC, "severity": MEDIUM,
                "text": wbc_t[0].format(pct=round_dec(worst_wbc.wbc_pct, 2), category=worst_wbc.category,
                                        units=qty(worst_wbc.wbc_qty)),
                "recommendation": wbc_t[1],
            })
    if not drafts:
        drafts.append({"category": None, "kind": GOOD_PRACTICE, "severity": LOW,
                       "text": good_t[0], "recommendation": good_t[1]})
    return drafts


# --------------------------------------------------------------------------- dashboard (5.11)

WINDOW_DAYS = {"daily": 7, "weekly": 28, "monthly": 90, "yearly": 365}
PERIOD_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}
WINDOW_TEXT = {"daily": "last 7 days", "weekly": "last 4 weeks", "monthly": "last 3 months", "yearly": "last 12 months"}
PREVIOUS_TEXT = {"daily": "previous 7 days", "weekly": "previous 4 weeks", "monthly": "previous 3 months",
                 "yearly": "previous 12 months"}


def window(period: str, today: date) -> tuple[date, date]:
    n = WINDOW_DAYS.get(period, 28)
    return today - timedelta(days=n - 1), today


def previous_window(period: str, today: date) -> tuple[date, date]:
    n = WINDOW_DAYS.get(period, 28)
    start, _ = window(period, today)
    return start - timedelta(days=n), start - timedelta(days=1)


@dataclass(frozen=True)
class Bucket:
    label: str
    start: date
    end: date


def buckets(period: str, today: date, first_year: int | None = None) -> list[Bucket]:
    out = []
    if period == "daily":
        for i in range(13, -1, -1):
            d = today - timedelta(days=i)
            out.append(Bucket(short_date(d), d, d))
    elif period == "weekly":
        for i in range(11, -1, -1):
            end = today - timedelta(days=7 * i)
            start = end - timedelta(days=6)
            out.append(Bucket(short_date(start), start, end))
    elif period == "monthly":
        y, m = today.year, today.month
        months = []
        for _ in range(12):
            months.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        for y, m in reversed(months):
            start = date(y, m, 1)
            out.append(Bucket(f"{start:%b} {str(y)[-2:]}", start, _month_end(y, m)))
    else:
        first = min(first_year or today.year - 2, today.year)
        for y in range(first, today.year + 1):
            out.append(Bucket(str(y), date(y, 1, 1), date(y, 12, 31)))
    return out


def change_pct(current, previous) -> Decimal | None:
    current, previous = D(current), D(previous)
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * HUNDRED


@dataclass
class StoreHealth:
    store_name: str
    city: str
    audits: int
    stock_value: Decimal
    diff_value: Decimal
    damage_value: Decimal
    wbc_value: Decimal
    shortage_value: Decimal = D0

    @property
    def var_pct(self):
        return pct_of(self.diff_value, self.stock_value)

    @property
    def damage_pct(self):
        return pct_of(self.damage_value, self.stock_value)

    @property
    def wbc_pct(self):
        return pct_of(self.wbc_value, self.stock_value)


def _b(text):
    return {"t": text, "b": True}


def _t(text):
    return {"t": text, "b": False}


def insight_text(item) -> str:
    """Plain text of an insight (for emails, exports and tests)."""
    return "".join(p["t"] for p in item["parts"])


def insights(stores: list[StoreHealth], shortage_now, shortage_prev, overdue_places: list[str],
             th: Thresholds, window_text: str, previous_text: str = "previous period") -> list[dict]:
    """'What stands out': short sentences with a bold lead. Each item: parts [{t, b}], tone, link (optional)."""
    out = []
    audited = [s for s in stores if s.audits]
    warn = plain_pct(th.warn_pct)
    if audited:
        worst = min(audited, key=lambda x: x.diff_value)
        if worst.diff_value < 0:
            out.append({"parts": [_b(f"{worst.city or worst.store_name} ({worst.store_name})"),
                                  _t(f" has the largest shortage in the {window_text}: {inr(abs(worst.diff_value))}, "
                                     f"{round_dec(abs(worst.var_pct), 1)}% of its stock value.")], "tone": "bad"})
    ch = change_pct(abs(D(shortage_now)), abs(D(shortage_prev)))
    if ch is not None:
        word = "down" if ch <= 0 else "up"
        out.insert(min(1, len(out)), {"parts": [_t("Shortage is "), _b(f"{word} {round_dec(abs(ch), 0)}%"),
                                               _t(f" compared with the {previous_text}.")],
                                     "tone": "good" if ch <= 0 else "bad"})
    if audited:
        above = [s for s in audited if abs(s.var_pct) > th.warn_pct]
        if above:
            verb = "is" if len(above) == 1 else "are"
            out.append({"parts": [_b(f"{len(above)} of {len(audited)} audited stores"),
                                  _t(f" {verb} above {warn} variance and need review.")], "tone": "warn"})
        else:
            out.append({"parts": [_b("All audited stores"), _t(f" are within {warn} variance.")], "tone": "good"})
        best = min(audited, key=lambda x: abs(x.var_pct))
        out.append({"parts": [_b(best.city or best.store_name),
                              _t(f" is the best performing store at {round_dec(abs(best.var_pct), 1)}% variance.")],
                    "tone": "good"})
        wbc = max(audited, key=lambda x: x.wbc_pct)
        if wbc.wbc_pct > 0:
            out.append({"parts": [_t("Highest WBC (without barcode) share is at "), _b(wbc.city or wbc.store_name),
                                  _t(f": {round_dec(wbc.wbc_pct, 2)}% of stock value. "
                                     "Barcode labelling there is worth checking.")], "tone": "warn"})
    else:
        out.append({"parts": [_t(f"No audits in the {window_text}.")], "tone": "neutral"})
    if overdue_places:
        n = len(overdue_places)
        out.append({"parts": [_b(f"{n} store is" if n == 1 else f"{n} stores are"),
                              _t(f" past the {th.cycle_days}-day full audit cycle: {', '.join(overdue_places)}. ")],
                    "tone": "bad", "link": "aging"})
    return out


def signed_money(value) -> str:
    v = D(value)
    return inr(v, signed=True)


def minus(text: str) -> str:
    return text.replace("-", MINUS)


__all__ = [name for name in dir() if not name.startswith("_")] + ["num"]
