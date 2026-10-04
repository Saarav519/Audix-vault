"""Excel and PDF exports: aging report, audits summary, single comparison."""

from __future__ import annotations

import uuid

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from activity.log import log
from activity.models import ActionType
from audits import services
from audits.models import Audit
from core import exports as ex
from core.formatting import fmt_date, inr, pct, qty
from core.permissions import portal_required
from reports.aging import build_aging
from reports.queries import window_totals
from reports.views import aging_inputs, describe_audit_filters, filtered_audits, resolve, scope_stores

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _response(data: bytes, content_type: str, filename: str):
    resp = HttpResponse(data, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


def _slug(client):
    return client.slug or "client"


# ---------------------------------------------------------------- aging


@portal_required
def aging_export(request, fmt):
    if fmt not in ("xlsx", "pdf"):
        raise Http404
    client, scope, _ = resolve(request)
    qs, period, _, stores, store, t = aging_inputs(request, client, scope)
    rep = build_aging(client, qs, stores, period, t, store)
    period_text = period.label + (f" · {store.code} {store.name}" if store else "")
    log(request, ActionType.EXPORT, f"Exported aging report ({fmt.upper()})", detail=period_text, client=client)
    name = f"audix-aging-{_slug(client)}-{period.key}.{fmt}"
    if fmt == "xlsx":
        return _response(aging_xlsx(client, rep, period_text), XLSX, name)
    return _response(aging_pdf(client, rep, period_text), "application/pdf", name)


def aging_xlsx(client, rep, period_text) -> bytes:
    wb, ws = ex.new_workbook()
    ws.title = "Audit register"
    sh = ex.Sheet(ws, client.name, "Yearly audit aging report", period_text)
    k = rep.kpis
    sh.add(["Full audits", k["full_audits"]])
    sh.add(["Stores audited", f"{k['stores_audited']} of {k['stores_total']}"])
    sh.add(["Average aging (days)", k["avg_aging"]])
    sh.add(["Done on time", k["on_time"], k["on_time_pct"]], [None, None, "pct"])
    sh.add(["Delayed audits", k["delayed"]])
    sh.add(["Longest delay (days)", k["longest_delay"]])
    b = k["basis"]
    sh.add(["Net difference, all audits", k["net_diff"], b.pct_stock_all, "of stock value"], [None, "inr", "pct", None])
    if b.has_any_sale:
        sh.add([f"Net difference, audits with sale value ({b.n_with_sale} of {b.n_total})", b.net_diff_with_sale,
                b.pct_sale, "of sale value"], [None, "inr", "pct", None])
    sh.row += 1
    sh.header(["Audit date", "Store ID", "Location", "Stock value", "Sale value", "Difference value", "Shortage %",
               "Percentage base", "Aging (days)", "Delay", "Remarks"], [13, 9, 26, 15, 15, 15, 11, 15, 11, 14, 60])
    for r in rep.register:
        a = r["audit"]
        sh.add([a.audit_date, a.store.code, f"{a.store.name}, {a.store.city}", a.stock_value,
                a.sale_value if a.sale_value else "Not given", a.diff_value, r["pct"], r["pct_base"],
                a.aging_days if a.aging_days is not None else "First audit", r["delay"], r["remark"]],
               ["date", None, None, "inr", "inr" if a.sale_value else None, "inr", "pct", None, None, None, None])
    f = rep.footer
    sh.add([f"All audits ({f['n']})", "", "", f["stock"], "", f["diff"], f["pct_stock"], "of stock value",
            f"Avg {f['avg_aging']}" if f["avg_aging"] is not None else "", f"{f['late']} late", ""],
           [None, None, None, "inr", None, "inr", "pct", None, None, None, None], bold=True)
    if f["sale_rows"]:
        sh.add([f"Audits with sale value ({f['n_sale']} of {f['n']})", "", "", f["stock_sale"], f["sale"], f["diff_sale"],
                f["pct_sale"], "of sale value", "", "", ""],
               [None, None, None, "inr", "inr", "inr", "pct", None, None, None, None], bold=True)
    sh.footer()

    ws2 = wb.create_sheet("Quarter calendar")
    sh2 = ex.Sheet(ws2, client.name, "Quarter-wise audit calendar", period_text)
    sh2.header(["Store ID", "Store"] + [f"{q.name} ({q.months})" for q in rep.quarters] + ["Next full audit due", "Status"],
               [9, 26, 26, 26, 26, 26, 16, 18])
    for row in rep.calendar:
        cells = []
        for c in row["cells"]:
            if c["items"]:
                cells.append("; ".join(f"{fmt_date(it['audit'].audit_date)} ({it['chip']})" for it in c["items"]))
            else:
                cells.append(c["empty"])
        sh2.add([row["store"].code, f"{row['store'].name}, {row['store'].city}"] + cells
                + [row["due"].next_due, row["due"].label],
                [None, None, None, None, None, None, "date", None])
    sh2.footer()
    return ex.workbook_bytes(wb)


def aging_pdf(client, rep, period_text) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer

    def story():
        k = rep.kpis
        s = [ex.paragraph("Yearly audit aging report", "h1"),
             ex.paragraph(f"{client.name} · {period_text} · full audits, {client.cycle_days}-day cycle", "small"),
             Spacer(1, 4 * mm)]
        s.append(ex.pdf_table([
            ["Full audits", "Stores audited", "Average aging", "Done on time", "Delayed audits", "Net difference"],
            [str(k["full_audits"]), f"{k['stores_audited']} of {k['stores_total']}",
             f"{k['avg_aging']} days" if k["avg_aging"] is not None else "-",
             f"{pct(k['on_time_pct'], 0)} ({k['on_time']})" if k["on_time_pct"] is not None else "-",
             f"{k['delayed']}" + (f" (longest {k['longest_delay']} days)" if k["longest_delay"] else ""),
             f"{inr(k['net_diff'])}, {k['net_text']}"],
        ]))
        s.append(ex.paragraph("Quarter-wise audit calendar", "h2"))
        rows = [["Store"] + [f"{q.name} {q.months}" for q in rep.quarters] + ["Next full audit due"]]
        for row in rep.calendar:
            cells = []
            for c in row["cells"]:
                cells.append(" / ".join(f"{fmt_date(it['audit'].audit_date)} {it['chip']}" for it in c["items"])
                             if c["items"] else c["empty"])
            due = row["due"]
            rows.append([f"{row['store'].code} {row['store'].name}"] + cells
                        + [f"{fmt_date(due.next_due) if due.next_due else '-'} {due.label}"])
        s.append(ex.pdf_table(rows, col_widths=[45 * mm] + [44 * mm] * 4 + [48 * mm]))
        s.append(ex.paragraph("Audit register", "h2"))
        rows = [["Audit date", "Store", "Location", "Stock value", "Sale value", "Difference", "Shortage %", "Aging",
                 "Delay", "Remarks"]]
        for r in rep.register:
            a = r["audit"]
            rows.append([fmt_date(a.audit_date), a.store.code, f"{a.store.name}, {a.store.city}", inr(a.stock_value),
                         inr(a.sale_value) if a.sale_value else "Not given", inr(a.diff_value),
                         f"{pct(r['pct'])} {r['pct_base']}", r["aging"], r["delay"], r["remark"]])
        f = rep.footer
        rows.append([f"All audits ({f['n']})", "", "", inr(f["stock"]), "", inr(f["diff"]),
                     f"{pct(f['pct_stock'])} of stock value",
                     f"Avg {f['avg_aging']} days" if f["avg_aging"] is not None else "", f"{f['late']} late", ""])
        if f["sale_rows"]:
            rows.append([f"With sale value ({f['n_sale']} of {f['n']})", "", "", inr(f["stock_sale"]), inr(f["sale"]),
                         inr(f["diff_sale"]), f"{pct(f['pct_sale'])} of sale value", "", "", ""])
        s.append(ex.pdf_table(rows, col_widths=[20 * mm, 13 * mm, 34 * mm, 24 * mm, 24 * mm, 22 * mm, 30 * mm,
                                                17 * mm, 20 * mm, 65 * mm], num_cols=(3, 4, 5),
                              footer=2 if f["sale_rows"] else 1))
        return s

    return ex.build_pdf(story, client.name, "Yearly audit aging report", period_text)


# ---------------------------------------------------------------- audits summary


EXPORT_CAP = 5000


@portal_required
def audits_export(request, fmt):
    if fmt not in ("xlsx", "pdf"):
        raise Http404
    client, scope, _ = resolve(request)
    qs, f = filtered_audits(request, scope)
    filters = describe_audit_filters(f, scope_stores(client, scope))
    return export_audits(request, fmt, qs, filters, client.name, _slug(client), client=client)


def export_audits(request, fmt, qs, filters, letter_name, file_slug, client=None, include_client=False):
    """Audits summary export shared by the portal and the admin Audits page. `qs` is the filtered queryset,
    exactly as the list shows it; `filters` is describe_audit_filters() output."""
    total = qs.count()
    audits = list(qs.select_related("store", "client")[:EXPORT_CAP])
    summary = window_totals(qs)
    filter_text = filters_text(filters)
    count_text = f"{total} audit{'s' if total != 1 else ''}"
    if total > EXPORT_CAP:
        count_text = (f"Showing the first {EXPORT_CAP:,} of {total:,} audits (export limit). "
                      "Narrow the filters to export the rest.")
    log(request, ActionType.EXPORT, f"Exported audits summary ({fmt.upper()})", detail=filter_text, client=client)
    name = f"audix-audits-{file_slug}-{ex.stamp()}.{fmt}"
    args = (letter_name, audits, summary, filter_text, count_text, include_client)
    if fmt == "xlsx":
        return _response(audits_xlsx(*args), XLSX, name)
    return _response(audits_pdf(*args), "application/pdf", name)


def filters_text(filters) -> str:
    return " · ".join(f"{k}: {v}" for k, v in filters)


AUDIT_COLS = ["Date", "Reference", "Store ID", "Store", "Audit type", "Shift", "Stock value", "Total physical",
              "Difference", "Var % of stock value", "Var % of sale value", "Status"]


def _status(a):
    return a.status_label if a.is_published else a.get_status_display()


def audits_xlsx(letter_name, audits, summary, filter_text, count_text, include_client=False) -> bytes:
    wb, ws = ex.new_workbook()
    ws.title = "Audits"
    sh = ex.Sheet(ws, letter_name, "Audits summary", filter_text, period_label="Filters")
    sh.add(["Filters", filter_text], bold=True)
    sh.add(["Audits", count_text])
    sh.add(["Totals", "Stock value", summary["stock_value"], "Shortage", summary["shortage"],
            "Excess", summary["excess"], "Net", summary["diff_value"]],
           [None, None, "inr", None, "inr", None, "inr", None, "inr"], bold=True)
    sh.row += 1
    cols, widths = list(AUDIT_COLS), [13, 15, 9, 26, 20, 11, 15, 15, 15, 12, 12, 10]
    if include_client:
        cols.insert(2, "Client")
        widths.insert(2, 20)
    sh.header(cols, widths)
    for a in audits:
        row = [a.audit_date, a.reference, a.store.code, f"{a.store.name}, {a.store.city}", a.get_audit_type_display(),
               a.get_shift_display(), a.stock_value, a.total_value, a.diff_value, a.var_pct_stock,
               a.var_pct_sale, _status(a)]
        kinds = ["date", None, None, None, None, None, "inr", "inr", "inr", "pct",
                 "pct" if a.var_pct_sale is not None else None, None]
        if include_client:
            row.insert(2, a.client.name)
            kinds.insert(2, None)
        sh.add(row, kinds)
    sh.footer()
    return ex.workbook_bytes(wb)


def audits_pdf(letter_name, audits, summary, filter_text, count_text, include_client=False) -> bytes:
    from reportlab.lib.units import mm

    def story():
        s = [ex.paragraph("Audits summary", "h1"),
             ex.paragraph(f"Filters: {filter_text}", "bold"),
             ex.paragraph(count_text, "body"),
             ex.paragraph(f"Stock value {inr(summary['stock_value'])} · Shortage "
                          f"{inr(summary['shortage'])} · Excess {inr(summary['excess'])} · Net {inr(summary['diff_value'])} "
                          f"({pct(summary['var_pct'])} of stock value)", "body")]
        head = ["Date", "Reference", "Store", "Audit type", "Shift", "Stock value", "Total physical", "Difference",
                "Var %", "Status"]
        widths = [22, 26, 42, 30, 18, 27, 27, 25, 32, 18]
        if include_client:
            head.insert(2, "Client")
            widths = [20, 25, 30, 36, 26, 16, 25, 25, 23, 30, 18]
        rows = [head]
        for a in audits:
            v = f"{pct(a.var_pct_stock)} of stock" + (f"\n{pct(a.var_pct_sale)} of sale" if a.var_pct_sale is not None else "")
            row = [fmt_date(a.audit_date), a.reference, f"{a.store.code} {a.store.name}", a.get_audit_type_display(),
                   a.get_shift_display(), inr(a.stock_value), inr(a.total_value), inr(a.diff_value), v, _status(a)]
            if include_client:
                row.insert(2, a.client.name)
            rows.append(row)
        off = 1 if include_client else 0
        s.append(ex.pdf_table(rows, col_widths=[w * mm for w in widths],
                              num_cols=tuple(c + off for c in (5, 6, 7, 8))))
        return s

    return ex.build_pdf(story, letter_name, "Audits summary", filter_text)


# ---------------------------------------------------------------- comparison


@portal_required
def compare_pdf(request):
    client, scope, _ = resolve(request)
    qs = Audit.objects.for_scope(scope).select_related("store")
    try:
        a = get_object_or_404(qs, pk=uuid.UUID(request.GET.get("a", "")))
        b = get_object_or_404(qs, pk=uuid.UUID(request.GET.get("b", "")))
    except ValueError:
        raise Http404
    cmp = services.comparison_for(a, b)
    period_text = f"{a.reference} ({fmt_date(a.audit_date)}) vs {b.reference} ({fmt_date(b.audit_date)})"
    log(request, ActionType.EXPORT, "Exported comparison (PDF)", detail=period_text, audit=b, client=client)
    data = comparison_pdf(client, a, b, cmp, period_text)
    return _response(data, "application/pdf", f"audix-comparison-{a.reference}-{b.reference}.pdf")


def comparison_pdf(client, a, b, cmp, period_text) -> bytes:
    from reportlab.lib.units import mm

    def story():
        s = [ex.paragraph("Audit comparison", "h1"),
             ex.paragraph(f"A: {a.store.name}, {a.store.city} · {fmt_date(a.audit_date)} · {a.get_audit_type_display()} · {a.reference}", "body"),
             ex.paragraph(f"B: {b.store.name}, {b.store.city} · {fmt_date(b.audit_date)} · {b.get_audit_type_display()} · {b.reference}", "body"),
             ex.paragraph("Summary", "h2")]
        for i, sentence in enumerate(cmp.sentences):
            s.append(ex.paragraph(sentence, "bold" if i == 0 else "body"))
        s.append(ex.paragraph("Overall", "h2"))
        rows = [["Measure", "A", "B", "Change"]] + [[r.label, r.a, r.b, r.change.text] for r in cmp.overall]
        s.append(ex.pdf_table(rows, col_widths=[70 * mm, 55 * mm, 55 * mm, 55 * mm], num_cols=(1, 2, 3)))
        if cmp.table_rows:
            s.append(ex.paragraph("Category by category", "h2"))
            rows = [["Category", "A stock", "B stock", "A difference", "A var %", "B difference", "B var %",
                     "Change in value", "Change in units", "B damage", "B WBC", "Verdict"]]
            dash = "—"
            for r in cmp.table_rows + [cmp.category_total] + ([cmp.common_total] if cmp.one_sided_count else []):
                na, nb = r.a, r.b
                rows.append([
                    r.category,
                    f"{qty(na.stock_qty)} / {inr(na.stock_value)}" if na else dash,
                    f"{qty(nb.stock_qty)} / {inr(nb.stock_value)}" if nb else dash,
                    f"{qty(na.diff_qty, signed=True)} / {inr(na.diff_value, signed=True)}" if na else dash,
                    pct(na.var_pct) if na else dash,
                    f"{qty(nb.diff_qty, signed=True)} / {inr(nb.diff_value, signed=True)}" if nb else dash,
                    pct(nb.var_pct) if nb else dash,
                    r.change_value.text if r.change_value else dash,
                    r.change_units.text if r.change_units else dash,
                    f"{qty(nb.damage_qty)} / {inr(nb.damage_value)} ({pct(nb.damage_pct)})" if nb else dash,
                    f"{qty(nb.wbc_qty)} / {inr(nb.wbc_value)} ({pct(nb.wbc_pct)})" if nb else dash,
                    r.verdict,
                ])
            extra = 2 if cmp.one_sided_count else 1
            s.append(ex.pdf_table(rows, num_cols=tuple(range(1, 11)), footer=extra))
            s.append(ex.paragraph("Quantity / value, amounts in rupees.", "small"))
            if cmp.one_sided_count:
                n = cmp.one_sided_count
                s.append(ex.paragraph(f"{n} {'category is' if n == 1 else 'categories are'} not in both audits. Totals "
                                      "show every category of each audit; changes compare common categories only.",
                                      "small"))
        if cmp.followups:
            s.append(ex.paragraph("Follow-up on A's recommendations", "h2"))
            rows = [["Category", "Recommendation", "Status"]] + [
                [f.observation.category_label, f.observation.recommendation, f.status] for f in cmp.followups]
            s.append(ex.pdf_table(rows, col_widths=[45 * mm, 170 * mm, 30 * mm]))
        s.append(ex.paragraph("Improvement points for B", "h2"))
        for i, p in enumerate(cmp.improvements or ["Nothing to add. Keep the current routine."], start=1):
            s.append(ex.paragraph(f"{i}. {p}", "body"))
        return s

    return ex.build_pdf(story, client.name, "Audit comparison", period_text)

