"""A first audit (no previous audit) shows no comparison on screen."""

from datetime import date, timedelta

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from tests import factories as f

pytestmark = pytest.mark.django_db
HIDDEN = ("Compared with last audit", "Open full comparison", "Last audit stock", "Last audit difference",
          "Change in value", "Follow-up on last audit")


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


@pytest.mark.parametrize("who", ["admin", "user_a"])
def test_detail_page_first_audit(request, client_a, who):
    audit = f.make_audit(client_a, audit_date=date(2026, 4, 5))
    assert audit.previous_audit_id is None
    html = http_for(request.getfixturevalue(who)).get(reverse("audits:detail", args=[audit.pk])).content.decode()
    for text in HIDDEN:
        assert text not in html, text
    assert "First audit for this store." in html
    assert "Category-wise summary" in html and "Observations and recommendations" in html


def test_detail_page_with_previous_still_compares(client_a, user_a):
    f.make_audit(client_a, audit_date=date(2026, 4, 5))
    second = f.make_audit(client_a, audit_date=date(2026, 7, 6))
    html = http_for(user_a).get(reverse("audits:detail", args=[second.pk])).content.decode()
    for text in ("Compared with last audit", "Open full comparison", "Last audit stock", "Follow-up on last audit"):
        assert text in html
    assert "First audit for this store." not in html


def preview_data(client_obj, store):
    cat = client_obj.categories.first()
    data = {"client": str(client_obj.pk), "store": str(store.pk),
            "audit_date": (date.today() - timedelta(days=1)).isoformat(), "audit_type": "physical",
            "shift": "morning", f"inc-{cat.pk}": "1", f"l-{cat.pk}-stock_qty": "100",
            f"l-{cat.pk}-stock_value": "10000", f"l-{cat.pk}-physical_qty": "98", f"l-{cat.pk}-physical_value": "9800"}
    for other in client_obj.categories.exclude(pk=cat.pk):
        data[f"inc-{other.pk}"] = "0"
    return data


def test_preview_first_audit(admin_client, client_a):
    store = client_a.stores.get(code="S02")  # no audits yet
    r = admin_client.post(reverse("audits:preview"), preview_data(client_a, store))
    html = r.content.decode()
    assert r.context["preview"]["previous"] is None
    assert "First audit for this store, so there is no earlier audit to compare." in html
    assert "Compared with last audit" not in html and "Previous audit" not in html
    assert "Follow-up on last audit marked" not in html
    assert 'id="step-followup"' not in html  # step 5 is not swapped in


def test_entry_page_first_audit_hides_step5(admin_client, client_a):
    html = admin_client.get(reverse("audits:new") + f"?client={client_a.pk}").content.decode()
    step5 = html.split('id="step-followup"', 1)[1].split(">", 1)[0]
    assert "hidden" in step5


def test_preview_with_previous_compares(admin_client, client_a):
    store = client_a.stores.get(code="S01")
    f.make_audit(client_a, store=store, audit_date=date(2026, 4, 5))
    r = admin_client.post(reverse("audits:preview"), preview_data(client_a, store))
    html = r.content.decode()
    assert "Compared with last audit" in html
    assert "First audit for this store" not in html
