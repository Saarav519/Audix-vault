"""Category-wise tables show quantity and value (detail page, compare page, entry preview, sign-off sheet)."""

import re
from datetime import date

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from core import calc
from core.formatting import inr, pct, qty
from reports import signoff
from tests import factories as f
from tests.test_signoff import FIRST_AUDIT_FORBIDDEN, client_with, make_pair, pdf

pytestmark = pytest.mark.django_db


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def section(html, testid):
    m = re.search(rf'<table[^>]*data-testid="{testid}".*?</table>', html, re.S)
    assert m, testid
    return m.group(0)


def values(n, var_decimals=2):
    return [qty(n.stock_qty), inr(n.stock_value), qty(n.total_physical_qty), inr(n.total_physical_value),
            qty(n.diff_qty, signed=True), inr(n.diff_value), pct(n.var_pct, var_decimals)]


def assert_totals_equal(numbers, totals):
    for fld in calc.FIELDS:
        assert getattr(numbers, fld) == getattr(totals, fld), fld


def test_seven_categories_on_detail_compare_and_pdf(admin):
    c = client_with(7)
    audit, prev = make_pair(c, 7, True)
    h = http_for(admin)
    nums = audit.numbers_by_category()
    t = audit.totals

    # audit detail page
    r = h.get(reverse("audits:detail", args=[audit.pk]))
    table = section(r.content.decode(), "category-table")
    for n in nums:
        for v in values(n, 1) + [qty(n.physical_qty), qty(n.damage_qty), qty(n.wbc_qty)]:
            assert v in table, (n.category, v)
    assert ">Last audit<" in table and ">Change<" in table
    total = r.context["cat_total"].numbers
    assert_totals_equal(total, t)
    foot = table[table.index("<tfoot>"):]
    for v in values(t, 1):
        assert v in foot, v

    # compare page, previous (A) vs this audit (B)
    r = h.get(reverse("portal:compare") + f"?client={c.pk}&a={prev.pk}&b={audit.pk}")
    table = section(r.content.decode(), "compare-categories")
    for n in nums + prev.numbers_by_category():
        for v in values(n):
            assert v in table, (n.category, v)
    cmp = r.context["cmp"]
    assert_totals_equal(cmp.category_total.b, t)
    assert_totals_equal(cmp.category_total.a, prev.totals)
    foot = table[table.index("<tfoot>"):]
    for v in values(t) + values(prev.totals):
        assert v in foot, v
    assert ">Units<" in table and cmp.category_total.change_units.text in foot
    # damage and WBC are part of Total physical, so they have no separate columns here
    assert "damage" not in table.lower() and "WBC</th>" not in table

    # sign-off sheet
    pages, text = pdf(signoff.signoff_pdf(audit, "https://x/"))
    assert pages == 1
    for n in nums:
        for v in values(n):
            assert v in text, (n.category, v)
    for v in values(t):
        assert v in text, v
    assert "Total" in text


@pytest.mark.parametrize("n", [21, 26, 30])
def test_more_than_twenty_categories_get_other_row(n):
    c = client_with(n)
    audit, _ = make_pair(c, n, True)
    nums = audit.numbers_by_category()
    rows = calc.sheet_categories(nums)
    assert len(rows) == 20 and sum(1 for r in rows if r.is_other) == 1
    assert rows[-1].name == f"Other ({n - 19} more)"
    worst = sorted(nums, key=lambda x: x.diff_value)[:19]
    assert {r.name for r in rows[:-1]} == {x.category for x in worst}
    assert_totals_equal(calc.audit_totals(r.numbers for r in rows), audit.totals)
    assert_totals_equal(calc.sheet_total(nums).numbers, audit.totals)
    pages, text = pdf(signoff.signoff_pdf(audit, "https://x/"))
    assert pages == 1 and f"Other ({n - 19} more)" in text
    for v in values(rows[-1].numbers) + values(audit.totals):
        assert v in text, v


def test_first_audit_detail_has_no_last_audit_group(admin):
    c = client_with(7)
    audit, _ = make_pair(c, 7, False)
    r = http_for(admin).get(reverse("audits:detail", args=[audit.pk]))
    table = section(r.content.decode(), "category-table")
    assert "Last audit" not in table and "Change" not in table
    assert "Last audit" not in r.content.decode()
    _, text = pdf(signoff.signoff_pdf(audit, "https://x/"))
    for word in FIRST_AUDIT_FORBIDDEN:
        assert word not in text


def test_entry_preview_shows_last_and_now_quantities(admin):
    c = client_with(5)
    prev = f.make_audit(c, audit_date=date(2026, 4, 5))
    audit = f.make_audit(c, audit_date=date(2026, 7, 6), publish=False)
    html = http_for(admin).get(reverse("audits:edit", args=[audit.pk])).content.decode()
    table = section(html, "compare-categories")
    assert ">Last<" in table and ">Now<" in table and ">Qty<" in table
    for n in prev.numbers_by_category():
        for v in values(n):
            assert v in table, v
