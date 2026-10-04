"""Write activity-log rows. Never pass passwords or file contents here."""

import logging

from activity.models import ActivityLog

logger = logging.getLogger(__name__)


def client_ip(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")
    return ip or None


def log(request, action_type, action, detail="", audit=None, client=None, user=None, user_label=None):
    if user is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        user = request.user
    if client is None and audit is not None:
        client = audit.client
    if client is None and user is not None and getattr(user, "client_id", None):
        client = user.client
    if request is not None and getattr(request, "portal", None) and request.portal.impersonating:
        detail = (detail + " (viewed as client)").strip()
    try:
        return ActivityLog.objects.create(
            user=user,
            user_label=user_label or (user.label if user else ""),
            role=getattr(user, "role", "") if user else "",
            action_type=action_type,
            action=action[:200],
            detail=detail,
            audit=audit,
            client=client,
            ip=client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else ""),
        )
    except Exception:  # never break a request because of logging
        logger.exception("Could not write activity log")
        return None
