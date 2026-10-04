from decimal import Decimal

from django.conf import settings
from django.db import models

from core.calc import Thresholds
from core.models import AppSettings, UUIDModel

DEFAULT_CATEGORIES = [
    "Grocery & staples",
    "Dairy & frozen",
    "Beverages",
    "Snacks & confectionery",
    "Personal care",
    "Home care",
    "General merchandise",
]


class Client(UUIDModel):
    name = models.CharField("Company name", max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    logo_key = models.CharField(max_length=500, blank=True)
    contact_emails = models.TextField("Contact emails", blank=True, help_text="One or more, separated by commas.")
    is_active = models.BooleanField(default=True)
    good_pct = models.DecimalField("Healthy up to (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    warn_pct = models.DecimalField("Watch up to (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    cycle_days = models.PositiveIntegerField("Audit cycle (days)", default=90)
    soon_days = models.PositiveIntegerField("Due-soon window (days)", default=15)
    notify_on_publish = models.BooleanField("Email them when a new audit is published", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def thresholds(self) -> Thresholds:
        app = AppSettings.load()
        good = self.good_pct if self.good_pct is not None else app.good_pct
        warn = self.warn_pct if self.warn_pct is not None else app.warn_pct
        return Thresholds(
            good_pct=Decimal(good), warn_pct=Decimal(warn), cycle_days=self.cycle_days, soon_days=self.soon_days
        )

    def email_list(self):
        raw = (self.contact_emails or "").replace(";", ",").replace("\n", ",")
        return [e.strip() for e in raw.split(",") if e.strip()]


class Store(UUIDModel):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="stores")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    city = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["client", "code"], name="uniq_store_code_per_client")]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def label(self):
        return f"{self.name}, {self.city}" if self.city else self.name

    @property
    def place(self):
        return self.city or self.name

    @property
    def option_label(self):
        """Label used in store dropdowns: "CODE · Name, City"."""
        return f"{self.code} · {self.name}, {self.city}" if self.city else f"{self.code} · {self.name}"


class Category(UUIDModel):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(fields=["client", "name"], name="uniq_category_per_client")]

    def __str__(self):
        return self.name
