"""Audit workflow: publish, relink previous audits, snapshots, comparison data."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audits.models import Audit, AuditStatus, Observation, Series
from core import calc


class PublishError(Exception):
    pass


PUBLISH_RULE_MESSAGE = ("Add at least one observation with both the observation text and a recommendation "
                        "before publishing.")


def has_complete_observation(audit) -> bool:
    return any(o.is_complete for o in audit.observations.all())


def can_publish(audit) -> tuple[bool, str]:
    if audit.is_imported:
        return True, ""
    if not audit.lines.exists():
        return False, "Enter the category-wise summary before publishing."
    if not has_complete_observation(audit):
        return False, PUBLISH_RULE_MESSAGE
    return True, ""


def find_previous(client_id, store_id, series, audit_date, created_at=None, exclude_id=None):
    qs = Audit.objects.filter(client_id=client_id, store_id=store_id, series=series, status=AuditStatus.PUBLISHED)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    if created_at is not None:
        qs = qs.filter(Q(audit_date__lt=audit_date) | Q(audit_date=audit_date, created_at__lt=created_at))
    else:
        qs = qs.filter(audit_date__lt=audit_date)
    return qs.select_related("store").order_by("-audit_date", "-created_at").first()


def audit_previous(audit):
    """The previous audit used everywhere for comparisons: the stored link for a published audit,
    the audit it will link to once published for a draft. None means a first audit."""
    if audit.is_published:
        return audit.previous_audit
    return previous_for(audit)


def previous_for(audit):
    return find_previous(audit.client_id, audit.store_id, audit.series, audit.audit_date,
                         audit.created_at, audit.pk)


def relink_series(client, store_id, series):
    """Recompute previous_audit, aging and delay along one store's series."""
    audits = list(Audit.objects.filter(client_id=client.pk, store_id=store_id, series=series,
                                       status=AuditStatus.PUBLISHED).order_by("audit_date", "created_at"))
    prev = None
    for a in audits:
        if series == Series.FULL:
            aging = calc.aging_days(a.audit_date, prev.audit_date if prev else None)
            delay = calc.delay_days(aging, client.cycle_days)
        else:
            aging = delay = None
        prev_id = prev.pk if prev else None
        if (a.previous_audit_id, a.aging_days, a.delay_days) != (prev_id, aging, delay):
            Audit.objects.filter(pk=a.pk).update(previous_audit_id=prev_id, aging_days=aging, delay_days=delay)
        prev = a
    # Drafts and deleted audits are not part of the chain.
    Audit.objects.filter(client_id=client.pk, store_id=store_id, series=series).exclude(
        status=AuditStatus.PUBLISHED).update(previous_audit=None, aging_days=None, delay_days=None)


@transaction.atomic
def publish(audit, user=None):
    ok, msg = can_publish(audit)
    if not ok:
        raise PublishError(msg)
    first_time = audit.status != AuditStatus.PUBLISHED
    audit.status = AuditStatus.PUBLISHED
    if first_time:
        audit.published_by = user
        audit.published_at = timezone.now()
    audit.save()
    audit.refresh_snapshot()
    relink_series(audit.client, audit.store_id, audit.series)
    audit.refresh_from_db()
    return first_time


@transaction.atomic
def soft_delete(audit):
    audit.status = AuditStatus.DELETED
    audit.save(update_fields=["status", "updated_at"])
    relink_series(audit.client, audit.store_id, audit.series)


def recompute_snapshots(client=None):
    """Refresh status and remark after a threshold or cycle change."""
    from clients.models import Client

    clients = [client] if client is not None else list(Client.objects.all())
    for c in clients:
        th = c.thresholds()
        qs = c.audits.live().prefetch_related("lines__category")
        for audit in qs.iterator(chunk_size=500):
            audit.refresh_snapshot(th)
        pairs = c.audits.published().values_list("store_id", "series").distinct()
        for store_id, series in pairs:
            relink_series(c, store_id, series)


# ---------------------------------------------------------------- comparison data


def audit_data(audit, with_observations=True) -> calc.AuditData:
    nums = audit.numbers_by_category()
    lines = {n.category: n for n in nums}
    obs = []
    if with_observations:
        obs = [o.as_obs() for o in audit.observations.select_related("category")]
    return calc.AuditData(
        id=audit.pk, audit_date=audit.audit_date, store_id=audit.store_id, store_name=audit.store.name,
        city=audit.store.city, totals=calc.audit_totals(nums), lines=lines, sale_value=audit.sale_value,
        observations=obs, reference=audit.reference,
    )


def comparison_for(a: Audit, b: Audit):
    return calc.compare(audit_data(a), audit_data(b), b.client.thresholds())


def observation_templates():
    """DB-editable templates for suggested drafts, falling back to the built-in defaults."""
    from audits.models import ObservationTemplate

    out = {}
    rows = list(ObservationTemplate.objects.all())
    shortage = [(r.text, r.recommendation) for r in rows if r.kind == "shortage"]
    if shortage:
        out["shortage"] = shortage
    for kind in ("damage", "wbc", "good_practice"):
        r = next((r for r in rows if r.kind == kind), None)
        if r:
            out[kind] = (r.text, r.recommendation)
    return out


def previous_observations_with_recs(prev):
    if prev is None:
        return []
    return list(Observation.objects.filter(audit=prev).exclude(recommendation="").select_related("category"))
