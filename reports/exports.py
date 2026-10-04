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
from core.formatting import fmt_date, inr, pct
from core.permissions import portal_required
from reports.aging import build_aging
from reports.queries import window_totals
from reports.views import PERIODS, aging_inputs, filtered_audits, resolve

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


@portal_required
def audits_export(request, fmt):
    if fmt not in ("xlsx", "pdf"):
        raise Http404
    client, scope, _ = resolve(request)
    qs, f = filtered_audits(request, scope)
    audits = list(qs[:5000])
    summary = window_totals(qs)
    period_text = dict(PERIODS).get(f["period"], "Any time") + (f" · search “{f['q']}”" if f["q"] else "")
    log(request, ActionType.EXPORT, f"Exported audits summary ({fmt.upper()})", detail=period_text, client=client)
    name = f"audix-audits-{_slug(client)}-{ex.stamp()}.{fmt}"
    if fmt == "xlsx":
        return _response(audits_xlsx(client, audits, summary, period_text), XLSX, name)
    return _response(audits_pdf(client, audits, summary, period_text), "application/pdf", name)


AUDIT_COLS = ["Date", "Reference", "Store ID", "Store", "Audit type", "Shift", "Stock value", "Total physical",
              "Difference", "Var % of stock value", "Var % of sale value", "Status"]


def audits_xlsx(client, audits, summary, period_text) -> bytes:
    wb, ws = ex.new_workbook()
    ws.title = "Audits"
    sh = ex.Sheet(ws, client.name, "Audits summary", period_text)
    sh.add([f"{summary['audits']} audits", "Stock value", summary["stock_value"], "Shortage", summary["shortage"],
            "Excess", summary["excess"], "Net", summary["diff_value"]],
           [None, None, "inr", None, "inr", None, "inr", None, "inr"], bold=True)
    sh.row += 1
    sh.header(AUDIT_COLS, [13, 15, 9, 26, 20, 11, 15, 15, 15, 12, 12, 10])
    for a in audits:
        sh.add([a.audit_date, a.reference, a.store.code, f"{a.store.name}, {a.store.city}", a.get_audit_type_display(),
                a.get_shift_display(), a.stock_value, a.total_value, a.diff_value, a.var_pct_stock,
                a.var_pct_sale, a.status_label],
               ["date", None, None, None, None, None, "inr", "inr", "inr", "pct", "pct" if a.var_pct_sale is not None else None, None])
    sh.footer()
    return ex.workbook_bytes(wb)


def audits_pdf(client, audits, summary, period_text) -> bytes:
    from reportlab.lib.units import mm

    def story():
        s = [ex.paragraph("Audits summary", "h1"),
             ex.paragraph(f"{summary['audits']} audits · Stock value {inr(summary['stock_value'])} · Shortage "
                          f"{inr(summary['shortage'])} · Excess {inr(summary['excess'])} · Net {inr(summary['diff_value'])} "
                          f"({pct(summary['var_pct'])} of stock value)", "body")]
        rows = [["Date", "Reference", "Store", "Audit type", "Shift", "Stock value", "Total physical", "Difference",
                 "Var %", "Status"]]
        for a in audits:
            v = f"{pct(a.var_pct_stock)} of stock" + (f"\n{pct(a.var_pct_sale)} of sale" if a.var_pct_sale is not None else "")
            rows.append([fmt_date(a.audit_date), a.reference, f"{a.store.code} {a.store.name}", a.get_audit_type_display(),
                         a.get_shift_display(), inr(a.stock_value), inr(a.total_value), inr(a.diff_value), v,
                         a.status_label])
        s.append(ex.pdf_table(rows, col_widths=[22 * mm, 26 * mm, 42 * mm, 30 * mm, 18 * mm, 27 * mm, 27 * mm,
                                                25 * mm, 32 * mm, 18 * mm], num_cols=(5, 6, 7, 8)))
        return s

    return ex.build_pdf(story, client.name, "Audits summary", period_text)


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
        if cmp.categories:
            s.append(ex.paragraph("Category by category", "h2"))
            rows = [["Category", "A stock", "B stock", "A difference", "A var %", "B difference", "B var %",
                     "Change in value", "B damage", "B WBC", "Verdict"]]
            for r in cmp.categories:
                rows.append([r.category, inr(r.a.stock_value), inr(r.b.stock_value), inr(r.a.diff_value), pct(r.a.var_pct),
                             inr(r.b.diff_value), pct(r.b.var_pct), r.change_value.text,
                             f"{inr(r.b.damage_value)} ({pct(r.b.damage_pct)})", f"{inr(r.b.wbc_value)} ({pct(r.b.wbc_pct)})",
                             r.verdict])
            s.append(ex.pdf_table(rows, num_cols=tuple(range(1, 10))))
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

