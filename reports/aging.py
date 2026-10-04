"""Yearly audit aging report data (5.13). Used by the screen and the exports."""

from __future__ import annotations

from dataclasses import dataclass

from audits.models import Series
from core import calc
from core.calc import D
from reports.queries import last_full_audits


@dataclass
class AgingReport:
    client: object
    period: calc.Period
    quarters: list
    kpis: dict
    calendar: list
    register: list
    footer: dict
    sale_basis: bool
    stores: list


def build_aging(client, scope_qs, stores, period: calc.Period, today, store=None) -> AgingReport:
    th = client.thresholds()
    if store is not None:
        stores = [s for s in stores if s.pk == store.pk]
    store_ids = [s.pk for s in stores]
    full_all = scope_qs.filter(series=Series.FULL, store_id__in=store_ids)
    audits = list(full_all.filter(audit_date__gte=period.start, audit_date__lte=period.end)
                  .select_related("store").order_by("store__code", "audit_date", "created_at"))
    quarters = period.quarters()

    # KPIs
    n = len(audits)
    with_aging = [a for a in audits if a.aging_days is not None]
    on_time = [a for a in with_aging if not a.delay_days]
    delayed = [a for a in with_aging if a.delay_days]
    basis = calc.sale_basis(audits)
    total_stock, total_diff = basis.stock_total, basis.net_diff_all
    sale_basis_flag = basis.has_any_sale
    avg_aging = round(sum(a.aging_days for a in with_aging) / len(with_aging)) if with_aging else None
    kpis = {
        "full_audits": n,
        "stores_audited": len({a.store_id for a in audits}),
        "stores_total": len(stores),
        "avg_aging": avg_aging,
        "on_time": len(on_time),
        "on_time_pct": calc.pct_of(len(on_time), len(with_aging)) if with_aging else None,
        "delayed": len(delayed),
        "longest_delay": max((a.delay_days for a in delayed), default=None),
        "net_diff": total_diff,
        "basis": basis,
        "net_text": basis.sale_text() or basis.stock_text(),
    }

    # Quarter calendar
    last_full = last_full_audits(full_all)
    by_store = {}
    for a in audits:
        by_store.setdefault(a.store_id, []).append(a)
    calendar = []
    for s in stores:
        cells = []
        mine = by_store.get(s.pk, [])
        for q in quarters:
            items = []
            for a in mine:
                if q.contains(a.audit_date):
                    label, tone = calc.aging_chip(a.aging_days, a.delay_days)
                    items.append({"audit": a, "chip": label, "tone": tone})
            cells.append({"quarter": q, "items": items, "empty": None if items else calc.quarter_empty_state(q, today)})
        due = calc.due_status(last_full.get(s.pk), today, client.cycle_days, client.soon_days)
        calendar.append({"store": s, "cells": cells, "due": due})

    # Register
    register = []
    for a in audits:
        totals = a.totals
        sp = calc.sale_pct(a.diff_value, a.sale_value)
        register.append({
            "audit": a,
            "pct": sp if sp is not None else a.var_pct_stock,
            "pct_base": "of sale value" if sp is not None else "of stock value",
            "aging": calc.aging_text(a.aging_days),
            "delay": calc.delay_text(a.aging_days, a.delay_days),
            "remark": calc.register_remark(totals, a.sale_value, a.aging_days, a.delay_days,
                                           calc.status_for(a.var_pct_stock, th), a.largest_shortage_category or None),
        })
    footer = {"basis": basis, "n": basis.n_total, "stock": total_stock, "diff": total_diff,
              "pct_stock": basis.pct_stock_all, "avg_aging": avg_aging, "late": len(delayed),
              "sale_rows": basis.has_any_sale, "n_sale": basis.n_with_sale, "sale": basis.sale_total,
              "diff_sale": basis.net_diff_with_sale, "pct_sale": basis.pct_sale,
              "stock_sale": sum((a.stock_value for a in audits if calc.has_sale(a.sale_value)), D(0))}
    return AgingReport(client, period, quarters, kpis, calendar, register, footer, sale_basis_flag, stores)
