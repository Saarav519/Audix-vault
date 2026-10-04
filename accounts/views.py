from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.forms import ChangePasswordForm, LoginForm
from activity.log import log
from activity.models import ActionType


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


def logout_view(request):
    from django.contrib.auth import logout

    if request.method == "POST":
        logout(request)
        messages.info(request, "You have signed out.")
    return redirect("accounts:login")


@login_required
def password_change(request):
    forced = request.user.must_change_password
    form = ChangePasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password", "updated_at"])
        update_session_auth_hash(request, user)
        log(request, ActionType.ADMIN_CHANGE, "Changed own password")
        messages.success(request, "Your password has been changed.")
        return redirect("home")
    return render(request, "accounts/password_change.html", {"form": form, "forced": forced})
