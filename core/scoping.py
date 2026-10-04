"""Who is looking at the client portal, and which client's data they may see."""

from dataclasses import dataclass

from clients.models import Client

VIEW_AS_SESSION_KEY = "view_as_client_id"


@dataclass
class PortalScope:
    client: Client | None
    store_ids: set | None = None  # None means every store
    impersonating: bool = False
    is_staff_view: bool = False  # staff browsing reports with a client picker


def scope_for(request, client_param=None) -> PortalScope:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return PortalScope(None)
    if user.is_client_user:
        client = user.client if (user.client_id and user.client.is_active) else None
        stores = None
        if user.role == "client_store":
            stores = set(user.allowed_stores.values_list("pk", flat=True))
        return PortalScope(client, stores)
    if user.is_staff_member:
        cid = request.session.get(VIEW_AS_SESSION_KEY)
        if cid and user.is_admin:
            client = Client.objects.filter(pk=cid).first()
            if client:
                return PortalScope(client, None, impersonating=True)
        if client_param:
            client = Client.objects.filter(pk=client_param).first() if _is_uuid(client_param) else None
            return PortalScope(client, None, is_staff_view=True)
        return PortalScope(None, None, is_staff_view=True)
    return PortalScope(None)


def _is_uuid(value):
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
