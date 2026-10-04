from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from activity.log import log
from activity.models import ActionType


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log(request, ActionType.LOGIN, "Signed in", user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log(request, ActionType.LOGOUT, "Signed out", user=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    login_id = (credentials or {}).get("username", "")[:64]
    log(request, ActionType.LOGIN_FAILED, "Failed sign-in", detail=f"Login ID: {login_id}", user_label=login_id)
