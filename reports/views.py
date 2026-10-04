"""Client portal: dashboard, all audits, tracker, compare, aging report (read-only)."""

from __future__ import annotations

import calendar as pycal
import re
import uuid
from datetime import date, datetime, timedelta

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from audits import services
from audits.models import Audit, AuditType, Shift
from clients.models import Client, Store
from core import calc, charts
from core.calc import D
from core.formatting import compact_inr, inr, pct, qty, short_date
from core.permissions import portal_required
from core.scoping import PortalScope
from reports import queries
from reports.aging import build_aging


def today():
    return timezone.localdate()


def resolve(request):
    """(client, scope, picker_clients). Clients are fixed; staff pick a client."""
    user = request.user
    scope = request.portal
    if user.is_client_user or scope.impersonating:
        if scope.client is None:
            raise Http404
        return scope.client, scope, None
    if not user.is_staff_member:
        raise Http404
    clients = list(Client.objects.order_by("name"))
    client = None
    cid = request.GET.get("client")
    if cid:
        try:
            client = next((c for c in clients if c.pk == uuid.UUID(cid)), None)
        except ValueError:
            client = None
    if client is None:
        client = next((c for c in clients if c.is_active), clients[0] if clients else None)
    if client is None:
        raise Http404
    return client, PortalScope(client, None, is_staff_view=True), clients


def scope_stores(client, scope):
    qs = Store.objects.filter(client=client)
    if scope.store_ids is not None:
        qs = qs.filter(pk__in=scope.store_ids)
    return list(qs.order_by("code"))


def base_ctx(request, client, scope, pickers, **kw):
    cq = f"client={client.pk}" if pickers else ""
    return {"client": client, "scope": scope, "picker_clients": pickers, "th": client.thresholds(), "cq": cq, **kw}


def _change(cur, prev, lower_is_better=False, cmp="previous period"):
    ch = calc.change_pct(cur, prev)
    if ch is None:
        return {"text": "No earlier data", "tone": "neutral"}
    if abs(ch) < D("0.5"):
        return {"text": f"No change vs {cmp}", "tone": "neutral"}
    arrow = "\u25bc" if ch < 0 else "\u25b2"
    tone = "neutral"
    if lower_is_better:
        tone = "good" if ch < 0 else "bad"
    return {"text": f"{arrow} {calc.round_dec(abs(ch), 0)}% vs {cmp}", "tone": tone}


def _change_pts(cur, prev, cmp="previous period"):
    c = calc.change_pts(cur, prev)
    return {"text": f"{c.text} vs {cmp}", "tone": c.tone}


# ---------------------------------------------------------------- dashboard


@portal_required
def dashboard(request):
    client, scope, pickers = resolve(request)
    t = today()
    period = request.GET.get("period", "weekly")
    if period not in calc.WINDOW_DAYS:
        period = "weekly"
    th = client.thresholds()
    stores = scope_stores(client, scope)
    qs = Audit.objects.for_scope(scope)
    start, end = calc.window(period, t)
    pstart, pend = calc.previous_window(period, t)
    cur_qs = qs.filter(audit_date__gte=start, audit_date__lte=end)
    prev_qs = qs.filter(audit_date__gte=pstart, audit_date__lte=pend)
    cur = queries.window_totals(cur_qs)
    prev = queries.window_totals(prev_qs)

    first = qs.order_by("audit_date").values_list("audit_date", flat=True).first()
    bks = calc.buckets(period, t, first.year if first else None)
    series = queries.bucket_series(qs, bks)
    for b in series:
        if b["audits"]:
            b["tip"] = (f"{b['label']}: {b['audits']} audit{'s' if b['audits'] != 1 else ''}, "
                        f"shortage {inr(abs(b['shortage']))}, excess {inr(b['excess'])}, "
                        f"net {inr(b['shortage'] + b['excess'])}")
        else:
            b["tip"] = f"{b['label']}: no audits"
    chart = charts.diverging_bars(series)
    window_text, previous_text = calc.WINDOW_TEXT[period], calc.PREVIOUS_TEXT[period]

    store_rows = queries.store_rows(cur_qs, stores, th)
    for r in store_rows:
        if r["audits"]:
            r["bar_pct"] = min(abs(r["var_pct"]) / 4 * 100, 100)
            r["tone"] = calc.STATUS_TONE[r["status"]]
    cats = queries.category_rows(cur_qs)
    due = queries.due_rows(qs, stores, t, client)
    overdue = [r["store"].city or r["store"].name for r in due if r["due"].kind == "overdue"]
    healths = [r["health"] for r in store_rows if r["audits"]]
    insights = calc.insights(healths, cur["shortage"], prev["shortage"], overdue,
                             calc.Thresholds(th.good_pct, th.warn_pct, client.cycle_days, client.soon_days),
                             window_text, previous_text)
    phys_total = cur["total_value"]
    breakup = [
        ("Good stock", cur["physical_value"], calc.pct_of(cur["physical_value"], phys_total), "var(--good)"),
        ("Damage", cur["damage_value"], calc.pct_of(cur["damage_value"], phys_total), "var(--warn)"),
        ("WBC (Without Barcode)", cur["wbc_value"], calc.pct_of(cur["wbc_value"], phys_total), "var(--excess)"),
    ]
    vs = previous_text
    kpis = [
        {"label": "Audits completed", "value": str(cur["audits"]), "change": _change(cur["audits"], prev["audits"], cmp=vs)},
        {"label": "Stock value", "value": compact_inr(cur["stock_value"]), "sub": f"{qty(cur['stock_qty'])} units",
         "change": _change(cur["stock_value"], prev["stock_value"], cmp=vs)},
        {"label": "Total physical value", "value": compact_inr(cur["total_value"]), "sub": "Physical + damage + WBC",
         "change": _change(cur["total_value"], prev["total_value"], cmp=vs)},
        {"label": "Damage", "value": compact_inr(cur["damage_value"]),
         "sub": f"{qty(cur['damage_qty'])} units, {pct(cur['damage_pct'])} of stock value",
         "change": _change_pts(cur["damage_pct"], prev["damage_pct"], cmp=vs)},
        {"label": "WBC (Without Barcode)", "value": compact_inr(cur["wbc_value"]),
         "sub": f"{qty(cur['wbc_qty'])} units, {pct(cur['wbc_pct'])} of stock value",
         "change": _change_pts(cur["wbc_pct"], prev["wbc_pct"], cmp=vs)},
        {"label": "Variance", "value": pct(cur["var_pct"]), "sub": "of stock value" + (
            f"; {cur['sale_text']}" if cur["sale_text"] else ""),
         "change": _change_pts(cur["var_pct"], prev["var_pct"], cmp=vs)},
    ]
    shortage_change = _change(abs(cur["shortage"]), abs(prev["shortage"]), lower_is_better=True, cmp=vs)
    recent = qs.filter(audit_date__gt=t - timedelta(days=7)).count()
    latest = qs.select_related("store").order_by("-audit_date", "-created_at").first()
    ctx = base_ctx(request, client, scope, pickers, period=period, periods=calc.PERIOD_LABELS, window=(start, end),
                   window_text=window_text, previous_text=previous_text, cur=cur, prev=prev, chart=chart, kpis=kpis,
                   store_rows=store_rows, cats=cats, breakup=breakup, insights=insights,
                   shortage_change=shortage_change, overdue=len(overdue), recent=recent, latest=latest)
    return render(request, "portal/dashboard.html", ctx)


# ---------------------------------------------------------------- all audits

PERIODS = [("", "Any time"), ("30", "Last 30 days"), ("90", "Last 90 days"), ("fy", "This financial year"),
           ("lastfy", "Last financial year"), ("cy", "This calendar year")]
TYPE_WORDS = {label.lower(): value for value, label in AuditType.choices}


def _parse_date(text):
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            pass
    for fmt in ("%b %Y", "%B %Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            return d, "month"
        except ValueError:
            pass
    return None, None


FILTER_KEYS = ("q", "store", "type", "shift", "period")


def apply_audit_filters(qs, params, search_client_name=False):
    """Store, audit type, shift, period and search filters shared by the portal list, the admin list and
    their exports. Returns (queryset, filter values)."""
    f = {k: (params.get(k) or "").strip() for k in FILTER_KEYS}
    if f["store"]:
        try:
            qs = qs.filter(store_id=uuid.UUID(f["store"]))
        except ValueError:
            f["store"] = ""
    if f["type"] in AuditType.values:
        qs = qs.filter(audit_type=f["type"])
    else:
        f["type"] = ""
    if f["shift"] in Shift.values:
        qs = qs.filter(shift=f["shift"])
    else:
        f["shift"] = ""
    t = today()
    fy = calc.current_fy(t)
    if f["period"] == "30":
        qs = qs.filter(audit_date__gte=t - timedelta(days=29))
    elif f["period"] == "90":
        qs = qs.filter(audit_date__gte=t - timedelta(days=89))
    elif f["period"] == "fy":
        p = calc.Period("fy", fy)
        qs = qs.filter(audit_date__gte=p.start, audit_date__lte=p.end)
    elif f["period"] == "lastfy":
        p = calc.Period("fy", fy - 1)
        qs = qs.filter(audit_date__gte=p.start, audit_date__lte=p.end)
    elif f["period"] == "cy":
        qs = qs.filter(audit_date__year=t.year)
    else:
        f["period"] = ""
    if f["q"]:
        q = f["q"]
        cond = (Q(reference__icontains=q) | Q(store__name__icontains=q) | Q(store__code__icontains=q)
                | Q(store__city__icontains=q))
        if search_client_name:
            cond |= Q(client__name__icontains=q)
        tv = [v for label, v in TYPE_WORDS.items() if q.lower() in label]
        if tv:
            cond |= Q(audit_type__in=tv)
        d, kind = _parse_date(q)
        if d and kind == "month":
            cond |= Q(audit_date__year=d.year, audit_date__month=d.month)
        elif d:
            cond |= Q(audit_date=d)
        qs = qs.filter(cond)
    return qs.order_by("-audit_date", "-created_at"), f


def describe_audit_filters(f, stores=(), extra=()) -> list[tuple[str, str]]:
    """Human-readable list of the active filters, for export headers."""
    out = list(extra)
    if f.get("store"):
        store = next((s for s in stores if str(s.pk) == f["store"]), None)
        if store is not None:
            out.append(("Store", f"{store.code} · {store.name}" + (f", {store.city}" if store.city else "")))
    if f.get("type"):
        out.append(("Audit type", dict(AuditType.choices)[f["type"]]))
    if f.get("shift"):
        out.append(("Shift", dict(Shift.choices)[f["shift"]]))
    out.append(("Period", dict(PERIODS).get(f.get("period", ""), "Any time")))
    if f.get("q"):
        out.append(("Search", f"“{f['q']}”"))
    return out


def filtered_audits(request, scope):
    return apply_audit_filters(Audit.objects.for_scope(scope).select_related("store"), request.GET)


@portal_required
def audits(request):
    client, scope, pickers = resolve(request)
    qs, f = filtered_audits(request, scope)
    summary = queries.window_totals(qs)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = base_ctx(request, client, scope, pickers, page=page, f=f, summary=summary,
                   stores=scope_stores(client, scope), types=AuditType.choices, shifts=Shift.choices,
                   periods=PERIODS)
    if request.GET.get("partial") == "rows":
        return render(request, "portal/partials/audit_rows.html", ctx)
    return render(request, "portal/audits.html", ctx)


# ---------------------------------------------------------------- tracker


@portal_required
def tracker(request):
    client, scope, pickers = resolve(request)
    t = today()
    th = client.thresholds()
    m = re.match(r"^(\d{4})-(\d{2})$", request.GET.get("month", ""))
    year, month = (int(m.group(1)), int(m.group(2))) if m and 1 <= int(m.group(2)) <= 12 else (t.year, t.month)
    first = date(year, month, 1)
    last = date(year, month, pycal.monthrange(year, month)[1])
    grid_start = first - timedelta(days=first.weekday())
    grid_end = last + timedelta(days=6 - last.weekday())
    qs = Audit.objects.for_scope(scope).select_related("store")
    month_audits = list(qs.filter(audit_date__gte=grid_start, audit_date__lte=grid_end).order_by("audit_date", "store__code"))
    by_day = queries.group_by(month_audits, lambda a: a.audit_date)
    days = []
    d = grid_start
    while d <= grid_end:
        days.append({"date": d, "in_month": d.month == month, "audits": by_day.get(d, []), "key": d.isoformat()})
        d += timedelta(days=1)
    prev_month = (first - timedelta(days=1)).strftime("%Y-%m")
    next_month = (last + timedelta(days=1)).strftime("%Y-%m")

    tl_start = t - timedelta(days=89)
    stores = scope_stores(client, scope)
    tl_audits = queries.group_by(qs.filter(audit_date__gte=tl_start, audit_date__lte=t), lambda a: a.store_id)
    timeline = []
    for s in stores:
        dots = [{"audit": a, "left": round((a.audit_date - tl_start).days / 89 * 100, 2),
                 "tone": calc.STATUS_TONE.get(a.status_label, "neutral")} for a in tl_audits.get(s.pk, [])]
        timeline.append({"store": s, "dots": dots})
    ticks = [{"left": round(i / 89 * 100, 2), "label": short_date(tl_start + timedelta(days=i))} for i in (0, 30, 60, 89)]
    ctx = base_ctx(request, client, scope, pickers, days=days, month_label=first.strftime("%B %Y"),
                   prev_month=prev_month, next_month=next_month, today=t, timeline=timeline, ticks=ticks,
                   due=queries.due_rows(qs, stores, t, client), th=th,
                   selected=t.isoformat() if first <= t <= last else first.isoformat())
    return render(request, "portal/tracker.html", ctx)


# ---------------------------------------------------------------- compare


def _store_options(qs, store_id):
    if not store_id:
        return []
    return list(qs.filter(store_id=store_id).order_by("-audit_date", "-created_at")[:200])


@portal_required
def compare(request):
    client, scope, pickers = resolve(request)
    qs = Audit.objects.for_scope(scope).select_related("store", "previous_audit")
    stores = scope_stores(client, scope)

    def pick(param):
        v = request.GET.get(param)
        if not v:
            return None
        try:
            return get_object_or_404(qs, pk=uuid.UUID(v))
        except ValueError:
            raise Http404

    a, b = pick("a"), pick("b")
    if b is not None and a is None and b.previous_audit_id and qs.filter(pk=b.previous_audit_id).exists():
        a = qs.get(pk=b.previous_audit_id)
    # store-level selectors: picking a store selects its latest audit
    for side in ("a", "b"):
        sid = request.GET.get(f"{side}_store")
        if sid and not request.GET.get(side):
            try:
                latest = qs.filter(store_id=uuid.UUID(sid)).order_by("-audit_date", "-created_at").first()
            except ValueError:
                latest = None
            if side == "b":
                b = latest
            else:
                a = latest
    quick = []
    latest_by_store = {au.store_id: au for au in qs.filter(previous_audit__isnull=False)
                       .order_by("store_id", "-audit_date", "-created_at").distinct("store_id")}
    for s in stores:
        lat = latest_by_store.get(s.pk)
        if lat is not None:
            quick.append({"store": s, "a": lat.previous_audit_id, "b": lat.pk})
    if a is None and b is None and quick:
        b = qs.get(pk=quick[0]["b"])
        a = qs.get(pk=quick[0]["a"])
    cmp = None
    chart = None
    if a is not None and b is not None and a.pk != b.pk:
        cmp = services.comparison_for(a, b)
        chart = charts.paired_bars([{"category": r.category, "a_pct": r.a.var_pct, "b_pct": r.b.var_pct,
                                     "a_diff": r.a.diff_value, "b_diff": r.b.diff_value} for r in cmp.categories],
                                   "A", "B")
    ctx = base_ctx(request, client, scope, pickers, a=a, b=b, cmp=cmp, chart=chart, quick=quick, stores=stores,
                   a_options=_store_options(qs, a.store_id if a else request.GET.get("a_store")),
                   b_options=_store_options(qs, b.store_id if b else request.GET.get("b_store")),
                   a_obs=list(a.observations.select_related("category")) if a else [],
                   b_obs=list(b.observations.select_related("category")) if b else [])
    return render(request, "portal/compare.html", ctx)


# ---------------------------------------------------------------- aging


def aging_inputs(request, client, scope):
    t = today()
    qs = Audit.objects.for_scope(scope)
    first = qs.order_by("audit_date").values_list("audit_date", flat=True).first()
    period = calc.Period.parse(request.GET.get("period"), t)
    choices = calc.period_choices(t, first.year if first else None)
    if period not in choices:
        choices.append(period)
    stores = scope_stores(client, scope)
    store = None
    if request.GET.get("store"):
        try:
            store = next((s for s in stores if s.pk == uuid.UUID(request.GET["store"])), None)
        except ValueError:
            store = None
    return qs, period, choices, stores, store, t


@portal_required
def aging(request):
    client, scope, pickers = resolve(request)
    qs, period, choices, stores, store, t = aging_inputs(request, client, scope)
    report = build_aging(client, qs, stores, period, t, store)
    ctx = base_ctx(request, client, scope, pickers, report=report, period=period, period_choices=choices,
                   stores=stores, store=store)
    return render(request, "portal/aging.html", ctx)
