from django.urls import path

from audits import detail_views, views

app_name = "audits"
urlpatterns = [
    path("console/audits/", views.audit_list, name="list"),
    path("console/audits/new/", views.audit_new, name="new"),
    path("console/audits/preview/", views.audit_preview, name="preview"),
    path("console/audits/suggest/", views.suggest_drafts, name="suggest"),
    path("console/audits/<uuid:pk>/edit/", views.audit_edit, name="edit"),
    path("console/audits/<uuid:pk>/publish/", views.audit_publish, name="publish"),
    path("console/audits/<uuid:pk>/delete/", views.audit_delete, name="delete"),
    path("console/audits/<uuid:pk>/files/presign/", detail_views.upload_presign, name="presign"),
    path("console/audits/<uuid:pk>/files/confirm/", detail_views.upload_confirm, name="confirm"),
    path("console/audits/<uuid:pk>/files/<uuid:file_id>/delete/", detail_views.file_delete, name="file_delete"),
    path("audits/<uuid:pk>/", detail_views.audit_detail, name="detail"),
    path("audits/<uuid:pk>/files/<uuid:file_id>/download/", detail_views.file_download, name="download"),
    path("audits/<uuid:pk>/files/<uuid:file_id>/preview/", detail_views.file_preview, name="preview_file"),
    path("audits/<uuid:pk>/files/<uuid:file_id>/thumb/", detail_views.file_thumb, name="thumb"),
    path("audits/<uuid:pk>/zip/", detail_views.audit_zip, name="zip"),
    path("audits/<uuid:pk>/signoff-sheet/", detail_views.signoff_sheet, name="signoff_sheet"),
]
