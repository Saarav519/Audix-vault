from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from activity.log import log
from activity.models import ActionType
from audits import importer
from core.permissions import admin_required

MAX_IMPORT_BYTES = 20 * 1024 * 1024


@admin_required
def import_view(request):
    result = None
    dry_run = True
    if request.method == "POST":
        f = request.FILES.get("file")
        dry_run = request.POST.get("dry_run") == "1"
        if f is None or not f.name.lower().endswith((".xlsx", ".csv")):
            messages.error(request, "Choose an .xlsx or .csv file made from the template.")
            return redirect("console:import")
        if f.size > MAX_IMPORT_BYTES:
            messages.error(request, "The file is larger than 20 MB. Split it into smaller files.")
            return redirect("console:import")
        try:
            result = importer.parse(f.name, f.read())
        except Exception:
            messages.error(request, "The file could not be read. Use the template and save it as .xlsx or .csv.")
            return redirect("console:import")
        if not dry_run and result.ok:
            n = importer.run_import(result, request.user)
            log(request, ActionType.ADMIN_CHANGE, "Imported old audits", detail=f"{f.name}: {n} audits")
            messages.success(request, f"Imported {n} audit{'s' if n != 1 else ''}. They are published and marked as imported.")
            return redirect("console:import")
        if not dry_run and not result.ok:
            messages.error(request, "Nothing was imported because the file has errors. Fix them and try again.")
    return render(request, "console/import.html", {"result": result, "dry_run": dry_run})


@admin_required
def import_template(request):
    resp = HttpResponse(importer.template_bytes(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="audix-import-template.xlsx"'
    return resp
