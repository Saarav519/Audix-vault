from django.urls import path

from reports import exports, views

app_name = "portal"
urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("audits/", views.audits, name="audits"),
    path("audits/export/<str:fmt>/", exports.audits_export, name="audits_export"),
    path("tracker/", views.tracker, name="tracker"),
    path("compare/", views.compare, name="compare"),
    path("compare/export/pdf/", exports.compare_pdf, name="compare_pdf"),
    path("aging/", views.aging, name="aging"),
    path("aging/export/<str:fmt>/", exports.aging_export, name="aging_export"),
]
