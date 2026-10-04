from django.http import HttpResponse

from core.permissions import admin_required


@admin_required
def activity_list(request):
    return HttpResponse("todo")
