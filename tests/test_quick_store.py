"""Add a store from inside the Add audit page."""

from datetime import date, timedelta

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from activity.models import ActivityLog
from audits.models import Audit
from clients.forms import next_store_code
from clients.models import Store

pytestmark = pytest.mark.django_db


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def url(client_obj):
    return reverse("console:store_quick_add", args=[client_obj.pk])


@pytest.mark.parametrize("who", ["admin", "auditor"])
def test_staff_can_quick_add(request, client_a, who):
    user = request.getfixturevalue(who)
    r = http_for(user).post(url(client_a), {"name": "Baner", "city": "Pune", "code": "pn7"})
    assert r.status_code == 201, r.content
    body = r.json()
    store = Store.objects.get(pk=body["id"])
    assert store.client == client_a and store.is_active and store.code == "PN7"
    assert body == {"id": str(store.pk), "label": "PN7 · Baner, Pune", "code": "PN7"}


def test_blank_code_auto_generates_unique_code(admin, client_a):
    Store.objects.create(client=client_a, code="S007", name="Old", city="X")
    Store.objects.create(client=client_a, code="S008", name="Older", city="Y")  # taken numbers are skipped
    h = http_for(admin)
    first = h.post(url(client_a), {"name": "Aundh", "city": "Pune", "code": ""}).json()
    second = h.post(url(client_a), {"name": "Wakad"}).json()
    assert first["code"] == "S009" and second["code"] == "S010"
    assert second["label"] == "S010 · Wakad"


def test_next_code_numbering(client_b):
    from clients.models import Client

    empty = Client.objects.create(name="No Stores Yet", slug="no-stores-yet")
    assert next_store_code(empty) == "S001"
    Store.objects.create(client=empty, code="GR01", name="Other scheme")
    assert next_store_code(empty) == "S001"  # codes in other formats do not count
    assert next_store_code(client_b) == "S003"  # factory stores S01 and S02 count as numbers 1 and 2


def test_duplicate_code_rejected_case_insensitive(admin, client_a):
    r = http_for(admin).post(url(client_a), {"name": "Anything", "code": "s01"})
    assert r.status_code == 400
    assert "code" in r.json()["errors"]


def test_duplicate_name_and_city_rejected(admin, client_a):
    r = http_for(admin).post(url(client_a), {"name": "store 1", "city": "CITY 1"})
    assert r.status_code == 400
    msg = r.json()["errors"]["__all__"][0]
    assert "S01 · Store 1, City 1" in msg and "Pick it from the store list" in msg
    # same name in another city is fine
    assert http_for(admin).post(url(client_a), {"name": "Store 1", "city": "Elsewhere"}).status_code == 201


def test_name_required(admin, client_a):
    r = http_for(admin).post(url(client_a), {"name": "  ", "city": "Pune"})
    assert r.status_code == 400 and "name" in r.json()["errors"]


def test_client_user_denied(user_a, client_a):
    r = http_for(user_a).post(url(client_a), {"name": "Sneaky", "city": "Pune"})
    assert r.status_code in (403, 404)
    assert not Store.objects.filter(name="Sneaky").exists()


def test_only_the_client_in_the_url(admin, client_a, client_b):
    # a crafted request cannot redirect the store to another client
    r = http_for(admin).post(url(client_a), {"name": "Target", "client": str(client_b.pk), "client_id": str(client_b.pk)})
    assert r.status_code == 201
    assert Store.objects.get(name="Target").client == client_a
    assert not client_b.stores.filter(name="Target").exists()
    bogus = reverse("console:store_quick_add", args=["00000000-0000-0000-0000-000000000000"])
    assert http_for(admin).post(bogus, {"name": "X"}).status_code == 404


def test_get_not_allowed(admin, client_a):
    assert http_for(admin).get(url(client_a)).status_code == 405


def test_activity_log_row(admin, client_a):
    http_for(admin).post(url(client_a), {"name": "Logged", "city": "Pune"})
    row = ActivityLog.objects.get(action="Added store from Add audit")
    assert row.client == client_a and row.action_type == "admin_change" and "Logged" in row.detail


def test_add_audit_page_has_new_store_option(admin, client_a):
    h = http_for(admin)
    html = h.get(reverse("audits:new") + f"?client={client_a.pk}").content.decode()
    assert '<option value="__new__">+ Add new store...</option>' in html
    assert url(client_a) in html and 'id="quick-store"' in html
    # also on the edit page
    from tests import factories as f

    audit = f.make_audit(client_a, publish=False)
    assert "+ Add new store..." in h.get(reverse("audits:edit", args=[audit.pk])).content.decode()


def test_audit_saved_against_quick_added_store(auditor, client_a):
    h = http_for(auditor)
    store_id = h.post(url(client_a), {"name": "Fresh", "city": "Nashik"}).json()["id"]
    cat = client_a.categories.first()
    data = {
        "client": str(client_a.pk), "store": store_id, "audit_date": (date.today() - timedelta(days=1)).isoformat(),
        "audit_type": "physical", "shift": "morning", "action": "draft", "next_index": "0",
        f"inc-{cat.pk}": "1", f"l-{cat.pk}-stock_qty": "10", f"l-{cat.pk}-stock_value": "1000",
        f"l-{cat.pk}-physical_qty": "10", f"l-{cat.pk}-physical_value": "1000",
    }
    for other in client_a.categories.exclude(pk=cat.pk):
        data[f"inc-{other.pk}"] = "0"
    r = h.post(reverse("audits:new"), data)
    assert r.status_code == 302, r.content.decode()[:2000]
    assert Audit.objects.get().store_id is not None and str(Audit.objects.get().store_id) == store_id
    # the live preview also accepts it
    r = h.post(reverse("audits:preview"), data)
    assert r.status_code == 200 and r.context["preview"]["has_lines"]


def test_new_store_sentinel_is_not_a_valid_store(admin, client_a):
    from audits.entry import NEW_STORE_VALUE, HeaderForm

    form = HeaderForm({"store": NEW_STORE_VALUE, "audit_date": date.today().isoformat(), "audit_type": "physical",
                       "shift": "morning"}, client=client_a)
    assert not form.is_valid() and "store" in form.errors
