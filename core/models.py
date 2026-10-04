import uuid
from decimal import Decimal

from django.core.cache import cache
from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(TimeStamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class AppSettings(TimeStamped):
    """Single-row global settings."""

    good_pct = models.DecimalField("Healthy up to (%)", max_digits=5, decimal_places=2, default=Decimal("1.00"))
    warn_pct = models.DecimalField("Watch up to (%)", max_digits=5, decimal_places=2, default=Decimal("2.00"))
    cycle_days = models.PositiveIntegerField("Audit cycle (days)", default=90)
    soon_days = models.PositiveIntegerField("Due-soon window (days)", default=15)
    auditor_can_publish = models.BooleanField("Auditors can publish audits", default=False)
    session_timeout_minutes = models.PositiveIntegerField("Session timeout (minutes)", default=30)
    signed_url_minutes = models.PositiveIntegerField("File link expiry (minutes)", default=10)
    notify_new_audit = models.BooleanField("Email clients when an audit is published", default=True)
    notify_monthly_summary = models.BooleanField("Send the monthly summary on the 1st", default=True)
    notify_missing_files = models.BooleanField("Send the daily missing-files reminder", default=True)
    last_backup_at = models.DateTimeField(null=True, blank=True)
    last_backup_note = models.CharField(max_length=255, blank=True)

    CACHE_KEY = "audix-app-settings"

    class Meta:
        verbose_name = "app settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    @classmethod
    def load(cls) -> "AppSettings":
        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, 30)
        return obj
