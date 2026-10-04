from django.conf import settings

from core.models import AppSettings
from core.templatetags.audix import initials


def portal(request):
    user = getattr(request, "user", None)
    scope = getattr(request, "portal", None)
    ctx = {"PORTAL_BASE_URL": settings.PORTAL_BASE_URL, "DEBUG": settings.DEBUG}
    if user is not None and user.is_authenticated:
        ctx["portal_scope"] = scope
        ctx["portal_client"] = scope.client if scope is not None and (user.is_client_user or scope.impersonating) else None
        ctx["in_client_mode"] = bool(user.is_client_user or (scope is not None and scope.impersonating))
        ctx["app_settings"] = AppSettings.load()
        client = ctx["portal_client"]
        if client is not None:
            stores = client.stores.filter(is_active=True)
            if scope.store_ids is not None:
                stores = stores.filter(pk__in=scope.store_ids)
            n = stores.count()
            cities = len({c.strip().lower() for c in stores.values_list("city", flat=True) if c.strip()})
            ctx["client_initials"] = initials(client.name)
            ctx["client_summary"] = (f"{n} store{'s' if n != 1 else ''} across {cities} cit{'ies' if cities != 1 else 'y'}"
                                     if cities else f"{n} store{'s' if n != 1 else ''}")
    return ctx
