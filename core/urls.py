from django.urls import path

from core import file_views

app_name = "files"
urlpatterns = [
    path("local/upload/<str:token>/", file_views.local_upload, name="local_upload"),
    path("local/<str:token>/", file_views.local_serve, name="local_serve"),
    path("logo/<uuid:client_id>/", file_views.client_logo, name="client_logo"),
]
