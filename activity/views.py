from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from activity.models import ActionType, ActivityLog
from clients.models import Client
from core.permissions import admin_required

TYPE_FILTERS = {
    "login": ("Login", [ActionType.LOGIN, ActionType.LOGOUT, ActionType.LOGIN_FAILED]),
    "download": ("Download", [ActionType.DOWNLOAD]),
    "view": ("View", [ActionType.VIEW]),
    "upload": ("Upload", [ActionType.UPLOAD]),
    "admin": ("Admin", [ActionType.ADMIN_CHANGE]),
    "export": ("Export", [ActionType.EXPORT]),
}


@admin_required
def activity_list(request):
    qs = ActivityLog.objects.select_related("audit", "client")
    t = request.GET.get("type", "")
    if t in TYPE_FILTERS:
        qs = qs.filter(action_type__in=TYPE_FILTERS[t][1])
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(user_label__icontains=q) | Q(action__icontains=q) | Q(detail__icontains=q)
                       | Q(audit__reference__icontains=q))
    cid = request.GET.get("client", "")
    if cid:
        try:
            qs = qs.filter(client_id=cid)
        except Exception:
            pass
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "console/activity.html", {
        "page": page, "types": [(k, v[0]) for k, v in TYPE_FILTERS.items()], "t": t, "q": q,
        "clients": Client.objects.order_by("name"), "cid": cid})
