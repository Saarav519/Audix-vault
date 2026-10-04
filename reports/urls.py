from django.urls import path

from django.http import HttpResponse

def todo(request, *a, **k):
    return HttpResponse("todo")

app_name = "portal"
urlpatterns = [
    path("dashboard/", todo, name="dashboard"),
    path("audits/", todo, name="audits"),
    path("tracker/", todo, name="tracker"),
    path("aging/", todo, name="aging"),
    path("compare/", todo, name="compare"),
]
