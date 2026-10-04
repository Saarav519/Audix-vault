"""Admin console overview."""

from datetime import timedelta

from django.db.models import Count, Max, Q
from django.shortcuts import render
from django.utils import timezone

from accounts.models import CLIENT_ROLES
from activity.models import ActionType, ActivityLog
from audits.models import Audit, AuditStatus, FileKind
from clients.models import Client, Store
from core.permissions import staff_required

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


@staff_required
def overview(request):
    today = timezone.localdate()
    active_clients = Client.objects.filter(is_active=True)
    published = Audit.objects.filter(status=AuditStatus.PUBLISHED, client__is_active=True)
    waiting = waiting_for_files(Audit.objects.live().filter(client__is_active=True))

    # stores above the Watch threshold, judged on each store's latest published audit
    latest_ids = (published.values("store_id").annotate(last=Max("audit_date")))
    latest_map = {r["store_id"]: r["last"] for r in latest_ids}
    above = 0
    thresholds = {c.pk: c.thresholds() for c in active_clients}
    seen = set()
    for a in published.filter(store_id__in=list(latest_map)).order_by("store_id", "-audit_date", "-created_at").only(
            "store_id", "client_id", "audit_date", "var_pct_stock"):
        if a.store_id in seen or latest_map.get(a.store_id) != a.audit_date:
            continue
        seen.add(a.store_id)
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
    ctx = {
        "kpis": kpis, "clients": clients, "waiting": waiting[:25], "waiting_total": len(waiting),
        "activity": ActivityLog.objects.select_related("audit")[:15],
    }
    return render(request, "console/overview.html", ctx)
