from django.urls import path

from activity import views as activity_views
from audits import import_views
from clients import views
from reports import console_views

app_name = "console"
urlpatterns = [
    path("overview/", console_views.overview, name="overview"),
    path("clients/", views.client_list, name="clients"),
    path("clients/new/", views.client_create, name="client_create"),
    path("clients/<uuid:pk>/", views.client_detail, name="client_detail"),
    path("clients/<uuid:pk>/edit/", views.client_edit, name="client_edit"),
    path("clients/<uuid:pk>/toggle/", views.client_toggle, name="client_toggle"),
    path("clients/<uuid:pk>/stores/add/", views.store_add, name="store_add"),
    path("clients/<uuid:pk>/categories/add/", views.category_add, name="category_add"),
    path("clients/<uuid:pk>/logins/add/", views.login_add, name="login_add"),
    path("clients/<uuid:pk>/view-as/", views.view_as_start, name="view_as"),
    path("view-as/stop/", views.view_as_stop, name="view_as_stop"),
    path("stores/<uuid:pk>/edit/", views.store_edit, name="store_edit"),
    path("stores/<uuid:pk>/toggle/", views.store_toggle, name="store_toggle"),
    path("categories/<uuid:pk>/edit/", views.category_edit, name="category_edit"),
    path("categories/<uuid:pk>/toggle/", views.category_toggle, name="category_toggle"),
    path("users/<uuid:pk>/reset-password/", views.user_reset_password, name="user_reset"),
    path("users/<uuid:pk>/toggle/", views.user_toggle, name="user_toggle"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/team/add/", views.staff_add, name="staff_add"),
    path("activity/", activity_views.activity_list, name="activity"),
    path("import/", import_views.import_view, name="import"),
    path("import/template/", import_views.import_template, name="import_template"),
]
