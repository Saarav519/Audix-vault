from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache


@never_cache
def healthz(request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return JsonResponse({"status": "ok", "db": "ok"})
    except Exception:
        return JsonResponse({"status": "error", "db": "unavailable"}, status=503)


def home(request):
    user = request.user
    if not user.is_authenticated:
        return redirect("accounts:login")
    if user.is_staff_member and not request.portal.impersonating:
        return redirect("console:overview")
    return redirect("portal:dashboard")


def not_found(request, exception=None):
    return render(request, "404.html", status=404)


def forbidden(request, exception=None):
    return render(request, "403.html", status=403)
