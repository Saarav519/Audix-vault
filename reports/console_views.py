from django.shortcuts import render

from core.permissions import staff_required


@staff_required
def overview(request):
    return render(request, "console/overview.html", {})
