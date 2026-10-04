"""Stage 5: client portal screens (acceptance 21, 22) and list isolation."""

from datetime import date, timedelta

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from tests import factories as f

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 4)

LINES = [dict(stock_qty=1000, stock_value=100000, physical_qty=970, physical_value=97000, damage_qty=5,
              damage_value=500, wbc_qty=2, wbc_value=300),
         dict(stock_qty=500, stock_value=60000, physical_qty=495, physical_value=59400)]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("reports.views.today", lambda: TODAY)


@pytest.fixture
def data(client_a, client_b, user_a, user_b):
    s1, s2 = client_a.stores.all()
    a1 = f.make_audit(client_a, store=s1, audit_date=date(2026, 4, 10), lines=LINES)
    a2 = f.make_audit(client_a, store=s1, audit_date=date(2026, 7, 20), lines=LINES)
    a3 = f.make_audit(client_a, store=s2, audit_date=date(2026, 9, 25), lines=LINES, audit_type="cycle")
    draft = f.make_audit(client_a, store=s2, audit_date=date(2026, 9, 30), lines=LINES, publish=False,
                         reference="AUD-2026-9999")
    b1 = f.make_audit(client_b, audit_date=date(2026, 9, 28), lines=LINES, reference="AUD-2026-8888")
    return {"a1": a1, "a2": a2, "a3": a3, "draft": draft, "b1": b1}


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


PAGES = ["portal:dashboard", "portal:audits", "portal:tracker", "portal:compare", "portal:aging"]


@pytest.mark.parametrize("name", PAGES)
def test_portal_pages_render_for_client(data, user_a, name):
    r = http_for(user_a).get(reverse(name))
    assert r.status_code == 200
    html = r.content.decode()
    assert "Alpha Retail" in html
    assert "Beta Stores" not in html
    assert "AUD-2026-8888" not in html  # other client's audit
    assert "AUD-2026-9999" not in html  # draft


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "yearly"])
def test_dashboard_periods(data, user_a, period):
    r = http_for(user_a).get(reverse("portal:dashboard") + f"?period={period}")
    assert r.status_code == 200
    assert "<svg" in r.content.decode()


def test_search_isolated(data, user_a):
    h = http_for(user_a)
    r = h.get(reverse("portal:audits") + "?q=AUD-2026-8888")
    assert "No audits match" in r.content.decode()
    r = h.get(reverse("portal:audits") + "?q=" + data["a2"].reference)
    assert data["a2"].reference in r.content.decode()
    r = h.get(reverse("portal:audits") + "?q=20 Jul 2026")
    assert data["a2"].reference in r.content.decode() and data["a1"].reference not in r.content.decode()
    # filters
    r = h.get(reverse("portal:audits") + "?type=cycle")
    assert r.context["summary"]["audits"] == 1


def test_compare_other_client_404(data, user_a):
    h = http_for(user_a)
    r = h.get(reverse("portal:compare") + f"?a={data['a1'].pk}&b={data['b1'].pk}")
    assert r.status_code == 404
    r = h.get(reverse("portal:compare") + f"?a={data['a1'].pk}&b={data['draft'].pk}")
    assert r.status_code == 404


def test_compare_default_previous_vs_latest(data, user_a):
    r = http_for(user_a).get(reverse("portal:compare"))
    assert r.context["a"].pk == data["a1"].pk and r.context["b"].pk == data["a2"].pk
    html = r.content.decode()
    assert "Improvement points for B" in html and "Overall" in html


# ---------------------------------------------------------------- 21


def test_no_sale_value_means_no_sale_percent(data, user_a):
    h = http_for(user_a)
    for name in PAGES:
        html = h.get(reverse(name)).content.decode()
        assert "of sale value" not in html, name
        assert "of sale<" not in html, name
    html = h.get(reverse("audits:detail", args=[data["a2"].pk])).content.decode()
    assert "of sale value" not in html
    r = h.get(reverse("portal:aging"))
    assert r.context["report"].kpis["net_base"] == "of stock value"
    assert all(row["pct_base"] == "of stock value" for row in r.context["report"].register)


def test_sale_value_shows_both_bases(client_a, user_a):
    f.make_audit(client_a, audit_date=date(2026, 8, 1), lines=LINES, sale_value=1000000)
    h = http_for(user_a)
    r = h.get(reverse("portal:aging"))
    assert r.context["report"].kpis["net_base"] == "of sale value"
    assert "of sale value" in r.content.decode()


# ---------------------------------------------------------------- 22


def test_aging_quarters_fy_2026_27(data, user_a):
    r = http_for(user_a).get(reverse("portal:aging") + "?period=fy-2026")
    rep = r.context["report"]
    assert rep.period.label == "FY 2026-27"
    assert [q.name for q in rep.quarters] == ["Q1", "Q2", "Q3", "Q4"]
    assert rep.quarters[0].start == date(2026, 4, 1) and rep.quarters[3].end == date(2027, 3, 31)
    s1_row = next(r for r in rep.calendar if r["store"].code == "S01")
    q1, q2, q3, q4 = s1_row["cells"]
    assert q1["items"][0]["chip"] == "First audit"
    assert q2["items"][0]["chip"] == "Delayed 11 days"  # 10 Apr -> 20 Jul = 101 days
    assert q3["empty"] == "Pending"
    assert q4["empty"] == "Not due yet"
    s2_row = next(r for r in rep.calendar if r["store"].code == "S02")
    assert s2_row["cells"][0]["empty"] == "Not audited"  # cycle counts are not full audits
    html = r.content.decode()
    assert "Pending" in html and "Not due yet" in html and "Not audited" in html


def test_staff_aging_with_client_picker(data, admin, client_b):
    r = http_for(admin).get(reverse("portal:aging") + f"?client={client_b.pk}")
    assert r.status_code == 200 and r.context["client"] == client_b
    assert "Beta Stores" in r.content.decode()


def test_tracker_and_due(data, user_a):
    r = http_for(user_a).get(reverse("portal:tracker") + "?month=2026-09")
    assert r.status_code == 200
    due = {row["store"].code: row["due"] for row in r.context["due"]}
    assert due["S01"].label == "Due soon"  # last full audit 20 Jul, 76 days ago
    assert due["S02"].label == "No full audit yet"


def test_show_more_partial(client_a, user_a):
    for i in range(30):
        f.make_audit(client_a, audit_date=TODAY - timedelta(days=i + 1), audit_type="cycle", lines=LINES)
    h = http_for(user_a)
    r = h.get(reverse("portal:audits"))
    assert len(r.context["page"]) == 25 and "Show more" in r.content.decode()
    r = h.get(reverse("portal:audits") + "?page=2&partial=rows")
    assert len(r.context["page"]) == 5 and "<html" not in r.content.decode()
