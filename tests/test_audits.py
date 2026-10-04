"""Stage 3: audit entry and publishing (acceptance 1, 2, 18, 20)."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from activity.models import ActivityLog
from audits import services
from audits.models import Audit, Observation
from core.models import AppSettings
from tests import factories as f

pytestmark = pytest.mark.django_db

EXPECTED = ("Shortage of 50 units (₹5,000), 5.0% of stock value. "
            "Total physical 950 = Physical 900 + Damage 20 + WBC (Without Barcode) 30. "
            "Largest shortage in Grocery (₹5,000). High variance, review required.")


@pytest.fixture
def grocery_client(db):
    return f.make_client("Grocer Co", stores=2, categories=["Grocery", "Beverages"])


def form_data(client_obj, action="draft", sale="", observation=True, audit_date=None, store=None, audit_type="physical"):
    store = store or client_obj.stores.first()
    g = client_obj.categories.get(name="Grocery")
    b = client_obj.categories.get(name="Beverages")
    data = {
        "client": str(client_obj.pk), "store": str(store.pk),
        "audit_date": (audit_date or date.today() - timedelta(days=1)).isoformat(),
        "audit_type": audit_type, "shift": "morning", "sale_value": sale, "note": "",
        f"inc-{g.pk}": "1", f"l-{g.pk}-stock_qty": "1000", f"l-{g.pk}-stock_value": "1,00,000",
        f"l-{g.pk}-physical_qty": "900", f"l-{g.pk}-physical_value": "90000", f"l-{g.pk}-damage_qty": "20",
        f"l-{g.pk}-damage_value": "2000", f"l-{g.pk}-wbc_qty": "30", f"l-{g.pk}-wbc_value": "3000",
        f"inc-{b.pk}": "0", f"l-{b.pk}-stock_qty": "5",
        "action": action, "next_index": "1",
    }
    if observation:
        data.update({"obs-0-category": str(g.pk), "obs-0-kind": "shortage", "obs-0-severity": "high",
                     "obs-0-text": "Shortage on fast movers", "obs-0-recommendation": "Count daily"})
    return data


def test_example_audit_through_the_form(admin_client, grocery_client):
    r = admin_client.post(reverse("audits:new") + f"?client={grocery_client.pk}", form_data(grocery_client))
    assert r.status_code == 302, r.content.decode()[:3000]
    audit = Audit.objects.get()
    assert audit.is_draft and audit.reference.startswith("AUD-")
    assert audit.remark == EXPECTED
    assert audit.status_label == "Review"
    assert audit.lines.count() == 1  # removed row is not saved
    assert str(audit.var_pct_stock) == "-5.0000"
    assert audit.var_pct_sale is None


def test_sale_value_through_the_form(admin_client, grocery_client):
    admin_client.post(reverse("audits:new"), form_data(grocery_client, sale="2000000"))
    audit = Audit.objects.get()
    assert "5.0% of stock value and 0.25% of sale value." in audit.remark
    assert str(audit.var_pct_sale) == "-0.2500"


def test_live_preview_matches_saved_audit(admin_client, grocery_client):
    data = form_data(grocery_client, sale="2000000")
    r = admin_client.post(reverse("audits:preview"), data)
    assert r.status_code == 200
    preview = r.context["preview"]
    admin_client.post(reverse("audits:new"), data)
    audit = Audit.objects.get()
    assert preview["remark"] == audit.remark
    assert preview["status"] == audit.status_label
    assert preview["totals"].diff_value == audit.diff_value
    assert preview["totals"].total_physical_qty == audit.total_qty
    html = r.content.decode()
    assert 'id="o-tot-dv"' in html and "hx-swap-oob" in html


def test_publish_without_observation_fails(admin_client, grocery_client):
    r = admin_client.post(reverse("audits:new"), form_data(grocery_client, action="publish", observation=False))
    audit = Audit.objects.get()
    assert audit.is_draft
    assert r.status_code == 302
    r = admin_client.get(r["Location"])
    assert "observation" in r.content.decode()


def test_observation_needs_recommendation_to_publish(grocery_client, admin):
    audit = f.make_audit(grocery_client, publish=False, observation=False)
    Observation.objects.create(audit=audit, kind="shortage", text="Only text", recommendation="")
    with pytest.raises(services.PublishError):
        services.publish(audit, admin)


def test_imported_audit_publishes_without_observation(grocery_client, admin):
    audit = f.make_audit(grocery_client, publish=False, observation=False, is_imported=True)
    services.publish(audit, admin)
    assert Audit.objects.get(pk=audit.pk).is_published


def test_publish_through_form_sends_email(admin_client, grocery_client, mailoutbox):
    r = admin_client.post(reverse("audits:new"), form_data(grocery_client, action="publish"))
    audit = Audit.objects.get()
    assert audit.is_published and r["Location"] == reverse("audits:detail", args=[audit.pk])
    assert len(mailoutbox) == 1
    msg = mailoutbox[0]
    assert msg.to == grocery_client.email_list()
    assert audit.reference in msg.subject
    assert not msg.attachments
    assert ActivityLog.objects.filter(action="Published audit", audit=audit).exists()


def test_auditor_cannot_publish_by_default(auditor_client, grocery_client):
    auditor_client.post(reverse("audits:new"), form_data(grocery_client, action="publish"))
    assert Audit.objects.get().is_draft


def test_auditor_can_publish_when_allowed(auditor_client, grocery_client):
    s = AppSettings.load()
    s.auditor_can_publish = True
    s.save()
    auditor_client.post(reverse("audits:new"), form_data(grocery_client, action="publish"))
    assert Audit.objects.get().is_published


def test_auditor_cannot_edit_published(auditor_client, grocery_client):
    audit = f.make_audit(grocery_client)
    assert auditor_client.get(reverse("audits:edit", args=[audit.pk])).status_code == 403


def test_future_date_and_negative_numbers_rejected(admin_client, grocery_client):
    data = form_data(grocery_client, audit_date=date.today() + timedelta(days=2))
    g = grocery_client.categories.get(name="Grocery")
    data[f"l-{g.pk}-damage_qty"] = "-3"
    r = admin_client.post(reverse("audits:new"), data)
    assert r.status_code == 200
    assert not Audit.objects.exists()
    html = r.content.decode()
    assert "future" in html and "must be zero or more" in html


def test_edit_published_is_logged(admin_client, grocery_client):
    audit = f.make_audit(grocery_client)
    data = form_data(grocery_client, audit_date=audit.audit_date)
    data["sale_value"] = "500000"
    r = admin_client.post(reverse("audits:edit", args=[audit.pk]), data)
    assert r.status_code == 302
    audit.refresh_from_db()
    assert audit.edited_at is not None and audit.is_published
    entry = ActivityLog.objects.get(action="Edited published audit")
    assert "sale value" in entry.detail


def test_aging_and_previous_links(grocery_client, admin):
    store = grocery_client.stores.first()
    a1 = f.make_audit(grocery_client, store=store, audit_date=date(2026, 4, 5))
    cyc = f.make_audit(grocery_client, store=store, audit_date=date(2026, 5, 1), audit_type="cycle")
    a2 = f.make_audit(grocery_client, store=store, audit_date=date(2026, 7, 6))
    a3 = f.make_audit(grocery_client, store=store, audit_date=date(2026, 10, 4))
    for a in (a1, a2, a3, cyc):
        a.refresh_from_db()
    assert (a1.aging_days, a1.delay_days) == (None, None)
    assert (a2.aging_days, a2.delay_days, a2.previous_audit_id) == (92, 2, a1.pk)
    assert (a3.aging_days, a3.delay_days) == (90, 0)
    assert cyc.previous_audit_id is None and cyc.aging_days is None
    services.soft_delete(a2)
    a3.refresh_from_db()
    assert a3.previous_audit_id == a1.pk and a3.aging_days == 182


def test_followups_saved_and_preset(admin_client, grocery_client):
    first = f.make_audit(grocery_client, audit_date=date(2026, 7, 1),
                         lines=[dict(stock_qty=1000, stock_value=100000, physical_qty=950, physical_value=95000)])
    obs = first.observations.get()
    data = form_data(grocery_client, audit_date=date(2026, 9, 1))
    r = admin_client.post(reverse("audits:preview"), data)
    fu = r.context["preview"]["followups"]
    assert len(fu) == 1 and fu[0]["obs"].pk == obs.pk
    data[f"fu-{obs.pk}-status"] = "done"
    data[f"fu-{obs.pk}-note"] = "Daily counts started"
    admin_client.post(reverse("audits:new"), data)
    audit = Audit.objects.exclude(pk=first.pk).get()
    assert audit.followups.get().status == "done"


def test_suggest_drafts_endpoint(admin_client, grocery_client):
    r = admin_client.post(reverse("audits:suggest"), form_data(grocery_client, observation=False))
    html = r.content.decode()
    assert r.status_code == 200 and "Shortage of 50 units" in html and 'name="obs-1-text"' in html


def test_threshold_change_recomputes_status(admin_client, grocery_client):
    audit = f.make_audit(grocery_client)  # 2% shortage -> Watch
    assert audit.status_label == "Watch"
    admin_client.post(reverse("console:client_edit", args=[grocery_client.pk]), {
        "name": grocery_client.name, "contact_emails": "", "good_pct": "0.5", "warn_pct": "1.5",
        "cycle_days": 90, "soon_days": 15})
    audit.refresh_from_db()
    assert audit.status_label == "Review"
    assert "High variance, review required." in audit.remark


def test_soft_delete_admin_only(admin_client, auditor, grocery_client):
    from django.test import Client as HttpClient

    audit = f.make_audit(grocery_client)
    auditor_client = HttpClient()
    auditor_client.force_login(auditor)
    assert auditor_client.post(reverse("audits:delete", args=[audit.pk])).status_code == 403
    admin_client.post(reverse("audits:delete", args=[audit.pk]))
    audit.refresh_from_db()
    assert audit.status == "deleted"


def test_entry_pages_render(admin_client, grocery_client):
    assert admin_client.get(reverse("audits:new")).status_code == 200
    r = admin_client.get(reverse("audits:new") + f"?client={grocery_client.pk}")
    assert r.status_code == 200 and "Category-wise summary" in r.content.decode()
    audit = f.make_audit(grocery_client)
    assert admin_client.get(reverse("audits:edit", args=[audit.pk])).status_code == 200
    assert admin_client.get(reverse("audits:list")).status_code == 200
