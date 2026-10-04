"""Branded emails. Sent synchronously; failures are logged and never break a request."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from core.formatting import fmt_date
from core.models import AppSettings

logger = logging.getLogger(__name__)


def absolute(path: str) -> str:
    base = settings.PORTAL_BASE_URL or ""
    return f"{base}{path}" if base else path


def send(subject: str, to: list[str], template: str, context: dict) -> int:
    to = [t for t in to if t]
    if not to:
        return 0
    ctx = {"portal_url": absolute("/"), **context}
    try:
        text = render_to_string(f"emails/{template}.txt", ctx)
        html = render_to_string(f"emails/{template}.html", ctx)
        msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to)
        msg.attach_alternative(html, "text/html")
        return msg.send(fail_silently=False)
    except Exception:
        logger.exception("Email %s could not be sent", template)
        return 0


def send_new_audit(audit) -> int:
    """'New audit uploaded' to the client's contact emails. Never attaches files."""
    client = audit.client
    if not (client.notify_on_publish and AppSettings.load().notify_new_audit and client.is_active):
        return 0
    recipients = client.email_list()
    if not recipients:
        return 0
    ctx = {"audit": audit, "client": client, "link": absolute(reverse("accounts:login"))}
    subject = f"New audit: {audit.store.name}, {fmt_date(audit.audit_date)} ({audit.reference})"
    return len(recipients) if send(subject, recipients, "new_audit", ctx) else 0


def month_bounds(year: int, month: int):
    import calendar
    from datetime import date

    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def send_monthly_summary(client, year: int, month: int) -> int:
    """Last month's audits, shortage, excess and delayed audits for one client."""
    from audits.models import Audit
    from reports.queries import window_totals

    if not (AppSettings.load().notify_monthly_summary and client.is_active):
        return 0
    start, end = month_bounds(year, month)
    qs = Audit.objects.filter(client=client, status="published", audit_date__gte=start, audit_date__lte=end)
    totals = window_totals(qs)
    delayed = list(qs.filter(delay_days__gt=0).select_related("store").order_by("audit_date"))
    ctx = {"client": client, "start": start, "end": end, "t": totals, "delayed": delayed,
           "month_label": start.strftime("%B %Y"), "link": absolute(reverse("accounts:login"))}
    return send(f"Audix Vault monthly summary: {start:%B %Y}", client.email_list(), "monthly_summary", ctx)


def send_missing_files_reminder() -> int:
    """Daily reminder to admins and auditors about audits waiting for files."""
    from accounts.models import STAFF_ROLES, User
    from reports.console_views import waiting_for_files

    if not AppSettings.load().notify_missing_files:
        return 0
    waiting = waiting_for_files()
    if not waiting:
        return 0
    to = list(User.objects.filter(role__in=list(STAFF_ROLES), is_active=True).exclude(email="")
              .values_list("email", flat=True))
    ctx = {"waiting": waiting[:50], "total": len(waiting), "link": absolute(reverse("console:overview"))}
    return send(f"Audix Vault: {len(waiting)} audit(s) waiting for files", to, "missing_files", ctx)
