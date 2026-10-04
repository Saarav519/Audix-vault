"""Admin overview: client-wise summary by period."""

from datetime import date

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from audits.models import Audit
from core import calc
from reports import queries
from tests import factories as f

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.localdate", lambda *a, **k: TODAY)


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def test_overview_windows():
    w = {k: calc.overview_window(k, TODAY) for k, _ in calc.OVERVIEW_PERIODS}
    assert (w["today"].start, w["today"].end) == (TODAY, TODAY)
    assert w["week"].start == date(2026, 9, 28)
    assert w["month"].start == date(2026, 10, 1)
    assert w["quarter"].start == date(2026, 10, 1) and "Q3" in w["quarter"].label
    assert w["fy"].start == date(2026, 4, 1) and "FY 2026-27" in w["fy"].label
    assert calc.overview_window("nonsense", TODAY).key == "month"
    assert calc.overview_window("quarter", date(2026, 8, 15)).start == date(2026, 7, 1)


@pytest.mark.parametrize("who", ["admin", "auditor"])
def test_client_rows_match_client_totals(who, request, client_a, client_b):
    s1, s2 = client_a.stores.order_by("code")
    a1 = f.make_audit(client_a, store=s1, audit_date=date(2026, 10, 2))
    f.make_audit(client_a, store=s2, audit_date=date(2026, 9, 10))
    f.make_audit(client_a, store=s2, audit_date=date(2026, 10, 3), publish=False)  # drafts never count
    f.make_audit(client_b, audit_date=date(2026, 5, 2))
    h = http_for(request.getfixturevalue(who))

    r = h.get(reverse("console:overview") + "?op=month")
    assert r.status_code == 200
    rows = {row["client"].name: row for row in r.context["summary"]["rows"]}
    ra = rows[client_a.name]
    assert ra["t"]["audits"] == 1 and ra["stores_audited"] == 1 and ra["stores_total"] == 2
    expected = queries.window_totals(Audit.objects.filter(pk=a1.pk))
    assert ra["t"]["diff_value"] == expected["diff_value"] == a1.diff_value
    assert ra["status"] == calc.status_for(a1.var_pct_stock, client_a.thresholds())
    assert rows[client_b.name]["t"]["audits"] == 0
    html = r.content.decode()
    assert "Client-wise summary" in html and "No audit in this period" in html
    assert f"?client={client_a.pk}" in html

    r = h.get(reverse("console:overview") + "?op=fy")
    rows = {row["client"].name: row for row in r.context["summary"]["rows"]}
    assert rows[client_a.name]["t"]["audits"] == 2 and rows[client_b.name]["t"]["audits"] == 1
    assert r.context["summary"]["total"]["audits"] == 3

    r = h.get(reverse("console:overview") + "?op=today")
    assert r.context["summary"]["total"]["audits"] == 0


def test_client_users_cannot_open_overview(user_a):
    assert http_for(user_a).get(reverse("console:overview")).status_code in (302, 403, 404)
