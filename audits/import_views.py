from django.http import HttpResponse

from core.permissions import admin_required


@admin_required
def import_view(request):
    return HttpResponse("todo")


@admin_required
def import_template(request):
    return HttpResponse("todo")
