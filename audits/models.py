from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from core import calc
from core.models import TimeStamped, UUIDModel

QTY = dict(max_digits=14, decimal_places=3, default=Decimal(0))
MONEY = dict(max_digits=14, decimal_places=2, default=Decimal(0))


class AuditType(models.TextChoices):
    PHYSICAL = "physical", "Physical stock audit"
    SURPRISE = "surprise", "Surprise audit"
    CYCLE = "cycle", "Cycle count"
    CLOSING = "closing", "Closing audit"


class Shift(models.TextChoices):
    MORNING = "morning", "Morning"
    AFTERNOON = "afternoon", "Afternoon"
    NIGHT = "night", "Night"


class AuditStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    DELETED = "deleted", "Deleted"


class Series(models.TextChoices):
    FULL = "full", "Full audit"
    OTHER = "other", "Other audit"


class AuditQuerySet(models.QuerySet):
    def live(self):
        return self.exclude(status=AuditStatus.DELETED)

    def published(self):
        return self.filter(status=AuditStatus.PUBLISHED)

    def for_scope(self, scope):
        """Read-only portal scope: one client, published audits only, optional store limit.

        This is the single place where client data isolation is enforced.
        """
        if scope is None or scope.client is None:
            return self.none()
        qs = self.filter(client_id=scope.client.pk, status=AuditStatus.PUBLISHED, client__is_active=True)
        if scope.store_ids is not None:
            qs = qs.filter(store_id__in=scope.store_ids)
        return qs

    def for_staff(self, user):
        if user is None or not user.is_authenticated or not user.is_staff_member:
            return self.none()
        return self.live()


class Audit(UUIDModel):
    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="audits")
    store = models.ForeignKey("clients.Store", on_delete=models.PROTECT, related_name="audits")
    audit_date = models.DateField()
    audit_type = models.CharField(max_length=20, choices=AuditType.choices, default=AuditType.PHYSICAL)
    shift = models.CharField(max_length=20, choices=Shift.choices, default=Shift.MORNING)
    series = models.CharField(max_length=10, choices=Series.choices, default=Series.FULL, editable=False)
    sale_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.TextField("Note from Audix", blank=True)
    status = models.CharField(max_length=12, choices=AuditStatus.choices, default=AuditStatus.DRAFT)
    reference = models.CharField(max_length=20, unique=True)

    # snapshot of computed totals (refreshed on every save and on threshold change)
    stock_qty = models.DecimalField(**QTY)
    stock_value = models.DecimalField(**MONEY)
    physical_qty = models.DecimalField(**QTY)
    physical_value = models.DecimalField(**MONEY)
    damage_qty = models.DecimalField(**QTY)
    damage_value = models.DecimalField(**MONEY)
    wbc_qty = models.DecimalField(**QTY)
    wbc_value = models.DecimalField(**MONEY)
    total_qty = models.DecimalField(**QTY)
    total_value = models.DecimalField(**MONEY)
    diff_qty = models.DecimalField(**QTY)
    diff_value = models.DecimalField(**MONEY)
    var_pct_stock = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal(0))
    var_pct_sale = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    status_label = models.CharField(max_length=12, blank=True)
    remark = models.TextField(blank=True)
    largest_shortage_category = models.CharField(max_length=80, blank=True)

    previous_audit = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    aging_days = models.IntegerField(null=True, blank=True)
    delay_days = models.IntegerField(null=True, blank=True)
    is_imported = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-audit_date", "-created_at"]
        indexes = [
            models.Index(fields=["client", "store", "audit_date"], name="audit_client_store_date"),
            models.Index(fields=["client", "audit_date"], name="audit_client_date"),
            models.Index(fields=["client", "status"], name="audit_client_status"),
        ]

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        self.series = calc.series_for(self.audit_type)
        if not self.reference:
            self.reference = next_reference(self.audit_date.year if self.audit_date else timezone.localdate().year)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------ helpers
    @property
    def is_full(self):
        return self.series == Series.FULL

    @property
    def is_published(self):
        return self.status == AuditStatus.PUBLISHED

    @property
    def is_draft(self):
        return self.status == AuditStatus.DRAFT

    @property
    def totals(self) -> calc.Numbers:
        return calc.Numbers.from_mapping(self)

    @property
    def status_tone(self):
        return calc.STATUS_TONE.get(self.status_label, "neutral")

    def numbers_by_category(self):
        lines = getattr(self, "_prefetched_objects_cache", {}).get("lines")
        if lines is None:
            lines = self.lines.select_related("category").order_by("category__sort_order", "category__name")
        return [ln.numbers() for ln in lines]

    def refresh_snapshot(self, thresholds=None, save=True):
        """Recompute totals, variance, status and remark from the saved lines."""
        th = thresholds or self.client.thresholds()
        nums = self.numbers_by_category()
        t = calc.audit_totals(nums)
        for f in calc.FIELDS:
            setattr(self, f, t.__getattribute__(f))
        self.total_qty = t.total_physical_qty
        self.total_value = t.total_physical_value
        self.diff_qty = t.diff_qty
        self.diff_value = t.diff_value
        self.var_pct_stock = calc.round_dec(t.var_pct, 4)
        sp = calc.sale_pct(t.diff_value, self.sale_value)
        self.var_pct_sale = calc.round_dec(sp, 4) if sp is not None else None
        self.status_label = calc.status_for(t.var_pct, th)
        self.remark = calc.auto_remark(t, nums, self.sale_value, th)
        worst = calc.largest_shortage(nums)
        self.largest_shortage_category = worst.category if (worst and t.is_shortage) else ""
        if save:
            Audit.objects.filter(pk=self.pk).update(
                **{f: getattr(self, f) for f in SNAPSHOT_FIELDS}, updated_at=timezone.now()
            )
        return t


SNAPSHOT_FIELDS = list(calc.FIELDS) + [
    "total_qty", "total_value", "diff_qty", "diff_value", "var_pct_stock", "var_pct_sale",
    "status_label", "remark", "largest_shortage_category",
]


class ReferenceCounter(models.Model):
    year = models.PositiveIntegerField(primary_key=True)
    last = models.PositiveIntegerField(default=0)


def next_reference(year: int) -> str:
    with transaction.atomic():
        counter, _ = ReferenceCounter.objects.select_for_update().get_or_create(year=year)
        counter.last += 1
        counter.save(update_fields=["last"])
        return f"AUD-{year}-{counter.last:04d}"


class AuditLine(TimeStamped):
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="lines")
    category = models.ForeignKey("clients.Category", on_delete=models.PROTECT, related_name="+")
    stock_qty = models.DecimalField(**QTY)
    stock_value = models.DecimalField(**MONEY)
    physical_qty = models.DecimalField(**QTY)
    physical_value = models.DecimalField(**MONEY)
    damage_qty = models.DecimalField(**QTY)
    damage_value = models.DecimalField(**MONEY)
    wbc_qty = models.DecimalField(**QTY)
    wbc_value = models.DecimalField(**MONEY)
    total_physical_qty = models.DecimalField(**QTY)
    total_physical_value = models.DecimalField(**MONEY)
    diff_qty = models.DecimalField(**QTY)
    diff_value = models.DecimalField(**MONEY)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["audit", "category"], name="uniq_line_per_category")]

    def numbers(self) -> calc.Numbers:
        return calc.Numbers.from_mapping(self, category=self.category.name)

    def save(self, *args, **kwargs):
        n = calc.Numbers.from_mapping(self)
        for f in calc.FIELDS:
            if getattr(self, f) is None or getattr(self, f) < 0:
                raise ValueError(f"{f} must be zero or more")
        self.total_physical_qty = n.total_physical_qty
        self.total_physical_value = n.total_physical_value
        self.diff_qty = n.diff_qty
        self.diff_value = n.diff_value
        super().save(*args, **kwargs)


class ObservationKind(models.TextChoices):
    SHORTAGE = "shortage", "Shortage"
    DAMAGE = "damage", "Damage"
    WBC = "wbc", "WBC"
    PROCESS_GAP = "process_gap", "Process gap"
    GOOD_PRACTICE = "good_practice", "Good practice"


class Severity(models.TextChoices):
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"


class Observation(TimeStamped):
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="observations")
    category = models.ForeignKey("clients.Category", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=ObservationKind.choices, default=ObservationKind.SHORTAGE)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    text = models.TextField(blank=True)
    recommendation = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    @property
    def category_label(self):
        return self.category.name if self.category_id else "Whole store"

    @property
    def is_complete(self):
        return bool(self.text.strip() and self.recommendation.strip())

    def as_obs(self) -> calc.Obs:
        return calc.Obs(self.kind, self.category.name if self.category_id else None, self.severity,
                        self.text, self.recommendation, self.pk)


class FollowUpStatus(models.TextChoices):
    DONE = "done", "Done"
    IN_PROGRESS = "in_progress", "In progress"
    NOT_DONE = "not_done", "Not done"


class FollowUp(TimeStamped):
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="followups")
    previous_observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="followups")
    status = models.CharField(max_length=20, choices=FollowUpStatus.choices, default=FollowUpStatus.IN_PROGRESS)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["audit", "previous_observation"], name="uniq_followup")]

    @property
    def tone(self):
        return {"done": "good", "in_progress": "warn", "not_done": "bad"}[self.status]


class FileKind(models.TextChoices):
    AUDIT_EXCEL = "audit_excel", "Audit Excel"
    VARIANCE_REPORT = "variance_report", "Variance report"
    SCANNED_DATA = "scanned_data", "Scanned data"
    SIGNOFF = "signoff", "Signoff copy"
    PHOTO = "photo", "Evidence photograph"


class AuditFileQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_deleted=False)


class AuditFile(UUIDModel):
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="files")
    kind = models.CharField(max_length=20, choices=FileKind.choices)
    storage_key = models.CharField(max_length=500)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size_bytes = models.BigIntegerField(default=0)
    caption = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    thumbnail_key = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)
    is_deleted = models.BooleanField(default=False)

    objects = AuditFileQuerySet.as_manager()

    class Meta:
        ordering = ["kind", "sort_order", "uploaded_at"]
        indexes = [models.Index(fields=["audit", "kind"], name="auditfile_audit_kind")]

    def __str__(self):
        return self.original_name

    @property
    def previewable(self):
        return self.kind in (FileKind.SIGNOFF, FileKind.PHOTO)

    @property
    def is_pdf(self):
        return self.content_type == "application/pdf"


class ObservationTemplate(TimeStamped):
    """Editable defaults for 'Suggest drafts from the numbers'."""

    kind = models.CharField(max_length=20, choices=ObservationKind.choices)
    slot = models.PositiveSmallIntegerField(default=1)
    text = models.TextField(help_text="Shortage: the cause sentence. Damage/WBC: may use {pct}, {category}, {units}.")
    recommendation = models.TextField()

    class Meta:
        ordering = ["kind", "slot"]
        constraints = [models.UniqueConstraint(fields=["kind", "slot"], name="uniq_template_slot")]
