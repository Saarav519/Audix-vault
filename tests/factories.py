"""Small helpers to build clients and audits for tests."""

from datetime import date
from decimal import Decimal

from django.utils.text import slugify

from audits import services
from audits.models import Audit, AuditLine, Observation
from clients.models import DEFAULT_CATEGORIES, Category, Client, Store


def make_client(name, stores=2, categories=None):
    c = Client.objects.create(name=name, slug=slugify(name), contact_emails=f"ho@{slugify(name)}.example")
    for i in range(1, stores + 1):
        Store.objects.create(client=c, code=f"S{i:02d}", name=f"Store {i}", city=f"City {i}")
    for i, n in enumerate(categories or DEFAULT_CATEGORIES[:3], start=1):
        Category.objects.create(client=c, name=n, sort_order=i)
    return c


def make_audit(client, store=None, audit_date=date(2026, 7, 1), audit_type="physical", lines=None,
               sale_value=None, publish=True, observation=True, user=None, **kw):
    store = store or client.stores.first()
    audit = Audit.objects.create(client=client, store=store, audit_date=audit_date, audit_type=audit_type,
                                 sale_value=sale_value, created_by=user, **kw)
    cats = list(client.categories.all())
    lines = lines or [dict(stock_qty=1000, stock_value=100000, physical_qty=980, physical_value=98000)]
    for cat, data in zip(cats, lines, strict=False):
        AuditLine.objects.create(audit=audit, category=cat, **{k: Decimal(str(v)) for k, v in data.items()})
    if observation:
        Observation.objects.create(audit=audit, category=cats[0], kind="shortage", severity="medium",
                                   text="Shortage seen", recommendation="Count daily")
    audit.refresh_snapshot()
    if publish:
        services.publish(audit, user)
    audit.refresh_from_db()
    return audit
