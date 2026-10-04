from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("healthz", core_views.healthz, name="healthz"),
    path("", core_views.home, name="home"),
    path("accounts/", include("accounts.urls")),
    path("console/", include("clients.urls")),
    path("", include("audits.urls")),
    path("portal/", include("reports.urls")),
    path("files/", include("core.urls")),
]

handler404 = "core.views.not_found"
handler403 = "core.views.forbidden"
