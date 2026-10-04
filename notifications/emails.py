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
