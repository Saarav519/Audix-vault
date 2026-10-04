"""Audit detail (client and staff), file upload / download / preview / ZIP."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db.models import Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from activity.log import log
from activity.models import ActionType
from audits import files as file_rules
from audits import services
from audits.models import Audit, AuditFile, FileKind
from core import calc, storage
from core.formatting import fmt_date
from core.permissions import staff_required

CONFIRM_SALT = "audix.confirm"


def viewable_audits(request):
    """The one queryset every audit-facing endpoint starts from."""
    user = request.user
    scope = request.portal
    if user.is_client_user or scope.impersonating:
        return Audit.objects.for_scope(scope)
    if user.is_staff_member:
        return Audit.objects.live()
    return Audit.objects.none()


def get_viewable_audit(request, pk) -> Audit:
    return get_object_or_404(viewable_audits(request).select_related("client", "store", "previous_audit"), pk=pk)


def is_client_view(request) -> bool:
    return request.user.is_client_user or request.portal.impersonating


# ---------------------------------------------------------------- detail


def detail_context(request, audit: Audit) -> dict:
    client_view = is_client_view(request)
    th = audit.client.thresholds()
    previous = services.audit_previous(audit)
    if previous is not None and client_view and not viewable_audits(request).filter(pk=previous.pk).exists():
        previous = None
    comparison = services.comparison_for(previous, audit) if previous else None
    nums = audit.numbers_by_category()
    totals = calc.audit_totals(nums)
    prev_lines = comparison.a.lines if comparison else {}
    by_cat = {r.category: r for r in comparison.categories} if comparison else {}
    cat_rows = [{"n": n, "prev": prev_lines.get(n.category),
                 "change": by_cat[n.category].change_value if n.category in by_cat else None,
                 "change_units": by_cat[n.category].change_units if n.category in by_cat else None,
                 "status": calc.status_for(n.var_pct, th)} for n in nums]
    cat_total = calc.sheet_total(nums, comparison.a.totals if comparison else None)
    files = list(audit.files.active().order_by("kind", "sort_order", "uploaded_at"))
    by_kind = {k: [f for f in files if f.kind == k] for k in FileKind.values}
    total_bytes = sum(f.size_bytes for f in files)
    photo_bytes = sum(f.size_bytes for f in by_kind[FileKind.PHOTO])
    sale_period = None
    if previous is not None:
        days = (audit.audit_date - previous.audit_date).days
        sale_period = f"{fmt_date(previous.audit_date)} to {fmt_date(audit.audit_date)} ({days} days)"
    followups = list(audit.followups.select_related("previous_observation__category"))
    edits = []
    if not client_view:
        from activity.models import ActivityLog

        edits = list(ActivityLog.objects.filter(audit=audit, action_type=ActionType.ADMIN_CHANGE)[:20])
    return {
        "audit": audit, "client_view": client_view, "previous": previous, "cmp": comparison, "totals": totals,
        "cat_rows": cat_rows, "cat_total": cat_total, "observations": list(audit.observations.select_related("category")),
        "followups": followups, "files": by_kind, "signoffs": by_kind[FileKind.SIGNOFF],
        "photos": by_kind[FileKind.PHOTO],
        "reports": [(k, label, by_kind[k]) for k, label in (
            (FileKind.AUDIT_EXCEL, "Audit Excel"), (FileKind.VARIANCE_REPORT, "Variance report"),
            (FileKind.SCANNED_DATA, "Scanned data"))],
        "zip_too_big": total_bytes > settings.MAX_ZIP_BYTES, "photos_zip_too_big": photo_bytes > settings.MAX_ZIP_BYTES,
        "has_files": bool(files), "sale_period": sale_period, "thresholds": th, "edits": edits,
        "storage_ready": storage.is_configured(),
        "aging_text": calc.aging_text(audit.aging_days), "delay_text": calc.delay_text(audit.aging_days, audit.delay_days),
    }


@never_cache
@login_required
def audit_detail(request, pk):
    audit = get_viewable_audit(request, pk)
    log(request, ActionType.VIEW, "Viewed audit", detail=audit.reference, audit=audit)
    ctx = detail_context(request, audit)
    if request.GET.get("panel") == "1":
        return render(request, "audits/partials/detail_body.html", ctx)
    return render(request, "audits/detail.html", ctx)


@never_cache
@login_required
def signoff_sheet(request, pk):
    """One-page sign-off sheet PDF. Staff: drafts and published; clients: their own published audits."""
    from django.conf import settings as dj_settings
    from django.urls import reverse

    from reports import signoff

    audit = get_viewable_audit(request, pk)
    path = reverse("audits:detail", args=[audit.pk])
    portal_url = f"{dj_settings.PORTAL_BASE_URL}{path}" if dj_settings.PORTAL_BASE_URL else request.build_absolute_uri(path)
    data = signoff.signoff_pdf(audit, portal_url)
    log(request, ActionType.EXPORT, "Downloaded sign-off sheet", detail=audit.reference, audit=audit,
        client=audit.client)
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{signoff.filename(audit)}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


# ---------------------------------------------------------------- downloads


def _issue(request, audit, f: AuditFile, inline: bool, key: str | None = None):
    backend = storage.get_backend()
    if backend is None:
        raise Http404
    minutes = storage.signed_minutes()
    url = backend.download_url(key or f.storage_key, f.original_name, inline, f.content_type, minutes)
    log(request, ActionType.VIEW if inline else ActionType.DOWNLOAD,
        f"{'Previewed' if inline else 'Downloaded'} {f.get_kind_display().lower()}",
        detail=f"{audit.reference}: {f.original_name}", audit=audit)
    resp = HttpResponseRedirect(url)
    resp["Cache-Control"] = "private, no-store"
    return resp


def _get_file(request, pk, file_id):
    audit = get_viewable_audit(request, pk)
    f = get_object_or_404(AuditFile.objects.active(), pk=file_id, audit=audit)
    return audit, f


@never_cache
@login_required
def file_download(request, pk, file_id):
    audit, f = _get_file(request, pk, file_id)
    return _issue(request, audit, f, inline=False)


@never_cache
@login_required
def file_preview(request, pk, file_id):
    audit, f = _get_file(request, pk, file_id)
    if not f.previewable:
        raise Http404  # reports are download-only
    return _issue(request, audit, f, inline=True)


@login_required
def file_thumb(request, pk, file_id):
    audit, f = _get_file(request, pk, file_id)
    if f.kind != FileKind.PHOTO or not f.thumbnail_key:
        raise Http404
    backend = storage.get_backend()
    if backend is None:
        raise Http404
    try:
        data = backend.get(f.thumbnail_key)
    except Exception:
        raise Http404
    resp = HttpResponse(data, content_type="image/jpeg")
    resp["Cache-Control"] = "private, max-age=600"
    return resp


@never_cache
@login_required
def audit_zip(request, pk):
    audit = get_viewable_audit(request, pk)
    what = request.GET.get("what", "all")
    qs = audit.files.active().order_by("kind", "sort_order", "uploaded_at")
    if what == "photos":
        qs = qs.filter(kind=FileKind.PHOTO)
    files = list(qs)
    if not files:
        raise Http404
    total = sum(f.size_bytes for f in files)
    if total > settings.MAX_ZIP_BYTES:
        return render(request, "audits/zip_too_big.html", {"audit": audit, "limit_mb": settings.MAX_ZIP_BYTES // (1024 * 1024)},
                      status=413)
    backend = storage.get_backend()
    if backend is None:
        raise Http404
    label = "photos" if what == "photos" else "all-files"
    log(request, ActionType.DOWNLOAD, f"Downloaded ZIP ({'photographs' if what == 'photos' else 'everything'})",
        detail=f"{audit.reference}: {len(files)} files", audit=audit)
    resp = StreamingHttpResponse(file_rules.stream_zip(backend, file_rules.zip_entries(audit, files)),
                                 content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{audit.reference}-{label}.zip"'
    resp["Cache-Control"] = "private, no-store"
    return resp


# ---------------------------------------------------------------- uploads (staff)


def _staff_audit(request, pk):
    return get_object_or_404(Audit.objects.live(), pk=pk)


@staff_required
@require_POST
def upload_presign(request, pk):
    audit = _staff_audit(request, pk)
    backend = storage.get_backend()
    if backend is None:
        return JsonResponse({"error": "File storage is not configured yet"}, status=503)
    kind = request.POST.get("kind", "")
    name = (request.POST.get("name") or "")[:255]
    try:
        size = int(request.POST.get("size", "0"))
    except ValueError:
        size = 0
    content_type, error = file_rules.validate_upload(kind, name, size)
    if error:
        return JsonResponse({"error": error}, status=400)
    if kind == FileKind.PHOTO and audit.files.active().filter(kind=FileKind.PHOTO).count() >= file_rules.MAX_PHOTOS:
        return JsonResponse({"error": f"An audit can have at most {file_rules.MAX_PHOTOS} photographs."}, status=400)
    key = storage.build_key(audit.client_id, audit.pk, kind, name)
    rule = file_rules.RULES[kind]
    p = backend.presign_upload(key, content_type, rule.max_bytes, storage.signed_minutes())
    token = signing.dumps({"a": str(audit.pk), "k": key, "kind": kind, "n": name, "ct": content_type, "s": size},
                          salt=CONFIRM_SALT)
    return JsonResponse({**p, "content_type": content_type, "token": token})


@staff_required
@require_POST
def upload_confirm(request, pk):
    audit = _staff_audit(request, pk)
    backend = storage.get_backend()
    if backend is None:
        return JsonResponse({"error": "File storage is not configured yet"}, status=503)
    try:
        data = signing.loads(request.POST.get("token", ""), salt=CONFIRM_SALT, max_age=60 * 60)
    except signing.BadSignature:
        return JsonResponse({"error": "Upload expired. Please try again."}, status=400)
    if data["a"] != str(audit.pk):
        return JsonResponse({"error": "Wrong audit."}, status=400)
    head = backend.head(data["k"])
    if head is None:
        return JsonResponse({"error": "The file did not reach storage. Please retry."}, status=400)
    rule = file_rules.RULES[data["kind"]]
    if head["size"] > rule.max_bytes or head["size"] <= 0 or head["size"] != data["s"]:
        backend.delete(data["k"])
        return JsonResponse({"error": "The stored file does not match the upload."}, status=400)
    if head["content_type"] and head["content_type"] != data["ct"]:
        backend.delete(data["k"])
        return JsonResponse({"error": "The stored file type does not match."}, status=400)
    key, ct, size, name, thumb = data["k"], data["ct"], head["size"], data["n"], ""
    if data["kind"] == FileKind.PHOTO:
        try:
            key, ct, size, name, thumb = file_rules.process_photo(backend, key, ct, name)
        except Exception:
            backend.delete(data["k"])
            return JsonResponse({"error": "This photo could not be read. Upload a JPEG or PNG."}, status=400)
    order = (audit.files.filter(kind=data["kind"]).aggregate(m=Sum("sort_order"))["m"] or 0) + 1
    f = AuditFile.objects.create(audit=audit, kind=data["kind"], storage_key=key, original_name=name,
                                 content_type=ct, size_bytes=size, thumbnail_key=thumb, uploaded_by=request.user,
                                 sort_order=order, caption=(request.POST.get("caption") or "")[:200])
    log(request, ActionType.UPLOAD, f"Uploaded {f.get_kind_display().lower()}", detail=f"{audit.reference}: {name}",
        audit=audit)
    return JsonResponse({"ok": True, "id": str(f.pk), "name": name})


@staff_required
@require_POST
def file_delete(request, pk, file_id):
    audit = _staff_audit(request, pk)
    f = get_object_or_404(AuditFile.objects.active(), pk=file_id, audit=audit)
    if audit.is_published and not request.user.is_admin:
        raise Http404
    f.is_deleted = True
    f.save(update_fields=["is_deleted", "updated_at"])
    log(request, ActionType.ADMIN_CHANGE, "Removed file", detail=f"{audit.reference}: {f.original_name}", audit=audit)
    from django.shortcuts import redirect

    return redirect("audits:edit", audit.pk)
