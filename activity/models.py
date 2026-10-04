from django.conf import settings
from django.db import models


class ActionType(models.TextChoices):
    LOGIN = "login", "Login"
    LOGOUT = "logout", "Logout"
    LOGIN_FAILED = "login_failed", "Login failed"
    DOWNLOAD = "download", "Download"
    VIEW = "view", "View"
    UPLOAD = "upload", "Upload"
    ADMIN_CHANGE = "admin_change", "Admin"
    EXPORT = "export", "Export"


class ActivityLog(models.Model):
    """Append-only record of who did what."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user_label = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=20, blank=True)
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    action = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    audit = models.ForeignKey("audits.Audit", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    client = models.ForeignKey("clients.Client", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["client", "created_at"], name="activity_client_created")]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("_allow_update", False):
            raise RuntimeError("Activity log rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Activity log rows are append-only.")
