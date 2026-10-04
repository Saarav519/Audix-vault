from django.contrib.auth.backends import ModelBackend


class LoginIdBackend(ModelBackend):
    """Login ID (case-insensitive). Users of a disabled client cannot sign in."""

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        if user.is_client_user:
            return bool(user.client_id and user.client.is_active)
        return True
