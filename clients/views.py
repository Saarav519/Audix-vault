"""Admin console: clients, stores, categories, logins, settings, view-as-client."""

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role, User
from activity.log import log
from activity.models import ActionType
from clients import forms
from clients.models import Category, Client, Store
from core import storage
from core.models import AppSettings
from core.permissions import admin_required
from core.scoping import VIEW_AS_SESSION_KEY


def _save_logo(client, f):
    backend = storage.get_backend()
    if backend is None:
        return False
    key = f"clients/{client.pk}/logo/{storage.safe_filename(f.name)}"
    ext = storage.extension(f.name)
    backend.put(key, f.read(), forms.LOGO_TYPES.get(ext, "application/octet-stream"))
    client.logo_key = key
    client.save(update_fields=["logo_key", "updated_at"])
    return True


def _recompute(client=None):
    from audits.services import recompute_snapshots

    recompute_snapshots(client)


@admin_required
def client_list(request):
    q = (request.GET.get("q") or "").strip()
    clients = Client.objects.annotate(
        store_count=Count("stores", filter=Q(stores__is_active=True), distinct=True),
        login_count=Count("users", distinct=True),
        last_audit=Max("audits__audit_date", filter=Q(audits__status="published")),
    ).order_by("name")
    if q:
        clients = clients.filter(name__icontains=q)
    return render(request, "console/client_list.html", {"clients": clients, "q": q})


@admin_required
def client_create(request):
    form = forms.ClientCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            client = Client.objects.create(
                name=d["name"], slug=form.unique_slug(), contact_emails=d["contact_emails"],
                notify_on_publish=d["notify_on_publish"], created_by=request.user,
            )
            for code, name, city in d["stores"]:
                Store.objects.create(client=client, code=code, name=name, city=city)
            for i, name in enumerate(d["categories"], start=1):
                Category.objects.create(client=client, name=name, sort_order=i * 10)
            user = User.objects.create_user(
                d["login_id"], d["password"], name=d["name"], email=(client.email_list() or [""])[0],
                role=Role.CLIENT, client=client, must_change_password=d["must_change_password"],
            )
        logo_note = ""
        if d.get("logo"):
            if not _save_logo(client, d["logo"]):
                logo_note = " The logo was not saved because file storage is not configured yet."
        log(request, ActionType.ADMIN_CHANGE, "Created client", detail=f"{client.name}; login {user.login_id}",
            client=client)
        messages.success(request, f"{client.name} has been created with login ID {user.login_id}.{logo_note}")
        return redirect("console:client_detail", client.pk)
    return render(request, "console/client_form.html", {"form": form, "creating": True})


@admin_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    stores = client.stores.annotate(
        audit_count=Count("audits", filter=Q(audits__status="published")),
        last_audit=Max("audits__audit_date", filter=Q(audits__status="published")),
    )
    ctx = {
        "client": client,
        "stores": stores,
        "categories": client.categories.all(),
        "logins": client.users.order_by("login_id"),
        "store_form": forms.StoreForm(client=client),
        "category_form": forms.CategoryForm(client=client, initial={"sort_order": (client.categories.count() + 1) * 10}),
        "login_form": forms.ClientLoginForm(client=client),
        "thresholds": client.thresholds(),
        "storage_ready": storage.is_configured(),
    }
    return render(request, "console/client_detail.html", ctx)


@admin_required
def client_edit(request, pk):
    client = get_object_or_404(Client, pk=pk)
    before = (client.good_pct, client.warn_pct)
    form = forms.ClientEditForm(request.POST or None, request.FILES or None, instance=client)
    if request.method == "POST" and form.is_valid():
        form.save()
        if form.cleaned_data.get("logo"):
            if not _save_logo(client, form.cleaned_data["logo"]):
                messages.warning(request, "The logo was not saved because file storage is not configured yet.")
        if before != (client.good_pct, client.warn_pct):
            _recompute(client)
        log(request, ActionType.ADMIN_CHANGE, "Edited client", detail=", ".join(form.changed_data), client=client)
        messages.success(request, "Client details saved.")
        return redirect("console:client_detail", client.pk)
    return render(request, "console/client_form.html", {"form": form, "client": client, "creating": False})


@admin_required
@require_POST
def client_toggle(request, pk):
    client = get_object_or_404(Client, pk=pk)
    client.is_active = not client.is_active
    client.save(update_fields=["is_active", "updated_at"])
    word = "Enabled" if client.is_active else "Disabled"
    log(request, ActionType.ADMIN_CHANGE, f"{word} client", detail=client.name, client=client)
    messages.success(request, f"{client.name} is now {'enabled' if client.is_active else 'disabled'}. "
                              f"{'' if client.is_active else 'Its users cannot sign in; data is kept.'}")
    return redirect("console:client_detail", client.pk)


@admin_required
@require_POST
def store_add(request, pk):
    client = get_object_or_404(Client, pk=pk)
    form = forms.StoreForm(request.POST, client=client)
    if form.is_valid():
        store = form.save(commit=False)
        store.client = client
        store.save()
        log(request, ActionType.ADMIN_CHANGE, "Added store", detail=str(store), client=client)
        messages.success(request, f"Store {store.code} added.")
    else:
        messages.error(request, "Store not added: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("console:client_detail", client.pk)


@admin_required
def store_edit(request, pk):
    store = get_object_or_404(Store, pk=pk)
    form = forms.StoreForm(request.POST or None, instance=store, client=store.client)
    if request.method == "POST" and form.is_valid():
        form.save()
        log(request, ActionType.ADMIN_CHANGE, "Edited store", detail=str(store), client=store.client)
        messages.success(request, "Store saved.")
        return redirect("console:client_detail", store.client_id)
    return render(request, "console/simple_form.html", {"form": form, "title": f"Edit store {store.code}",
                                                         "back": store.client})


@admin_required
@require_POST
def store_toggle(request, pk):
    store = get_object_or_404(Store, pk=pk)
    store.is_active = not store.is_active
    store.save(update_fields=["is_active", "updated_at"])
    log(request, ActionType.ADMIN_CHANGE, "Activated store" if store.is_active else "Deactivated store",
        detail=str(store), client=store.client)
    return redirect("console:client_detail", store.client_id)


@admin_required
@require_POST
def category_add(request, pk):
    client = get_object_or_404(Client, pk=pk)
    form = forms.CategoryForm(request.POST, client=client)
    if form.is_valid():
        cat = form.save(commit=False)
        cat.client = client
        cat.save()
        log(request, ActionType.ADMIN_CHANGE, "Added category", detail=cat.name, client=client)
        messages.success(request, f"Category {cat.name} added.")
    else:
        messages.error(request, "Category not added: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("console:client_detail", client.pk)


@admin_required
def category_edit(request, pk):
    cat = get_object_or_404(Category, pk=pk)
    form = forms.CategoryForm(request.POST or None, instance=cat, client=cat.client)
    if request.method == "POST" and form.is_valid():
        form.save()
        log(request, ActionType.ADMIN_CHANGE, "Edited category", detail=cat.name, client=cat.client)
        messages.success(request, "Category saved.")
        return redirect("console:client_detail", cat.client_id)
    return render(request, "console/simple_form.html", {"form": form, "title": f"Edit category {cat.name}",
                                                         "back": cat.client})


@admin_required
@require_POST
def category_toggle(request, pk):
    cat = get_object_or_404(Category, pk=pk)
    cat.is_active = not cat.is_active
    cat.save(update_fields=["is_active", "updated_at"])
    log(request, ActionType.ADMIN_CHANGE, "Activated category" if cat.is_active else "Deactivated category",
        detail=cat.name, client=cat.client)
    return redirect("console:client_detail", cat.client_id)


@admin_required
@require_POST
def login_add(request, pk):
    client = get_object_or_404(Client, pk=pk)
    form = forms.ClientLoginForm(request.POST, client=client)
    if form.is_valid():
        d = form.cleaned_data
        user = User.objects.create_user(d["login_id"], d["password"], name=d["name"], email=d["email"],
                                        role=d["role"], client=client, must_change_password=True)
        if d["role"] == Role.CLIENT_STORE:
            user.allowed_stores.set(d["stores"])
        log(request, ActionType.ADMIN_CHANGE, "Added client login", detail=user.login_id, client=client)
        messages.success(request, f"Login {user.login_id} created. They must change the password at first login.")
    else:
        messages.error(request, "Login not added: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("console:client_detail", client.pk)


@admin_required
@require_POST
def user_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "Use “Change password” to change your own password.")
        return redirect("home")
    temp = forms.generate_password()
    user.set_password(temp)
    user.must_change_password = True
    user.save()
    log(request, ActionType.ADMIN_CHANGE, "Reset password", detail=f"Login {user.login_id}", client=user.client)
    # Shown once, in this response only. Never stored, logged or emailed.
    resp = render(request, "console/password_shown.html", {"target": user, "temp_password": temp})
    resp["Cache-Control"] = "no-store"
    return resp


@admin_required
@require_POST
def user_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot disable your own login.")
        return redirect("home")
    user.is_active = not user.is_active
    user.save(update_fields=["is_active", "updated_at"])
    log(request, ActionType.ADMIN_CHANGE, "Enabled login" if user.is_active else "Disabled login",
        detail=user.login_id, client=user.client)
    if user.client_id:
        return redirect("console:client_detail", user.client_id)
    return redirect("console:settings")


@admin_required
@require_POST
def view_as_start(request, pk):
    client = get_object_or_404(Client, pk=pk)
    request.session[VIEW_AS_SESSION_KEY] = str(client.pk)
    log(request, ActionType.ADMIN_CHANGE, "Started view as client", detail=client.name, client=client)
    return redirect("portal:dashboard")


@require_POST
def view_as_stop(request):
    if not request.user.is_authenticated or not request.user.is_staff_member:
        raise Http404
    cid = request.session.pop(VIEW_AS_SESSION_KEY, None)
    if cid:
        log(request, ActionType.ADMIN_CHANGE, "Stopped view as client", client=Client.objects.filter(pk=cid).first())
    return redirect("console:overview")


@admin_required
def settings_view(request):
    app = AppSettings.load()
    app = AppSettings.objects.get(pk=app.pk)
    before = (app.good_pct, app.warn_pct)
    form = forms.AppSettingsForm(request.POST or None, instance=app)
    staff_form = forms.StaffUserForm()
    if request.method == "POST" and form.is_valid():
        form.save()
        if before != (app.good_pct, app.warn_pct):
            _recompute(None)
        log(request, ActionType.ADMIN_CHANGE, "Changed settings", detail=", ".join(form.changed_data))
        messages.success(request, "Settings saved.")
        return redirect("console:settings")
    staff = User.objects.filter(role__in=[Role.ADMIN, Role.AUDITOR]).order_by("role", "login_id")
    return render(request, "console/settings.html", {
        "form": form, "staff": staff, "staff_form": staff_form, "app": app,
        "storage_backend": getattr(storage.get_backend(), "name", None),
    })


@admin_required
@require_POST
def staff_add(request):
    form = forms.StaffUserForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        user = User.objects.create_user(d["login_id"], d["password"], name=d["name"], email=d["email"],
                                        role=d["role"], must_change_password=True)
        log(request, ActionType.ADMIN_CHANGE, "Added team login", detail=f"{user.login_id} ({user.get_role_display()})")
        messages.success(request, f"{user.get_role_display()} login {user.login_id} created.")
    else:
        messages.error(request, "Login not added: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("console:settings")
