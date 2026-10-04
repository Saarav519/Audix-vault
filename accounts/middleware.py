from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from core.models import AppSettings

EXEMPT_PREFIXES = ("/static/", "/healthz", "/accounts/logout", "/accounts/password", "/accounts/login")


class AccountGuardMiddleware:
    """Session idle timeout, disabled-client sign-out and the first-login password change."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and not user.is_authenticated and "_auth_user_id" in getattr(request, "session", {}):
            # The backend refused the stored user (disabled login or disabled client): end the session.
            request.session.flush()
        if user is not None and user.is_authenticated:
            if not user.is_active or (user.is_client_user and not (user.client_id and user.client.is_active)):
                logout(request)
                return redirect(reverse("accounts:login"))
            minutes = AppSettings.load().session_timeout_minutes or 30
            request.session.set_expiry(minutes * 60)
            if user.must_change_password and not request.path.startswith(EXEMPT_PREFIXES):
                return redirect(reverse("accounts:password_change"))
        return self.get_response(request)
