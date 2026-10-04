"""Local-storage endpoints (development) and the client logo."""

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from clients.models import Client
from core import storage


@csrf_exempt  # the signed, short-lived token is the authorisation (same as an S3 presigned POST)
@require_POST
@login_required
def local_upload(request, token):
    """Proxy upload endpoint: used by local storage and by S3 in "proxy" upload mode."""
    backend = storage.get_backend()
    if not (isinstance(backend, storage.LocalBackend)
            or (isinstance(backend, storage.S3Backend) and backend.upload_mode == "proxy")):
        raise Http404
    try:
        data = signing.loads(token, salt=storage.UPLOAD_SALT, max_age=storage.signed_minutes() * 60)
    except signing.BadSignature:
        return HttpResponse("Upload link expired", status=403)
    f = request.FILES.get("file")
    if f is None:
        return HttpResponseBadRequest("No file")
    if f.size > data["max"] or f.size < 1:
        return HttpResponse("File too large", status=400)
    ct = request.POST.get("Content-Type") or f.content_type
    if ct != data["ct"]:
        return HttpResponse("Content type mismatch", status=400)
    f.seek(0)
    backend.put(data["k"], f, data["ct"])
    return JsonResponse({"ok": True}, status=201)


@never_cache
@login_required
def local_serve(request, token):
    backend = storage.get_backend()
    if not isinstance(backend, storage.LocalBackend):
        raise Http404
    try:
        data = signing.loads(token, salt=storage.DOWNLOAD_SALT, max_age=storage.signed_minutes() * 60)
    except signing.BadSignature:
        raise Http404
    try:
        path = backend._path(data["k"])
    except ValueError:
        raise Http404
    if not path.exists():
        raise Http404
    resp = FileResponse(open(path, "rb"), content_type=data.get("ct") or storage.guess_type(data["fn"]))
    resp["Content-Disposition"] = storage.content_disposition(data["fn"], data["in"])
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
def client_logo(request, client_id):
    user = request.user
    try:
        client = Client.objects.get(pk=client_id)
    except (Client.DoesNotExist, ValueError):
        raise Http404
    allowed = user.is_staff_member or (user.client_id == client.pk)
    if not allowed or not client.logo_key:
        raise Http404
    backend = storage.get_backend()
    if backend is None:
        raise Http404
    try:
        data = backend.get(client.logo_key)
    except Exception:
        raise Http404
    resp = HttpResponse(data, content_type=storage.guess_type(client.logo_key))
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


