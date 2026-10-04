"""One-page audit sign-off sheet (reports/signoff.py)."""

import io
from datetime import date

import pytest
from django.test import Client as HttpClient
from django.urls import reverse
from django.utils.text import slugify
from pypdf import PdfReader

from activity.models import ActivityLog
from audits.models import Observation
from clients.models import Category, Client, Store
from core import calc
from core.formatting import inr, pct
from reports import signoff
from tests import factories as f

pytestmark = pytest.mark.django_db
FIRST_AUDIT_FORBIDDEN = ("Last audit", "Compared with", "Change vs")


def pdf(data):
    reader = PdfReader(io.BytesIO(data))
    return len(reader.pages), "\n".join(p.extract_text() for p in reader.pages)


def lines_for(n, shift=0):
    out = []
    for i in range(n):
        stock = 100000 + i * 3711 + shift
        diff = -(500 + i * 97 + shift // 10)
        out.append(dict(stock_qty=1000 + i, stock_value=stock, physical_qty=990 + i, physical_value=stock + diff - 300,
                        damage_qty=2, damage_value=200, wbc_qty=1, wbc_value=100))
    return out


def client_with(n, name=None):
    name = name or f"Client With {n} Categories"
    c = Client.objects.create(name=name, slug=slugify(name), contact_emails="x@example.com")
    Store.objects.create(client=c, code="S01", name="Very Long Store Name For Testing", city="Navi Mumbai")
    for i in range(n):
        Category.objects.create(client=c, name=f"Category {i + 1:02d} with a fairly long descriptive name",
                                sort_order=i)
    return c


def make_pair(c, n, with_previous):
    prev = None
    if with_previous:
        prev = f.make_audit(c, audit_date=date(2026, 4, 5), lines=lines_for(n, shift=5000))
    audit = f.make_audit(c, audit_date=date(2026, 7, 6), lines=lines_for(n), sale_value=2500000)
    return audit, prev


def check_common(text, audit):
    assert audit.client.name in text
    assert audit.store.code in text
    assert audit.reference in text
    assert "6 Jul 2026" in text
    t = audit.totals
    for value in (inr(t.stock_value), inr(t.total_physical_value), inr(t.diff_value), pct(audit.var_pct_stock)):
        assert value in text, value
    assert "Store manager" in text


def test_normal_audit_with_previous():
    c = client_with(7)
    audit, prev = make_pair(c, 7, True)
    pages, text = pdf(signoff.signoff_pdf(audit, "https://vault.example.com/a/"))
    assert pages == 1
    check_common(text, audit)
    assert "Compared with last audit" in text and "Change vs last audit" in text
    assert "Net variance is" in text
    assert "Sale value" in text and "5 Apr 2026" in text  # sale period from the previous audit


def test_first_audit_has_no_comparison():
    c = client_with(7)
    audit, _ = make_pair(c, 7, False)
    assert audit.previous_audit_id is None
    pages, text = pdf(signoff.signoff_pdf(audit, "https://vault.example.com/a/"))
    assert pages == 1
    check_common(text, audit)
    for word in FIRST_AUDIT_FORBIDDEN:
        assert word not in text, word
    assert "This audit at a glance" in text and "Damage" in text and "WBC" in text
    assert "recommendations: " not in text  # no follow-up line


@pytest.mark.parametrize("n", [10, 14, 18, 26, 30])
@pytest.mark.parametrize("with_previous", [True, False])
def test_many_categories_fit_one_page(n, with_previous):
    c = client_with(n, f"Many {n} {with_previous}")
    audit, _ = make_pair(c, n, with_previous)
    pages, text = pdf(signoff.signoff_pdf(audit, "https://vault.example.com/a/"))
    assert pages == 1
    check_common(text, audit)
    nums = audit.numbers_by_category()
    if n <= 26:
        for x in nums:
            for value in (inr(x.stock_value), inr(x.total_physical_value), inr(x.diff_value), pct(x.var_pct)):
                assert value in text, (x.category, value)
    else:
        rows = calc.sheet_categories(nums)
        assert len(rows) == 25 and rows[-1].name == "Other (6 more)"
        assert "Other (6 more)" in text
        for r in rows:
            assert inr(r.numbers.stock_value) in text and inr(r.numbers.diff_value) in text
    if n >= 14:
        assert "compact two-column view" in text
        assert "Total physical" in text
    if not with_previous:
        for word in FIRST_AUDIT_FORBIDDEN:
            assert word not in text


def test_long_observations_are_clipped_on_one_page():
    c = client_with(12)
    audit, _ = make_pair(c, 12, True)
    long = ("Stock in the back room was not counted at close and transfers between sections were recorded late, "
            "so the system stock and the floor count drifted apart over the week. ") * 3
    long = long[:330]
    for i in range(5):
        Observation.objects.create(audit=audit, kind="shortage", severity="high", text=long,
                                   recommendation=long, sort_order=i + 1)
    pages, text = pdf(signoff.signoff_pdf(audit, "https://vault.example.com/a/"))
    assert pages == 1
    assert "..." in text
    assert "more in the portal" in text


def test_no_sale_value():
    c = client_with(6)
    audit = f.make_audit(c, audit_date=date(2026, 7, 6), lines=lines_for(6))
    pages, text = pdf(signoff.signoff_pdf(audit, "https://vault.example.com/a/"))
    assert pages == 1
    assert "Sale value not given for this audit" in text
    assert "of sale value" not in text


def test_draft_has_watermark():
    c = client_with(5)
    audit = f.make_audit(c, audit_date=date(2026, 7, 6), lines=lines_for(5), publish=False)
    pages, text = pdf(signoff.signoff_pdf(audit, "https://vault.example.com/a/"))
    assert pages == 1 and "DRAFT, not final" in text


# ---------------------------------------------------------------- endpoint and access


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def test_endpoint_for_staff_and_owner(admin, auditor, user_a, client_a):
    audit = f.make_audit(client_a, audit_date=date(2026, 7, 6))
    url = reverse("audits:signoff_sheet", args=[audit.pk])
    for user in (admin, auditor, user_a):
        r = http_for(user).get(url)
        assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
        assert r["Content-Disposition"] == (f'attachment; filename="audix-signoff-{client_a.slug}-'
                                            f'{slugify(audit.store.code)}-{audit.reference}.pdf"')
        assert pdf(r.content)[0] == 1
    row = ActivityLog.objects.filter(action="Downloaded sign-off sheet").latest("created_at")
    assert row.action_type == "export" and row.audit == audit and row.client == client_a


def test_endpoint_denies_others(user_a, user_b, client_a, admin):
    audit = f.make_audit(client_a, audit_date=date(2026, 7, 6))
    draft = f.make_audit(client_a, audit_date=date(2026, 7, 7), publish=False)
    assert http_for(user_b).get(reverse("audits:signoff_sheet", args=[audit.pk])).status_code == 404
    assert http_for(user_a).get(reverse("audits:signoff_sheet", args=[draft.pk])).status_code == 404
    r = http_for(admin).get(reverse("audits:signoff_sheet", args=[draft.pk]))
    assert r.status_code == 200 and "DRAFT, not final" in pdf(r.content)[1]
    assert HttpClient().get(reverse("audits:signoff_sheet", args=[audit.pk])).status_code == 302


def test_buttons_on_detail_and_entry(admin, client_a):
    audit = f.make_audit(client_a, audit_date=date(2026, 7, 6), publish=False)
    url = reverse("audits:signoff_sheet", args=[audit.pk])
    h = http_for(admin)
    assert url in h.get(reverse("audits:detail", args=[audit.pk])).content.decode()
    assert url in h.get(reverse("audits:edit", args=[audit.pk])).content.decode()
    new_page = h.get(reverse("audits:new") + f"?client={client_a.pk}").content.decode()
    assert "Download sign-off sheet (PDF)" in new_page and "signoff-sheet" not in new_page  # disabled until saved
