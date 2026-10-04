"""Admin console overview."""

from datetime import timedelta

from django.db.models import Count, Max, Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.models import CLIENT_ROLES
from activity.models import ActionType, ActivityLog
from audits.models import Audit, AuditStatus, FileKind
from clients.models import Client, Store
from core import calc
from core.permissions import staff_required
from reports import queries

REQUIRED_FILES = [(FileKind.SIGNOFF, "Signoff"), (FileKind.PHOTO, "Photographs"),
                  (FileKind.AUDIT_EXCEL, "Audit Excel"), (FileKind.VARIANCE_REPORT, "Variance report")]


def with_file_counts(qs):
    return qs.annotate(**{f"n_{k}": Count("files", filter=Q(files__kind=k, files__is_deleted=False))
                          for k, _ in REQUIRED_FILES})


def waiting_for_files(qs=None, days=180):
    """Live audits missing a signoff, photographs, the audit Excel or the variance report."""
    since = timezone.localdate() - timedelta(days=days)
    qs = (qs if qs is not None else Audit.objects.live()).filter(audit_date__gte=since)
    qs = with_file_counts(qs.select_related("client", "store"))
    cond = Q()
    for k, _ in REQUIRED_FILES:
        cond |= Q(**{f"n_{k}": 0})
    out = []
    for a in qs.filter(cond).order_by("-audit_date")[:200]:
        a.missing = [label for k, label in REQUIRED_FILES if getattr(a, f"n_{k}") == 0]
        out.append(a)
    return out


def client_summary(clients, published, window, today):
    """Client-wise figures for the chosen period: audits, stores audited, shortage, excess, net and
    variance (same totals as the client dashboard), plus stores overdue for a full audit today."""
    rows = []
    in_window = published.filter(audit_date__gte=window.start, audit_date__lte=window.end)
    for c in clients.order_by("name"):
        qs = in_window.filter(client=c)
        t = queries.window_totals(qs)
        stores = list(Store.objects.filter(client=c, is_active=True).order_by("code"))
        due = queries.due_rows(published.filter(client=c), stores, today, c)
        th = c.thresholds()
        status = calc.status_for(t["var_pct"], th) if t["audits"] else None
        rows.append({
            "client": c, "t": t, "stores_audited": qs.values("store_id").distinct().count(),
            "stores_total": len(stores), "overdue": sum(1 for r in due if r["due"].kind == "overdue"),
            "status": status, "tone": calc.STATUS_TONE.get(status, "neutral") if status else "neutral",
            "dashboard": reverse("portal:dashboard") + f"?client={c.pk}",
        })
    total = queries.window_totals(in_window)
    return {"rows": rows, "total": total, "overdue": sum(r["overdue"] for r in rows),
            "stores_audited": sum(r["stores_audited"] for r in rows), "stores_total": sum(r["stores_total"] for r in rows)}


@staff_required
def overview(request):
    today = timezone.localdate()
    active_clients = Client.objects.filter(is_active=True)
    published = Audit.objects.filter(status=AuditStatus.PUBLISHED, client__is_active=True)
    waiting = waiting_for_files(Audit.objects.live().filter(client__is_active=True))

    # stores above the Watch threshold, judged on each store's latest published audit
    thresholds = {c.pk: c.thresholds() for c in active_clients}
    above = 0
    for a in published.order_by("store_id", "-audit_date", "-created_at").distinct("store_id").only(
            "store_id", "client_id", "var_pct_stock"):
        th = thresholds.get(a.client_id)
        if th and abs(a.var_pct_stock) > th.warn_pct:
            above += 1

    kpis = [
        ("Active clients", active_clients.count()),
        ("Stores covered", Store.objects.filter(is_active=True, client__is_active=True).count()),
        ("Audits in the last 30 days", published.filter(audit_date__gte=today - timedelta(days=29)).count()),
        ("Audits waiting for files", len(waiting)),
        ("Stores above the Watch threshold", above),
        ("Client downloads in the last 7 days", ActivityLog.objects.filter(
            action_type=ActionType.DOWNLOAD, role__in=list(CLIENT_ROLES),
            created_at__gte=timezone.now() - timedelta(days=7)).count()),
    ]
    recent90 = today - timedelta(days=89)
    clients = list(Client.objects.annotate(
        last_audit=Max("audits__audit_date", filter=Q(audits__status=AuditStatus.PUBLISHED)),
        recent=Count("audits", filter=Q(audits__status=AuditStatus.PUBLISHED, audits__audit_date__gte=recent90)),
        drafts=Count("audits", filter=Q(audits__status=AuditStatus.DRAFT)),
    ).order_by("name"))
    waiting_by_client = {}
    for a in waiting:
        if a.audit_date >= recent90 and a.is_published:
            waiting_by_client[a.client_id] = waiting_by_client.get(a.client_id, 0) + 1
    for c in clients:
        incomplete = waiting_by_client.get(c.pk, 0)
        c.completeness = round((c.recent - incomplete) / c.recent * 100) if c.recent else None
    window = calc.overview_window(request.GET.get("op", "month"), today)
    summary = client_summary(active_clients, published, window, today)
    ctx = {
        "window": window, "overview_periods": calc.OVERVIEW_PERIODS, "summary": summary,
        "kpis": kpis, "clients": clients, "waiting": waiting[:25], "waiting_total": len(waiting),
        "activity": ActivityLog.objects.select_related("audit")[:30],
    }
    return render(request, "console/overview.html", ctx)
