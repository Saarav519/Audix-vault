from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404


def staff_required(view):
    """Admin or auditor. Clients get 404 so admin URLs are not discoverable."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not user.is_staff_member:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not user.is_staff_member:
            raise Http404
        if not user.is_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def portal_required(view):
    """Client portal pages: a client user, or an admin viewing as a client, or staff with a client picker."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return wrapped
