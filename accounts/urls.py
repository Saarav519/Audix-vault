from django.urls import path

from accounts import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/", views.password_change, name="password_change"),
]
