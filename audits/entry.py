"""Add / edit audit: parse the six-step form, build the live preview and save."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django import forms
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audits import services
from audits.models import (
    Audit,
    AuditFile,
    AuditLine,
    AuditStatus,
    AuditType,
    FileKind,
    FollowUp,
    FollowUpStatus,
    Observation,
    ObservationKind,
    Severity,
    Shift,
)
from clients.models import Category, Store
from core import calc
from core.formatting import fmt_date, inr, num

MAX_VALUE = Decimal("999999999999")
OBS_RE = re.compile(r"^obs-(\d+)-text$")


class HeaderForm(forms.Form):
    store = forms.ModelChoiceField(queryset=Store.objects.none(), empty_label="Choose a store")
    audit_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    audit_type = forms.ChoiceField(choices=AuditType.choices)
    shift = forms.ChoiceField(choices=Shift.choices)
    sale_value = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=2,
                                    widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=2000)

    def __init__(self, *args, client=None, audit=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Store.objects.filter(client=client)
        if audit is not None:
            qs = qs.filter(Q(is_active=True) | Q(pk=audit.store_id))
        else:
            qs = qs.filter(is_active=True)
        self.fields["store"].queryset = qs.order_by("code")
        self.fields["store"].label_from_instance = lambda s: f"{s.code} · {s.name}, {s.city}" if s.city else f"{s.code} · {s.name}"
        self.fields["audit_date"].widget.attrs["max"] = timezone.localdate().isoformat()

    def clean_sale_value(self):
        v = self.cleaned_data.get("sale_value")
        if v is not None and v > MAX_VALUE:
            raise forms.ValidationError("This value is too large.")
        return v if v else None

    def clean_audit_date(self):
        d = self.cleaned_data["audit_date"]
        if d > timezone.localdate():
            raise forms.ValidationError("The audit date cannot be in the future.")
        return d


def parse_decimal(raw, places):
    raw = (raw or "").strip().replace(",", "").replace("₹", "").replace(" ", "")
    if raw == "":
        return Decimal(0), None
    try:
        v = Decimal(raw)
    except InvalidOperation:
        return Decimal(0), "is not a number"
    if not v.is_finite():
        return Decimal(0), "is not a number"
    if v < 0:
        return Decimal(0), "must be zero or more"
    if v > MAX_VALUE:
        return Decimal(0), "is too large"
    return v.quantize(Decimal(1).scaleb(-places)), None


FIELD_LABELS = {
    "stock_qty": "Stock qty", "stock_value": "Stock value", "physical_qty": "Physical qty",
    "physical_value": "Physical value", "damage_qty": "Damage qty", "damage_value": "Damage value",
    "wbc_qty": "WBC qty", "wbc_value": "WBC value",
}


@dataclass
class EntryState:
    """Everything the six-step form shows, built from the database or from posted data."""

    categories: list
    values: dict = field(default_factory=dict)  # cat_id(str) -> {field: raw string}
    included: set = field(default_factory=set)  # cat_id(str)
    observations: list = field(default_factory=list)  # dicts
    followups: dict = field(default_factory=dict)  # obs_id(str) -> {"status", "note"}
    errors: list = field(default_factory=list)

    def lines(self):
        """[(Category, {field: Decimal})] for included rows that have any number."""
        out = []
        for cat in self.categories:
            cid = str(cat.pk)
            if cid not in self.included:
                continue
            vals = {}
            for f in calc.FIELDS:
                v, _ = parse_decimal(self.values.get(cid, {}).get(f), 3 if f.endswith("qty") else 2)
                vals[f] = v
            if any(vals.values()):
                out.append((cat, vals))
        return out

    def numbers(self):
        return [calc.Numbers(**vals, category=cat.name) for cat, vals in self.lines()]

    def rows(self):
        """For the template: one entry per category."""
        nums = {n.category: n for n in self.numbers()}
        out = []
        for cat in self.categories:
            cid = str(cat.pk)
            out.append({"category": cat, "id": cid, "included": cid in self.included,
                        "values": self.values.get(cid, {}), "numbers": nums.get(cat.name)})
        return out


def state_from_post(data, categories) -> EntryState:
    st = EntryState(categories=categories)
    for cat in categories:
        cid = str(cat.pk)
        if data.get(f"inc-{cid}", "1") == "1":
            st.included.add(cid)
        row = {}
        for f in calc.FIELDS:
            raw = data.get(f"l-{cid}-{f}", "")
            row[f] = raw
            if cid in st.included:
                _, err = parse_decimal(raw, 3 if f.endswith("qty") else 2)
                if err:
                    st.errors.append(f"{cat.name}: {FIELD_LABELS[f]} {err}.")
        st.values[cid] = row
    cats = {str(c.pk): c for c in categories}
    idx = sorted({int(m.group(1)) for k in data.keys() if (m := OBS_RE.match(k))})
    for i in idx:
        if data.get(f"obs-{i}-delete"):
            continue
        text = (data.get(f"obs-{i}-text") or "").strip()[:4000]
        rec = (data.get(f"obs-{i}-recommendation") or "").strip()[:4000]
        if not text and not rec:
            continue
        cat_id = data.get(f"obs-{i}-category") or ""
        kind = data.get(f"obs-{i}-kind") or ObservationKind.SHORTAGE
        sev = data.get(f"obs-{i}-severity") or Severity.MEDIUM
        st.observations.append({
            "category": cats.get(cat_id), "category_id": cat_id if cat_id in cats else "",
            "kind": kind if kind in ObservationKind.values else ObservationKind.SHORTAGE,
            "severity": sev if sev in Severity.values else Severity.MEDIUM,
            "text": text, "recommendation": rec,
        })
    for key in data.keys():
        m = re.match(r"^fu-([0-9]+)-status$", key)
        if m:
            status = data.get(key)
            if status in FollowUpStatus.values:
                st.followups[m.group(1)] = {"status": status,
                                            "note": (data.get(f"fu-{m.group(1)}-note") or "").strip()[:1000]}
    return st


def state_from_audit(audit, categories) -> EntryState:
    st = EntryState(categories=categories)
    for line in audit.lines.all():
        cid = str(line.category_id)
        st.included.add(cid)
        st.values[cid] = {f: _plain(getattr(line, f)) for f in calc.FIELDS}
    for cat in categories:
        st.values.setdefault(str(cat.pk), {})
    for o in audit.observations.select_related("category"):
        st.observations.append({"category": o.category, "category_id": str(o.category_id or ""), "kind": o.kind,
                                "severity": o.severity, "text": o.text, "recommendation": o.recommendation})
    for fu in audit.followups.all():
        st.followups[str(fu.previous_observation_id)] = {"status": fu.status, "note": fu.note}
    return st


def new_state(categories) -> EntryState:
    st = EntryState(categories=categories)
    st.included = {str(c.pk) for c in categories}
    st.values = {str(c.pk): {} for c in categories}
    return st


def _plain(v):
    if v is None:
        return ""
    d = Decimal(v).normalize()
    text = f"{d:f}"
    return "" if text == "0" else text


def categories_for(client, audit=None):
    qs = Category.objects.filter(client=client)
    if audit is not None:
        used = audit.lines.values_list("category_id", flat=True)
        qs = qs.filter(Q(is_active=True) | Q(pk__in=list(used)))
    else:
        qs = qs.filter(is_active=True)
    return list(qs.order_by("sort_order", "name"))


# ---------------------------------------------------------------- preview


def sale_period_hint(previous, audit_date):
    if audit_date is None:
        return "Choose the store and audit date to see the sales period."
    if previous is None:
        return "There is no earlier audit for this store, so there is no sales period yet."
    days = (audit_date - previous.audit_date).days
    return (f"Enter sales from {fmt_date(previous.audit_date)} to {fmt_date(audit_date)} "
            f"({days} {'day' if days == 1 else 'days'}).")


def build_preview(client, header: dict, state: EntryState, audit=None):
    """Same numbers and remark as the saved audit (acceptance 20)."""
    th = client.thresholds()
    nums = state.numbers()
    totals = calc.audit_totals(nums)
    sale = header.get("sale_value")
    store = header.get("store")
    audit_date = header.get("audit_date")
    audit_type = header.get("audit_type") or AuditType.PHYSICAL
    series = calc.series_for(audit_type)

    previous = None
    if store is not None and audit_date is not None:
        previous = services.find_previous(client.pk, store.pk, series, audit_date,
                                          audit.created_at if audit else None, audit.pk if audit else None)
    comparison = None
    current_data = calc.AuditData(
        id=audit.pk if audit else "new", audit_date=audit_date or timezone.localdate(),
        store_id=store.pk if store else None, store_name=store.name if store else "", city=store.city if store else "",
        totals=totals, lines={n.category: n for n in nums}, sale_value=sale,
        observations=[calc.Obs(o["kind"], o["category"].name if o["category"] else None, o["severity"], o["text"],
                               o["recommendation"]) for o in state.observations],
    )
    if previous is not None and nums:
        comparison = calc.compare(services.audit_data(previous), current_data, th)

    warnings = []
    if store is not None and audit_date is not None:
        dup = Audit.objects.live().filter(client=client, store=store, audit_date=audit_date, audit_type=audit_type)
        if audit is not None:
            dup = dup.exclude(pk=audit.pk)
        if dup.exists():
            warnings.append("Another audit exists for this store, date and type. Check this is not a duplicate.")
    for n in nums:
        if n.stock_qty > 0 and n.total_physical_qty == 0:
            warnings.append(f"{n.category}: there is stock but physical, damage and WBC are all zero.")
    if sale and totals.stock_value > 0:
        ratio = Decimal(sale) / totals.stock_value
        if ratio < Decimal("0.2") or ratio > Decimal("20"):
            warnings.append("The sale value looks unusual: it is outside 0.2 to 20 times the stock value. "
                            "Check it is the sales for the period shown.")

    # follow-up section
    prev_obs = services.previous_observations_with_recs(previous)
    preset = {}
    if comparison is not None:
        for item in comparison.followups:
            preset[str(item.observation.id)] = calc.FOLLOWUP_PRESET.get(item.status, "in_progress")
    followups = []
    for o in prev_obs:
        key = str(o.pk)
        chosen = state.followups.get(key, {})
        followups.append({"obs": o, "key": key, "status": chosen.get("status") or preset.get(key, "in_progress"),
                          "note": chosen.get("note", ""), "suggested": preset.get(key)})

    files = {}
    if audit is not None:
        for k in (FileKind.SIGNOFF, FileKind.PHOTO):
            files[k] = AuditFile.objects.active().filter(audit=audit, kind=k).exists()
    checklist = [
        ("Category-wise summary entered", bool(nums)),
        ("Observation with a recommendation", any(o["text"] and o["recommendation"] for o in state.observations)),
        ("Follow-up on last audit marked", all(str(o.pk) in state.followups for o in prev_obs)),
        ("Signoff copy attached", files.get(FileKind.SIGNOFF, False)),
        ("Photographs attached", files.get(FileKind.PHOTO, False)),
    ]
    sp = calc.sale_pct(totals.diff_value, sale)
    return {
        "totals": totals,
        "numbers": nums,
        "status": calc.status_for(totals.var_pct, th),
        "status_tone": calc.STATUS_TONE[calc.status_for(totals.var_pct, th)],
        "remark": calc.auto_remark(totals, nums, sale, th) if nums else "",
        "var_sale": sp,
        "previous": previous,
        "comparison": comparison,
        "warnings": warnings,
        "followups": followups,
        "checklist": checklist,
        "sale_hint": sale_period_hint(previous, audit_date),
        "thresholds": th,
        "has_lines": bool(nums),
    }


# ---------------------------------------------------------------- save


def describe_changes(before: dict, after: dict) -> str:
    changes = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            changes.append(f"{k}: {before.get(k, '—')} → {after.get(k, '—')}")
    return "; ".join(changes)[:4000]


def audit_fingerprint(audit) -> dict:
    out = {
        "store": audit.store.code if audit.store_id else "",
        "date": fmt_date(audit.audit_date) if audit.audit_date else "",
        "type": audit.get_audit_type_display() if audit.audit_type else "",
        "shift": audit.get_shift_display() if audit.shift else "",
        "sale value": inr(audit.sale_value) if audit.sale_value else "Not given",
        "note": (audit.note or "")[:80],
    }
    if audit.pk:
        for line in audit.lines.select_related("category"):
            for f in calc.FIELDS:
                out[f"{line.category.name} {FIELD_LABELS[f].lower()}"] = num(getattr(line, f), 3 if f.endswith("qty") else 2)
        out["observations"] = str(audit.observations.count())
    return out


@transaction.atomic
def save_audit(client, header: dict, state: EntryState, user, audit=None):
    creating = audit is None
    old_chain = None if creating else (audit.store_id, audit.series)
    if creating:
        audit = Audit(client=client, created_by=user, status=AuditStatus.DRAFT)
    audit.store = header["store"]
    audit.audit_date = header["audit_date"]
    audit.audit_type = header["audit_type"]
    audit.shift = header["shift"]
    audit.sale_value = header.get("sale_value")
    audit.note = header.get("note") or ""
    audit.save()

    keep = []
    for cat, vals in state.lines():
        line, _ = AuditLine.objects.update_or_create(audit=audit, category=cat, defaults=vals)
        keep.append(line.pk)
    audit.lines.exclude(pk__in=keep).delete()

    # Keep observation primary keys stable: later audits' follow-ups point at them.
    existing = list(audit.observations.order_by("sort_order", "id"))
    for i, o in enumerate(state.observations):
        ob = existing[i] if i < len(existing) else Observation(audit=audit)
        ob.category = o["category"]
        ob.kind, ob.severity, ob.text, ob.recommendation, ob.sort_order = (
            o["kind"], o["severity"], o["text"], o["recommendation"], i)
        ob.save()
    for ob in existing[len(state.observations):]:
        ob.delete()

    previous = services.previous_for(audit)
    prev_obs = {str(o.pk): o for o in services.previous_observations_with_recs(previous)}
    audit.followups.exclude(previous_observation_id__in=[o.pk for o in prev_obs.values()]).delete()
    for key, o in prev_obs.items():
        chosen = state.followups.get(key)
        if chosen:
            FollowUp.objects.update_or_create(audit=audit, previous_observation=o,
                                              defaults={"status": chosen["status"], "note": chosen["note"]})

    audit.refresh_snapshot()
    if audit.is_published:
        audit.edited_at = timezone.now()
        audit.save(update_fields=["edited_at", "updated_at"])
        services.relink_series(client, audit.store_id, audit.series)
        if old_chain and old_chain != (audit.store_id, audit.series):
            services.relink_series(client, old_chain[0], old_chain[1])
    return audit
