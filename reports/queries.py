"""Database-side aggregation for the portal. Business formulas come from core.calc."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.db.models import Count, Max, Q, Sum

from audits.models import AuditLine, Series
from core import calc
from core.calc import D

SUM_FIELDS = ("stock_value", "physical_value", "damage_value", "wbc_value", "total_value", "diff_value", "diff_qty",
              "damage_qty", "wbc_qty", "physical_qty", "stock_qty", "total_qty")


def window_totals(qs) -> dict:
    agg = qs.aggregate(
        audits=Count("id"),
        **{f"t_{f}": Sum(f) for f in SUM_FIELDS},
        shortage=Sum("diff_value", filter=Q(diff_value__lt=0)),
        shortage_qty=Sum("diff_qty", filter=Q(diff_value__lt=0)),
        excess=Sum("diff_value", filter=Q(diff_value__gt=0)),
        excess_qty=Sum("diff_qty", filter=Q(diff_value__gt=0)),
        sale_diff=Sum("diff_value", filter=Q(sale_value__gt=0)),
        sale=Sum("sale_value", filter=Q(sale_value__gt=0)),
    )
    out = {k.removeprefix("t_"): (D(v) if k != "audits" else (v or 0)) for k, v in agg.items()}
    out["var_pct"] = calc.pct_of(out["diff_value"], out["stock_value"])
    out["var_pct_sale"] = calc.sale_pct(out["sale_diff"], out["sale"]) if out["sale"] else None
    out["damage_pct"] = calc.pct_of(out["damage_value"], out["stock_value"])
    out["wbc_pct"] = calc.pct_of(out["wbc_value"], out["stock_value"])
    return out


def per_day(qs, start: date, end: date) -> dict:
    rows = qs.filter(audit_date__gte=start, audit_date__lte=end).values("audit_date").annotate(
        shortage=Sum("diff_value", filter=Q(diff_value__lt=0)),
        excess=Sum("diff_value", filter=Q(diff_value__gt=0)),
        stock=Sum("stock_value"), n=Count("id"))
    return {r["audit_date"]: r for r in rows}


def bucket_series(qs, buckets) -> list[dict]:
    if not buckets:
        return []
    days = per_day(qs, buckets[0].start, buckets[-1].end)
    out = []
    for b in buckets:
        sh = ex = st = D(0)
        n = 0
        for d, r in days.items():
            if b.start <= d <= b.end:
                sh += D(r["shortage"])
                ex += D(r["excess"])
                st += D(r["stock"])
                n += r["n"]
        out.append({"label": b.label, "shortage": sh, "excess": ex, "stock": st, "audits": n, "start": b.start, "end": b.end})
    return out


def store_rows(qs, stores, th: calc.Thresholds) -> list[dict]:
    agg = {r["store_id"]: r for r in qs.values("store_id").annotate(
        audits=Count("id"), s_stock=Sum("stock_value"), s_diff=Sum("diff_value"),
        s_damage=Sum("damage_value"), s_wbc=Sum("wbc_value"),
        s_shortage=Sum("diff_value", filter=Q(diff_value__lt=0)), last=Max("audit_date"))}
    latest = {a.store_id: a for a in qs.filter(store_id__in=list(agg)).select_related("previous_audit")
              .order_by("store_id", "-audit_date", "-created_at").distinct("store_id")}
    rows = []
    for s in stores:
        r = agg.get(s.pk)
        if not r:
            rows.append({"store": s, "audits": 0})
            continue
        h = calc.StoreHealth(s.name, s.city, r["audits"], D(r["s_stock"]), D(r["s_diff"]),
                             D(r["s_damage"]), D(r["s_wbc"]), D(r["s_shortage"]))
        lat = latest.get(s.pk)
        prev = lat.previous_audit if lat else None
        rows.append({
            "store": s, "audits": r["audits"], "health": h, "var_pct": h.var_pct, "damage_pct": h.damage_pct,
            "wbc_pct": h.wbc_pct, "status": calc.status_for(h.var_pct, th), "latest": lat, "prev": prev,
            "trend": calc.trend_label(prev.var_pct_stock, lat.var_pct_stock) if (lat and prev) else None,
        })
    audited = [r for r in rows if r["audits"]]
    audited.sort(key=lambda r: -abs(r["var_pct"]))
    m = max([abs(r["var_pct"]) for r in audited] + [D(0)])
    for r in audited:
        r["bar"] = int(abs(r["var_pct"]) / m * 90) if m else 0
    return audited + [r for r in rows if not r["audits"]]


def category_rows(qs) -> list[dict]:
    rows = AuditLine.objects.filter(audit__in=qs).values("category__name").annotate(
        stock=Sum("stock_value"), diff=Sum("diff_value"), physical=Sum("physical_value"),
        damage=Sum("damage_value"), wbc=Sum("wbc_value"))
    out = []
    for r in rows:
        out.append({"category": r["category__name"], "stock": D(r["stock"]), "diff": D(r["diff"]),
                    "pct": calc.pct_of(r["diff"], r["stock"]), "damage": D(r["damage"]), "wbc": D(r["wbc"])})
    out.sort(key=lambda r: r["diff"])
    m = max([abs(r["diff"]) for r in out] + [D(0)])
    for r in out:
        r["bar"] = int(abs(r["diff"]) / m * 90) if m else 0
    return out


def last_full_audits(qs) -> dict:
    """store_id -> last full audit date."""
    return dict(qs.filter(series=Series.FULL).values("store_id").annotate(last=Max("audit_date"))
                .values_list("store_id", "last"))


def due_rows(qs, stores, today, client) -> list[dict]:
    last = last_full_audits(qs)
    out = []
    for s in stores:
        st = calc.due_status(last.get(s.pk), today, client.cycle_days, client.soon_days)
        out.append({"store": s, "due": st})
    order = {"overdue": 0, "soon": 1, "none": 2, "ok": 3}
    out.sort(key=lambda r: (order[r["due"].kind], -(r["due"].overdue_days or 0), r["store"].code))
    return out


def group_by(items, key):
    d = defaultdict(list)
    for it in items:
        d[key(it)].append(it)
    return d
