from django.conf import settings

from core.models import AppSettings


def portal(request):
    user = getattr(request, "user", None)
    scope = getattr(request, "portal", None)
    ctx = {"PORTAL_BASE_URL": settings.PORTAL_BASE_URL, "DEBUG": settings.DEBUG}
    if user is not None and user.is_authenticated:
        ctx["portal_scope"] = scope
        ctx["portal_client"] = scope.client if scope is not None and (user.is_client_user or scope.impersonating) else None
        ctx["in_client_mode"] = bool(user.is_client_user or (scope is not None and scope.impersonating))
        ctx["app_settings"] = AppSettings.load()
    return ctx
