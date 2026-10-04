from django.urls import path

from django.http import HttpResponse

def todo(request, *a, **k):
    return HttpResponse("todo")

app_name = "audits"
urlpatterns = [
    path("console/audits/", todo, name="list"),
    path("console/audits/new/", todo, name="new"),
]
