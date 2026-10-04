"""Compare page and Add audit step 3 with categories that exist in only one audit; '+' for an excess;
'1 unit' plurals."""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from audits import services
from audits.models import AuditLine
from core import calc
from core.formatting import fix_unit_plural, inr, qty, units
from reports import signoff
from tests import factories as f
from tests.test_signoff import client_with, pdf

pytestmark = pytest.mark.django_db

def line(i, excess=False):
    d = dict(stock_qty=1000 + i * 10, stock_value=100000 + i * 5000, physical_qty=990 + i * 10,
             physical_value=99000 + i * 5000 - i * 100)
    if excess:
        d.update(physical_qty=1003 + i * 10, physical_value=100700 + i * 5000)
    return d


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def drop_line(audit, category_name, publish=True):
    AuditLine.objects.filter(audit=audit, category__name=category_name).delete()
    audit.refresh_snapshot()
    if publish:
        services.publish(audit)
    audit.refresh_from_db()


@pytest.fixture
def pair():
    """A has categories 1, 2, 3 (not 4); B has 1, 2, 4 (not 3)."""
    c = client_with(4, "One Sided")
    names = [cat.name for cat in c.categories.order_by("sort_order")]
    a = f.make_audit(c, audit_date=date(2026, 4, 5), lines=[line(0), line(1), line(2)])
    b = f.make_audit(c, audit_date=date(2026, 7, 6), lines=[line(0, True), line(1), line(2), line(3)], publish=False)
    drop_line(b, names[2])
    return c, a, b, names


def table(html, testid="compare-categories"):
    m = re.search(rf'<table[^>]*data-testid="{testid}".*?</table>', html, re.S)
    assert m
    return m.group(0)


def row_of(html, name):
    m = re.search(rf"<tr[^>]*><td class=\"cat\">{re.escape(name)}</td>.*?</tr>", html, re.S)
    assert m, name
    return m.group(0)


def assert_totals_equal(n, totals):
    for fld in calc.FIELDS:
        assert getattr(n, fld) == getattr(totals, fld), fld


def test_compare_shows_every_category_and_full_totals(pair, admin):
    c, a, b, names = pair
    r = http_for(admin).get(reverse("portal:compare") + f"?client={c.pk}&a={a.pk}&b={b.pk}")
    html = r.content.decode()
    t = table(html)
    cmp = r.context["cmp"]

    only_a, only_b = row_of(t, names[2]), row_of(t, names[3])
    assert "Only in A" in only_a and "Only in B" in only_b
    assert only_a.count("—") >= 7 + 2 + 4  # B side, change columns, B damage and WBC
    assert only_b.count("—") >= 7 + 2  # A side, change columns
    assert [x.category for x in cmp.table_rows] == names

    # Total row = each audit's real totals
    total = cmp.category_total
    assert_totals_equal(total.a, a.totals)
    assert_totals_equal(total.b, b.totals)
    foot = t[t.index("<tfoot>"):]
    total_row = row_of(foot, "Total")
    for v in (qty(b.totals.stock_qty), inr(b.totals.stock_value), qty(a.totals.stock_qty), inr(a.totals.stock_value),
              qty(b.totals.total_physical_qty), inr(b.totals.diff_value, signed=True)):
        assert v in total_row, v

    # common-category subtotals drive the changes
    na = [a.numbers_by_category()[i] for i in (0, 1)]
    nb = [x for x in b.numbers_by_category() if x.category in names[:2]]
    ca, cb = calc.audit_totals(na), calc.audit_totals(nb)
    assert_totals_equal(cmp.common_total.a, ca)
    assert_totals_equal(cmp.common_total.b, cb)
    assert total.change_value.text == calc.change_in_value(ca, cb).text
    assert total.change_units.text == calc.change_in_units(ca, cb).text
    assert total.change_value.text != calc.change_in_value(a.totals, b.totals).text
    common_row = row_of(foot, "Common categories only")
    assert qty(cb.stock_qty) in common_row and total.change_units.text in total_row

    assert ("2 categories are not in both audits. Totals show every category of each audit; "
            "changes compare common categories only.") in html


def test_no_note_when_categories_match(admin):
    c = client_with(3, "Matching")
    a = f.make_audit(c, audit_date=date(2026, 4, 5), lines=[line(0), line(1), line(2)])
    b = f.make_audit(c, audit_date=date(2026, 7, 6), lines=[line(0), line(1), line(2)])
    html = http_for(admin).get(reverse("portal:compare") + f"?client={c.pk}&a={a.pk}&b={b.pk}").content.decode()
    assert "not in both audits" not in html and "Common categories only" not in html


def test_entry_step3_shows_one_sided_categories(pair, admin):
    c, a, b, names = pair
    draft = f.make_audit(c, audit_date=date(2026, 9, 6), lines=[line(0), line(1), line(2)], publish=False)
    # previous is B (1, 2, 4); the draft has 1, 2, 3
    html = http_for(admin).get(reverse("audits:edit", args=[draft.pk])).content.decode()
    t = table(html)
    assert ">Last<" in t and ">Now<" in t
    assert "Only in A" in row_of(t, names[3])  # A = last audit, which has category 4
    assert "Only in B" in row_of(t, names[2])  # B = this audit, which has category 3
    assert "2 categories are not in both audits" in html


def test_plus_before_excess_rupees(pair, admin):
    c, a, b, names = pair
    first = b.numbers_by_category()[0]
    assert first.diff_value > 0
    plus_value, plus_qty = inr(first.diff_value, signed=True), qty(first.diff_qty, signed=True)
    assert plus_value.startswith("+₹") and plus_qty.startswith("+")
    h = http_for(admin)
    detail = table(h.get(reverse("audits:detail", args=[b.pk])).content.decode(), "category-table")
    assert plus_value in row_of(detail, names[0]) and plus_qty in row_of(detail, names[0])
    cmp_html = table(h.get(reverse("portal:compare") + f"?client={c.pk}&a={a.pk}&b={b.pk}").content.decode())
    assert plus_value in row_of(cmp_html, names[0])
    _, text = pdf(signoff.signoff_pdf(b, "https://x/"))
    assert plus_value in text
    # shortage keeps the true minus; Var % has no plus
    short = b.numbers_by_category()[1]
    assert inr(short.diff_value, signed=True).startswith("−₹")
    assert f"+{calc.round_dec(first.var_pct, 1)}%" not in row_of(detail, names[0])


def test_unit_plurals():
    assert units(1) == "1 unit" and units(2) == "2 units" and units(-1) == "−1 unit"
    assert units(1, signed=True) == "+1 unit" and units(Decimal("1.5")) == "1.5 units"
    assert units(0) == "0 units" and units(11) == "11 units" and units(1001) == "1,001 units"
    assert fix_unit_plural("1 units without barcode, 21 units, 1,001 units") == (
        "1 unit without barcode, 21 units, 1,001 units")
    one = calc.Numbers(stock_qty=10, stock_value=1000, physical_qty=9, physical_value=900)
    assert "of 1 unit (" in calc.auto_remark(one, [one])
    assert calc.change_in_units(calc.Numbers(stock_qty=10, physical_qty=8),
                                calc.Numbers(stock_qty=10, physical_qty=9)).text == "▼ 1 unit"
    drafts = calc.suggest_drafts("x", [calc.Numbers(category="Toys", stock_qty=100, stock_value=10000,
                                                    physical_qty=99, physical_value=9900, wbc_qty=1,
                                                    wbc_value=200)])
    wbc = next(d for d in drafts if d["kind"] == calc.WBC)
    assert wbc["text"].startswith("1 unit without barcode")


def test_one_unit_on_pages(admin):
    c = client_with(2, "Single Unit")
    audit = f.make_audit(c, audit_date=date(2026, 7, 6), lines=[
        dict(stock_qty=100, stock_value=10000, physical_qty=99, physical_value=9900),
        dict(stock_qty=50, stock_value=5000, physical_qty=50, physical_value=5000)])
    _, text = pdf(signoff.signoff_pdf(audit, "https://x/"))
    assert "1 units" not in text and "−1 unit" in text
    html = http_for(admin).get(reverse("audits:detail", args=[audit.pk])).content.decode()
    assert "1 units" not in re.sub(r"\d1 units", "", html)
