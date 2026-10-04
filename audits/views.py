"""Staff screens: add / edit audit (six steps), live preview, drafts, list, publish, delete."""

from __future__ import annotations

import uuid
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from activity.log import log
from activity.models import ActionType
from audits import entry, services
from audits.models import Audit, AuditStatus, AuditType, FileKind, ObservationKind, Severity, Shift
from clients.models import Client, Store
from core import calc, storage
from core.models import AppSettings
from core.permissions import admin_required, staff_required


def can_publish_role(user) -> bool:
    return user.is_admin or (user.is_auditor and AppSettings.load().auditor_can_publish)


def loose_header(data, client) -> dict:
    """Best-effort header for the live preview (no validation errors)."""
    store = None
    sid = data.get("store")
    if sid:
        try:
            store = Store.objects.filter(client=client, pk=sid).first()
        except (ValueError, Exception):
            store = None
    try:
        d = date.fromisoformat(data.get("audit_date") or "")
    except ValueError:
        d = None
    sale, err = entry.parse_decimal(data.get("sale_value"), 2)
    audit_type = data.get("audit_type")
    return {"store": store, "audit_date": d,
            "audit_type": audit_type if audit_type in AuditType.values else AuditType.PHYSICAL,
            "sale_value": sale if (sale and not err) else None}


def _entry_context(client, audit, header_form, state, preview, user):
    obs_rows = list(enumerate(state.observations))
    return {
        "client": client, "audit": audit, "form": header_form, "state": state, "rows": state.rows(),
        "preview": preview, "obs_rows": obs_rows, "next_index": len(obs_rows),
        "categories": state.categories, "kinds": ObservationKind.choices, "severities": Severity.choices,
        "fields": calc.FIELDS, "can_publish": can_publish_role(user),
        "storage_ready": storage.is_configured(),
        "file_kinds": FILE_KIND_ROWS,
        "files": list(audit.files.active().order_by("kind", "sort_order", "uploaded_at")) if audit else [],
        "is_published": bool(audit and audit.is_published),
    }


FILE_KIND_ROWS = [
    (FileKind.AUDIT_EXCEL, "Audit Excel", ".xlsx, .xls, .csv up to 25 MB", False),
    (FileKind.VARIANCE_REPORT, "Variance report", ".xlsx, .xls, .csv up to 25 MB", False),
    (FileKind.SCANNED_DATA, "Scanned data", ".pdf, .jpg, .png up to 50 MB", False),
    (FileKind.SIGNOFF, "Signoff copy", ".pdf, .jpg, .png up to 25 MB", False),
    (FileKind.PHOTO, "Evidence photographs", ".jpg, .png, .webp, .heic up to 15 MB each, 30 at most", True),
]


@staff_required
def audit_new(request):
    cid = request.GET.get("client") or request.POST.get("client")
    client = None
    if cid:
        try:
            client = Client.objects.filter(pk=cid, is_active=True).first()
        except (ValueError, Exception):
            client = None
    if client is None:
        clients = Client.objects.filter(is_active=True).order_by("name")
        return render(request, "audits/pick_client.html", {"clients": clients})
    return _entry(request, client, None)


@staff_required
def audit_edit(request, pk):
    audit = get_object_or_404(Audit.objects.live().select_related("client", "store"), pk=pk)
    if audit.is_published and not request.user.is_admin:
        raise PermissionDenied
    return _entry(request, audit.client, audit)


def _entry(request, client, audit):
    user = request.user
    categories = entry.categories_for(client, audit)
    if request.method == "POST":
        form = entry.HeaderForm(request.POST, client=client, audit=audit)
        state = entry.state_from_post(request.POST, categories)
        action = request.POST.get("action", "draft")
        valid = form.is_valid()
        if not state.lines():
            state.errors.append("Enter the numbers for at least one category.")
        if action == "publish" and not can_publish_role(user):
            action = "draft"
            messages.warning(request, "Your role cannot publish audits, so it was saved as a draft for an admin.")
        if valid and not state.errors:
            was_published = bool(audit and audit.is_published)
            before = entry.audit_fingerprint(audit) if was_published else None
            creating = audit is None
            audit = entry.save_audit(client, form.cleaned_data, state, user, audit)
            if before is not None:
                changes = entry.describe_changes(before, entry.audit_fingerprint(audit))
                log(request, ActionType.ADMIN_CHANGE, "Edited published audit", detail=changes or "No field changes",
                    audit=audit)
            else:
                log(request, ActionType.ADMIN_CHANGE, "Created draft audit" if creating else "Saved draft audit",
                    detail=audit.reference, audit=audit)
            if action == "publish" and not was_published:
                try:
                    services.publish(audit, user)
                except services.PublishError as e:
                    messages.error(request, f"Saved as a draft, not published. {e}")
                    return redirect("audits:edit", audit.pk)
                log(request, ActionType.ADMIN_CHANGE, "Published audit", detail=audit.reference, audit=audit)
                from notifications.emails import send_new_audit

                sent = send_new_audit(audit)
                note = f" The client has been emailed ({sent})." if sent else ""
                messages.success(request, f"{audit.reference} is published.{note}")
                return redirect("audits:detail", audit.pk)
            if was_published:
                messages.success(request, f"Changes to {audit.reference} are saved and logged.")
                return redirect("audits:detail", audit.pk)
            messages.success(request, f"Draft {audit.reference} saved. Clients cannot see drafts.")
            return redirect("audits:edit", audit.pk)
        header = loose_header(request.POST, client)
    else:
        if audit is not None:
            form = entry.HeaderForm(client=client, audit=audit, initial={
                "store": audit.store_id, "audit_date": audit.audit_date.isoformat(), "audit_type": audit.audit_type,
                "shift": audit.shift, "sale_value": audit.sale_value, "note": audit.note})
            state = entry.state_from_audit(audit, categories)
            header = {"store": audit.store, "audit_date": audit.audit_date, "audit_type": audit.audit_type,
                      "sale_value": audit.sale_value}
        else:
            store_id = request.GET.get("store")
            form = entry.HeaderForm(client=client, initial={
                "audit_date": date.today().isoformat(), "audit_type": AuditType.PHYSICAL, "shift": Shift.MORNING,
                "store": store_id})
            state = entry.new_state(categories)
            header = loose_header({"store": store_id or "", "audit_date": date.today().isoformat(),
                                   "audit_type": AuditType.PHYSICAL}, client)
    preview = entry.build_preview(client, header, state, audit)
    return render(request, "audits/entry.html", _entry_context(client, audit, form, state, preview, user))


@staff_required
@require_POST
def audit_preview(request):
    client = get_object_or_404(Client, pk=request.POST.get("client"))
    audit = None
    if request.POST.get("audit"):
        audit = Audit.objects.live().filter(pk=request.POST["audit"], client=client).first()
    categories = entry.categories_for(client, audit)
    state = entry.state_from_post(request.POST, categories)
    header = loose_header(request.POST, client)
    preview = entry.build_preview(client, header, state, audit)
    prev_id = str(preview["previous"].pk) if preview["previous"] else ""
    ctx = {"preview": preview, "rows": state.rows(), "client": client, "audit": audit,
           "followups_changed": prev_id != request.POST.get("prev_shown", ""), "prev_id": prev_id}
    return render(request, "audits/partials/preview.html", ctx)


@staff_required
@require_POST
def suggest_drafts(request):
    client = get_object_or_404(Client, pk=request.POST.get("client"))
    audit = None
    if request.POST.get("audit"):
        audit = Audit.objects.live().filter(pk=request.POST["audit"], client=client).first()
    categories = entry.categories_for(client, audit)
    state = entry.state_from_post(request.POST, categories)
    by_name = {c.name: c for c in categories}
    drafts = calc.suggest_drafts(audit.pk if audit else "new", state.numbers(), services.observation_templates())
    try:
        start = int(request.POST.get("next_index", "0"))
    except ValueError:
        start = 0
    rows = []
    for i, d in enumerate(drafts):
        cat = by_name.get(d["category"]) if d["category"] else None
        rows.append((start + i, {"category": cat, "category_id": str(cat.pk) if cat else "", "kind": d["kind"],
                                 "severity": d["severity"], "text": d["text"], "recommendation": d["recommendation"]}))
    return render(request, "audits/partials/obs_cards.html", {
        "obs_rows": rows, "categories": categories, "kinds": ObservationKind.choices, "severities": Severity.choices})


def admin_audits(params):
    """The admin Audits list filters: client and status here, the rest shared with the portal list.
    Returns (queryset, filter values, chosen client, that client's stores)."""
    from reports.views import apply_audit_filters

    qs = Audit.objects.live().select_related("client", "store")
    client = None
    cid = (params.get("client") or "").strip()
    if cid:
        try:
            client = Client.objects.filter(pk=uuid.UUID(cid)).first()
        except ValueError:
            client = None
    stores = list(client.stores.order_by("code")) if client else []
    params = params.copy()
    if not any(str(s.pk) == params.get("store") for s in stores):
        params["store"] = ""  # the store filter only applies within the chosen client
    if client:
        qs = qs.filter(client=client)
    status = params.get("status", "")
    if status in (AuditStatus.DRAFT, AuditStatus.PUBLISHED):
        qs = qs.filter(status=status)
    else:
        status = ""
    qs, f = apply_audit_filters(qs, params, search_client_name=True)
    f.update(client=str(client.pk) if client else "", status=status)
    return qs, f, client, stores


def describe_admin_filters(f, client, stores):
    from reports.views import describe_audit_filters

    extra = [("Client", client.name if client else "All clients")]
    if f["status"]:
        extra.append(("Status", dict(AuditStatus.choices)[f["status"]]))
    else:
        extra.append(("Status", "Drafts and published"))
    return describe_audit_filters(f, stores, extra)


@staff_required
def audit_list(request):
    from reports.views import PERIODS

    qs, f, client, stores = admin_audits(request.GET)
    qs = qs.annotate(
        signoff_count=Count("files", filter=Q(files__kind=FileKind.SIGNOFF, files__is_deleted=False)),
        photo_count=Count("files", filter=Q(files__kind=FileKind.PHOTO, files__is_deleted=False)),
    )
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "audits/list.html", {
        "page": page, "clients": Client.objects.order_by("name"), "f": f, "stores": stores,
        "types": AuditType.choices, "shifts": Shift.choices, "periods": PERIODS,
        "export_query": urlencode({k: v for k, v in f.items() if v}),
    })


@staff_required
def audit_export(request, fmt):
    """Excel or PDF of exactly the audits the admin list shows, across all pages."""
    from reports.exports import export_audits

    if fmt not in ("xlsx", "pdf"):
        raise Http404
    qs, f, client, stores = admin_audits(request.GET)
    filters = describe_admin_filters(f, client, stores)
    return export_audits(request, fmt, qs, filters, client.name if client else "All clients",
                         (client.slug or "client") if client else "all-clients", client=client,
                         include_client=client is None)


@admin_required
@require_POST
def audit_delete(request, pk):
    audit = get_object_or_404(Audit.objects.live(), pk=pk)
    services.soft_delete(audit)
    log(request, ActionType.ADMIN_CHANGE, "Deleted audit", detail=audit.reference, audit=audit)
    messages.success(request, f"{audit.reference} has been deleted. It is kept in the database but hidden.")
    return redirect("audits:list")


@staff_required
@require_POST
def audit_publish(request, pk):
    audit = get_object_or_404(Audit.objects.live(), pk=pk)
    if not can_publish_role(request.user):
        raise PermissionDenied
    if audit.is_published:
        return redirect("audits:detail", audit.pk)
    try:
        services.publish(audit, request.user)
    except services.PublishError as e:
        messages.error(request, str(e))
        return redirect("audits:edit", audit.pk)
    log(request, ActionType.ADMIN_CHANGE, "Published audit", detail=audit.reference, audit=audit)
    from notifications.emails import send_new_audit

    send_new_audit(audit)
    messages.success(request, f"{audit.reference} is published.")
    return redirect(reverse("audits:detail", args=[audit.pk]))

