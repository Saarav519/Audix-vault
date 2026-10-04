"""Acceptance tests 1 to 12: the calculation core (Part B section 15)."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from core import calc
from core.calc import AuditData, Numbers, Obs, Thresholds
from core.formatting import compact, compact_inr, indian_group, inr, num, pct, qty


def grocery(**over):
    base = dict(stock_qty=1000, stock_value=100000, physical_qty=900, physical_value=90000,
                damage_qty=20, damage_value=2000, wbc_qty=30, wbc_value=3000)
    base.update(over)
    return Numbers(**base, category="Grocery")


# 1
def test_example_audit_numbers_and_remark():
    line = grocery()
    totals = calc.audit_totals([line])
    assert totals.total_physical_qty == 950
    assert totals.total_physical_value == 95000
    assert totals.diff_qty == -50
    assert totals.diff_value == -5000
    assert totals.var_pct == Decimal("-5")
    assert pct(totals.var_pct) == "−5.00%"
    assert calc.status_for(totals.var_pct) == "Review"
    assert calc.auto_remark(totals, [line]) == (
        "Shortage of 50 units (₹5,000), 5.0% of stock value. "
        "Total physical 950 = Physical 900 + Damage 20 + WBC (Without Barcode) 30. "
        "Largest shortage in Grocery (₹5,000). High variance, review required."
    )


# 2
def test_sale_value_percentage():
    line = grocery()
    totals = calc.audit_totals([line])
    remark = calc.auto_remark(totals, [line], sale_value=Decimal("2000000"))
    first = remark.split(". Total")[0] + "."
    assert first.endswith("5.0% of stock value and 0.25% of sale value.")
    assert calc.sale_pct(totals.diff_value, 2000000) == Decimal("-0.25")
    assert calc.sale_pct(totals.diff_value, None) is None
    assert calc.sale_pct(totals.diff_value, 0) is None


# 3
def test_matched_stock():
    line = Numbers(stock_qty=100, stock_value=5000, physical_qty=100, physical_value=5000, category="X")
    totals = calc.audit_totals([line])
    assert calc.auto_remark(totals, [line]) == "Stock matched, no variance."
    assert calc.status_for(totals.var_pct) == "Healthy"


# 4
def test_excess():
    line = Numbers(stock_qty=100, stock_value=10000, physical_qty=105, physical_value=10500, category="X")
    totals = calc.audit_totals([line])
    remark = calc.auto_remark(totals, [line])
    assert remark.startswith("Excess of 5 units (₹500), 5.0% of stock value.")
    assert "Largest shortage" not in remark


# 5
@pytest.mark.parametrize("value,expected", [("1.00", "Healthy"), ("1.01", "Watch"), ("2.00", "Watch"),
                                            ("2.01", "Review"), ("-1.00", "Healthy"), ("-2.01", "Review")])
def test_status_boundaries(value, expected):
    assert calc.status_for(Decimal(value)) == expected


def test_status_thresholds_change_result():
    th = Thresholds(good_pct=Decimal("0.5"), warn_pct=Decimal("1.0"))
    assert calc.status_for(Decimal("1.00"), th) == "Watch"
    assert calc.status_for(Decimal("1.01"), th) == "Review"
    line = Numbers(stock_qty=100, stock_value=10000, physical_qty=98.5, physical_value=9850, category="X")
    t = calc.audit_totals([line])
    assert "High variance" not in calc.auto_remark(t, [line])
    assert "High variance" in calc.auto_remark(t, [line], th=th)


# 6
def test_audit_totals_sum_every_field():
    lines = [
        Numbers(1, 2, 3, 4, 5, 6, 7, 8, category="A"),
        Numbers(10, 20, 30, 40, 50, 60, 70, 80, category="B"),
        Numbers("0.5", "1.25", 0, 0, 0, 0, "0.5", "1.25", category="C"),
    ]
    t = calc.audit_totals(lines)
    for f in calc.FIELDS:
        assert getattr(t, f) == sum(getattr(n, f) for n in lines)
    assert t.total_physical_value == sum(n.total_physical_value for n in lines)
    assert t.diff_qty == sum(n.diff_qty for n in lines)


# 7
def test_aging_chain():
    chain = calc.aging_chain([date(2026, 4, 5), date(2026, 7, 6), date(2026, 10, 4)], 90)
    assert chain[0] == (date(2026, 4, 5), None, None)
    assert chain[1] == (date(2026, 7, 6), 92, 2)
    assert chain[2] == (date(2026, 10, 4), 90, 0)
    assert calc.aging_text(None) == "First audit"
    assert calc.delay_text(None, None) == "Not applicable"


# 8
@pytest.mark.parametrize("days,label", [(75, "On schedule"), (76, "Due soon"), (90, "Due soon"),
                                        (91, "Overdue by 1 day"), (100, "Overdue by 10 days")])
def test_due_status(days, label):
    today = date(2026, 10, 4)
    st = calc.due_status(today - timedelta(days=days), today, 90, 15)
    assert st.label == label
    assert st.next_due == today - timedelta(days=days) + timedelta(days=90)


# 9
@dataclass
class A:
    id: int
    audit_date: date
    series: str
    created_at: datetime = datetime(2026, 1, 1)
    client_id: int = 1
    store_id: int = 1
    status: str = "published"


def test_previous_audit_respects_series():
    full1 = A(1, date(2026, 4, 5), "full")
    cyc1 = A(2, date(2026, 5, 1), "other")
    full2 = A(3, date(2026, 7, 6), "full")
    cyc2 = A(4, date(2026, 7, 10), "other")
    other_store = A(5, date(2026, 7, 1), "other", store_id=2)
    draft = A(6, date(2026, 7, 8), "other", status="draft")
    all_ = [full1, cyc1, full2, cyc2, other_store, draft]
    assert calc.pick_previous(cyc2, all_) is cyc1
    assert calc.pick_previous(full2, all_) is full1
    assert calc.pick_previous(full1, all_) is None
    assert calc.pick_previous(cyc1, all_) is None
    assert calc.series_for("cycle") == "other"
    assert calc.series_for("physical") == "full"


def test_previous_audit_same_day_tie_uses_creation_time():
    a = A(1, date(2026, 5, 1), "other", created_at=datetime(2026, 5, 1, 9))
    b = A(2, date(2026, 5, 1), "other", created_at=datetime(2026, 5, 1, 12))
    assert calc.pick_previous(b, [a, b]) is a
    assert calc.pick_previous(a, [a, b]) is None


# 10
@pytest.mark.parametrize("pa,pb,expected", [("0.5", "0.8", "Within limit"), ("3.0", "3.1", "Repeat issue"),
                                            ("1.5", "1.4", "No change"), ("3.0", "2.0", "Improved"),
                                            ("1.5", "2.5", "Worse"), ("-3.0", "-2.0", "Improved")])
def test_verdict(pa, pb, expected):
    assert calc.verdict(Decimal(pa), Decimal(pb), Thresholds()) == expected


def line(cat, stock_value, diff_value, damage_value=0, wbc_value=0):
    # physical absorbs the difference
    phys = stock_value + diff_value - damage_value - wbc_value
    return Numbers(stock_qty=stock_value / 100, stock_value=stock_value, physical_qty=phys / 100,
                   physical_value=phys, damage_qty=damage_value / 100, damage_value=damage_value,
                   wbc_qty=wbc_value / 100, wbc_value=wbc_value, category=cat)


def audit_data(i, d, lines, obs=(), store=1, sale=None):
    lines = {n.category: n for n in lines}
    return AuditData(id=i, audit_date=d, store_id=store, store_name=f"Store {store}", city=f"City {store}",
                     totals=calc.audit_totals(lines.values()), lines=lines, sale_value=sale, observations=list(obs))


def test_category_change_uses_absolute_values():
    a = audit_data(1, date(2026, 4, 1), [line("G", 100000, -3000)])
    b = audit_data(2, date(2026, 7, 1), [line("G", 100000, 2000)])
    cmp = calc.compare(a, b)
    row = cmp.categories[0]
    assert row.change_value.text == "▼ ₹1,000"
    assert row.change_value.tone == "good"


# 11
def test_followup_statuses():
    th = Thresholds()
    a = audit_data(1, date(2026, 4, 1), [line("G", 100000, -5000, damage_value=3000, wbc_value=3000),
                                         line("H", 100000, -5000)])
    def st(kind, cat, b_lines):
        b = audit_data(2, date(2026, 7, 1), b_lines)
        return calc.followup_status(Obs(kind, cat, text="t", recommendation="r"), a, b, th)

    # Shortage: m0 = 5%
    assert st("shortage", "G", [line("G", 100000, -3400), line("H", 0, 0)]) == "Resolved"  # 3.4 <= 3.5
    assert st("shortage", "G", [line("G", 100000, -800), line("H", 0, 0)]) == "Resolved"  # within good
    assert st("shortage", "G", [line("G", 100000, -4500)]) == "Improving"  # 4.5 < 4.75
    assert st("shortage", "G", [line("G", 100000, -4800)]) == "Still open"
    # Damage metric: 3% in A
    assert st("damage", "G", [line("G", 100000, -100, damage_value=2000)]) == "Resolved"
    assert st("damage", "G", [line("G", 100000, -100, damage_value=2800)]) == "Improving"
    assert st("damage", "G", [line("G", 100000, -100, damage_value=2900)]) == "Still open"
    # WBC metric
    assert st("wbc", "G", [line("G", 100000, -100, wbc_value=1000)]) == "Resolved"
    # Whole store
    assert st("process_gap", None, [line("G", 100000, -100)]) == "Check on site"
    # Good practice
    assert st("good_practice", "G", [line("G", 100000, -500)]) == "Maintained"
    assert st("good_practice", "G", [line("G", 100000, -1500)]) == "Slipped"
    assert st("good_practice", None, [line("G", 100000, -500)]) == "Maintained"
    assert calc.FOLLOWUP_PRESET["Resolved"] == "done"


def test_comparison_sentences_and_improvements():
    th = Thresholds()
    a = audit_data(1, date(2026, 4, 5), [line("G", 100000, -3000, wbc_value=500), line("H", 100000, -2500)],
                   obs=[Obs("shortage", "G", text="t", recommendation="Count G daily")])
    b = audit_data(2, date(2026, 7, 6), [line("G", 100000, -1000, wbc_value=1000), line("H", 100000, -2600)],
                   obs=[Obs("shortage", "H", text="t", recommendation="Count H daily"),
                        Obs("good_practice", None, text="t", recommendation="keep")])
    cmp = calc.compare(a, b, th)
    s = cmp.sentences
    assert s[0] == ("City 1 had a shortage of ₹5,500 (2.75% of stock value) on 5 Apr 2026 and "
                    "a shortage of ₹3,600 (1.80% of stock value) on 6 Jul 2026.")
    assert "Net variance is down 35% since the last audit." in s
    assert any(x.startswith("G improved the most") for x in s)
    assert "Repeat issue: H was above 2% in both audits." in s
    assert cmp.followups[0].status == "Resolved"
    assert cmp.improvements[0] == "H: Count H daily"
    assert "H was above 2% in both audits. Count it first at the next audit." in cmp.improvements
    assert "WBC is rising. Check barcode labels on new stock at receiving." in cmp.improvements
    labels = [r.label for r in cmp.overall]
    assert "Sale value" not in labels and "Variance % of sale value" not in labels


def test_comparison_sale_not_comparable():
    a = audit_data(1, date(2026, 4, 5), [line("G", 100000, -3000)], sale=Decimal(500000))
    b = audit_data(2, date(2026, 7, 6), [line("G", 100000, -1000)])
    cmp = calc.compare(a, b)
    row = [r for r in cmp.overall if r.label == "Variance % of sale value"][0]
    assert row.change.text == "Not comparable"
    assert row.b == "Not given"


def test_suggest_drafts():
    lines = [line("G", 100000, -4000), line("H", 100000, -2000), line("I", 100000, -100, damage_value=1500),
             line("J", 100000, -100, wbc_value=2000)]
    drafts = calc.suggest_drafts("abc", lines)
    kinds = [d["kind"] for d in drafts]
    assert kinds == ["shortage", "shortage", "damage", "wbc"]
    assert drafts[0]["category"] == "G" and drafts[0]["severity"] == "high"
    assert drafts[1]["severity"] == "medium"
    assert drafts[0]["text"].startswith("Shortage of 40 units (₹4,000, 4.00% of stock value). ")
    assert drafts == calc.suggest_drafts("abc", lines)  # stable
    good = calc.suggest_drafts("abc", [line("G", 100000, -100)])
    assert good[0]["kind"] == "good_practice" and good[0]["category"] is None


# 12
def test_indian_formatting():
    assert indian_group("123456") == "1,23,456"
    assert num(123456) == "1,23,456"
    assert num(12345678) == "1,23,45,678"
    assert num(999) == "999"
    assert compact(12345678) == "1.23 Cr"
    assert compact_inr(-250000) == "−₹2.50 L"
    assert inr(-5000) == "−₹5,000"
    assert qty(Decimal("12.500")) == "12.5"
    assert qty(Decimal("1200.000")) == "1,200"


def test_periods_and_quarters():
    today = date(2026, 10, 4)
    p = calc.Period.parse(None, today)
    assert p.label == "FY 2026-27"
    qs = p.quarters()
    assert [q.start for q in qs] == [date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1), date(2027, 1, 1)]
    assert qs[3].end == date(2027, 3, 31)
    assert calc.quarter_empty_state(qs[0], today) == "Not audited"
    assert calc.quarter_empty_state(qs[2], today) == "Pending"
    assert calc.quarter_empty_state(qs[3], today) == "Not due yet"
    cy = calc.Period.parse("cy-2026", today)
    assert cy.quarters()[0].start == date(2026, 1, 1)


def test_register_remark():
    t = calc.audit_totals([grocery()])
    assert calc.register_remark(t, None, None, None, "Review", "Grocery") == (
        "Shortage 5.00% of stock value. First audit on record. Largest shortage in Grocery. "
        "High variance, review required.")
    assert calc.register_remark(t, Decimal(2000000), 92, 2, "Review", "Grocery").startswith(
        "Shortage 0.25% of sale value. Audit delayed by 2 days.")


def test_dashboard_windows_and_buckets():
    today = date(2026, 10, 4)
    assert calc.window("daily", today) == (date(2026, 9, 28), today)
    assert calc.previous_window("daily", today) == (date(2026, 9, 21), date(2026, 9, 27))
    assert len(calc.buckets("daily", today)) == 14
    assert len(calc.buckets("weekly", today)) == 12
    m = calc.buckets("monthly", today)
    assert len(m) == 12 and m[-1].start == date(2026, 10, 1) and m[0].start == date(2025, 11, 1)
    assert calc.buckets("yearly", today, 2024)[0].label == "2024"
