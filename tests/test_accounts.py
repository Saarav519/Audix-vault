"""Stage 0 and 2: health check, login page, bootstrap, security (acceptance 14 and 17)."""

import pytest
from django.urls import reverse

from accounts.models import Role, User
from activity.models import ActivityLog
from clients.models import Client

pytestmark = pytest.mark.django_db


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_login_page_renders_brand(client):
    r = client.get(reverse("accounts:login"))
    assert r.status_code == 200
    html = r.content.decode()
    assert "Exceptionalism" in html
    assert "Vikas Kshitij &amp; Associates" in html
    assert "Content-Security-Policy" in r.headers


def test_home_redirects_anonymous_to_login(client):
    r = client.get("/")
    assert r.status_code == 302 and reverse("accounts:login") in r["Location"]


def test_bootstrap_admin(monkeypatch):
    from django.core.management import call_command

    monkeypatch.setenv("ADMIN_LOGIN_ID", "Owner")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "Very-strong-pass-1")
    monkeypatch.delenv("ADMIN_FORCE_PASSWORD_CHANGE", raising=False)
    call_command("bootstrap_admin")
    u = User.objects.get()
    assert u.login_id == "owner" and u.role == Role.ADMIN and u.must_change_password
    call_command("bootstrap_admin")  # idempotent
    assert User.objects.count() == 1


def test_bootstrap_admin_skips_without_env(monkeypatch):
    from django.core.management import call_command

    monkeypatch.delenv("ADMIN_LOGIN_ID", raising=False)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)
    call_command("bootstrap_admin")
    assert not User.objects.exists()


def login(client, login_id, pw):
    return client.post(reverse("accounts:login"), {"username": login_id, "password": pw},
                       REMOTE_ADDR="10.0.0.1")


# ---------------------------------------------------------------- 17


def test_login_case_insensitive_and_logged(client, user_a, password):
    r = login(client, "ALPHA", password)
    assert r.status_code == 302
    assert ActivityLog.objects.filter(action_type="login", user=user_a).exists()


def test_disabled_client_cannot_log_in(client, user_a, password):
    Client.objects.filter(pk=user_a.client_id).update(is_active=False)
    r = login(client, "alpha", password)
    assert r.status_code == 200
    assert "_auth_user_id" not in client.session


def test_disabled_client_is_signed_out(client, user_a):
    client.force_login(user_a)
    Client.objects.filter(pk=user_a.client_id).update(is_active=False)
    r = client.get(reverse("portal:dashboard"))
    assert r.status_code == 302
    assert "_auth_user_id" not in client.session


def test_lockout_after_five_failures(client, user_a, password):
    for _ in range(5):
        login(client, "alpha", "wrong-password")
    r = login(client, "alpha", password)
    assert r.status_code in (403, 429)
    assert "_auth_user_id" not in client.session
    assert ActivityLog.objects.filter(action_type="login_failed").count() >= 5


def test_first_login_password_change_enforced(client, client_a, password):
    User.objects.create_user("newbie", password, role=Role.CLIENT, client=client_a, must_change_password=True)
    login(client, "newbie", password)
    r = client.get(reverse("portal:dashboard"))
    assert r.status_code == 302 and r["Location"] == reverse("accounts:password_change")
    r = client.post(reverse("accounts:password_change"), {
        "old_password": password, "new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
    assert r.status_code == 302
    assert not User.objects.get(login_id="newbie").must_change_password
    assert client.get(reverse("portal:dashboard")).status_code == 200


def test_short_password_rejected(client, client_a, password):
    User.objects.create_user("newbie", password, role=Role.CLIENT, client=client_a, must_change_password=True)
    client.force_login(User.objects.get(login_id="newbie"))
    r = client.post(reverse("accounts:password_change"), {
        "old_password": password, "new_password1": "short1", "new_password2": "short1"})
    assert r.status_code == 200


# ---------------------------------------------------------------- 14


def admin_urls(client_obj):
    store = client_obj.stores.first()
    cat = client_obj.categories.first()
    return [
        ("get", reverse("console:overview")),
        ("get", reverse("console:clients")),
        ("get", reverse("console:client_create")),
        ("get", reverse("console:client_detail", args=[client_obj.pk])),
        ("get", reverse("console:client_edit", args=[client_obj.pk])),
        ("post", reverse("console:client_toggle", args=[client_obj.pk])),
        ("post", reverse("console:store_add", args=[client_obj.pk])),
        ("get", reverse("console:store_edit", args=[store.pk])),
        ("post", reverse("console:category_add", args=[client_obj.pk])),
        ("get", reverse("console:category_edit", args=[cat.pk])),
        ("post", reverse("console:login_add", args=[client_obj.pk])),
        ("post", reverse("console:view_as", args=[client_obj.pk])),
        ("get", reverse("console:settings")),
        ("post", reverse("console:staff_add")),
        ("get", reverse("console:activity")),
        ("get", reverse("console:import")),
        ("get", reverse("audits:new")),
        ("get", reverse("audits:list")),
    ]


def test_client_users_get_404_on_admin_urls(client, user_a, client_a, admin):
    client.force_login(user_a)
    for method, url in admin_urls(client_a):
        r = getattr(client, method)(url)
        assert r.status_code in (403, 404), url
    r = client.post(reverse("console:user_reset", args=[admin.pk]))
    assert r.status_code in (403, 404)


ADMIN_ONLY = {"clients", "client_create", "client_detail", "client_edit", "client_toggle", "store_add", "store_edit",
              "category_add", "category_edit", "login_add", "view_as", "settings", "staff_add", "activity", "import"}


def test_auditors_cannot_reach_admin_only_pages(auditor_client, client_a, user_a):
    for method, url in admin_urls(client_a):
        name = next((n for n in ADMIN_ONLY if url == _safe_reverse(n, client_a, url)), None)
        r = getattr(auditor_client, method)(url)
        if name:
            assert r.status_code in (403, 404), url
    r = auditor_client.post(reverse("console:user_reset", args=[user_a.pk]))
    assert r.status_code == 403
    assert auditor_client.get(reverse("console:overview")).status_code == 200


def _safe_reverse(name, client_obj, url):
    for args in ([], [client_obj.pk], [client_obj.stores.first().pk], [client_obj.categories.first().pk]):
        try:
            u = reverse(f"console:{name}", args=args)
        except Exception:
            continue
        if u == url:
            return u
    return None


# ---------------------------------------------------------------- client creation through the UI


def test_admin_creates_client(admin_client):
    r = admin_client.post(reverse("console:client_create"), {
        "name": "Fresh Mart Pvt Ltd", "contact_emails": "a@fresh.example, b@fresh.example",
        "login_id": "FreshMart", "password": "Temp-password-123", "must_change_password": "on",
        "notify_on_publish": "on", "stores": "FM01, Baner, Pune\nFM02, Wakad, Pune",
        "categories": "Grocery & staples\nBeverages",
    })
    assert r.status_code == 302, r.content.decode()[:2000]
    c = Client.objects.get(name="Fresh Mart Pvt Ltd")
    assert c.stores.count() == 2 and c.categories.count() == 2
    u = User.objects.get(login_id="freshmart")
    assert u.client == c and u.must_change_password and u.check_password("Temp-password-123")
    assert c.email_list() == ["a@fresh.example", "b@fresh.example"]
    assert not ActivityLog.objects.filter(detail__contains="Temp-password-123").exists()


def test_reset_password_shown_once_not_logged(admin_client, user_a):
    r = admin_client.post(reverse("console:user_reset", args=[user_a.pk]))
    assert r.status_code == 200
    temp = r.context["temp_password"]
    user_a.refresh_from_db()
    assert user_a.check_password(temp) and user_a.must_change_password
    assert not ActivityLog.objects.filter(detail__contains=temp).exists()
    assert r["Cache-Control"] == "no-store"


def test_view_as_client_is_logged_and_bannered(admin_client, client_a):
    r = admin_client.post(reverse("console:view_as", args=[client_a.pk]))
    assert r.status_code == 302
    r = admin_client.get(reverse("portal:dashboard"))
    assert "Viewing as Alpha Retail" in r.content.decode()
    assert ActivityLog.objects.filter(action="Started view as client").exists()
    admin_client.post(reverse("console:view_as_stop"))
    assert "view_as_client_id" not in admin_client.session


def test_settings_change(admin_client):
    r = admin_client.post(reverse("console:settings"), {
        "good_pct": "1.5", "warn_pct": "2.5", "cycle_days": 90, "soon_days": 15,
        "session_timeout_minutes": 30, "signed_url_minutes": 10, "notify_new_audit": "on"})
    assert r.status_code == 302
    from core.models import AppSettings

    assert str(AppSettings.load().good_pct) == "1.50"


# ---------------------------------------------------------------- bootstrap_admin password recovery


def _reset_env(monkeypatch, login="BOSS", password="Brand-new-admin-pass-9", flag="1"):
    monkeypatch.setenv("ADMIN_RESET_PASSWORD", flag)
    monkeypatch.setenv("ADMIN_LOGIN_ID", login)
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", password)


def test_bootstrap_admin_resets_password(monkeypatch, admin, capsys):
    from axes.models import AccessAttempt
    from django.core.management import call_command

    User.objects.filter(pk=admin.pk).update(is_active=False, must_change_password=False)
    AccessAttempt.objects.create(username="Boss", ip_address="10.0.0.1", user_agent="x", failures_since_start=5,
                                 get_data="", post_data="", http_accept="", path_info="/accounts/login/")
    _reset_env(monkeypatch)
    call_command("bootstrap_admin")
    out = capsys.readouterr().out
    assert "bootstrap_admin: password reset for 'boss'" in out
    assert "Brand-new-admin-pass-9" not in out
    admin.refresh_from_db()
    assert admin.check_password("Brand-new-admin-pass-9")
    assert admin.is_active and admin.must_change_password
    assert not AccessAttempt.objects.filter(username__iexact="boss").exists()
    assert User.objects.filter(role=Role.ADMIN).count() == 1


def test_reset_lets_a_locked_out_admin_sign_in(monkeypatch, client, admin, password):
    from django.core.management import call_command

    for _ in range(5):
        login(client, "boss", "wrong-password")
    assert login(client, "boss", password).status_code in (403, 429)  # locked
    _reset_env(monkeypatch)
    call_command("bootstrap_admin")
    r = login(client, "boss", "Brand-new-admin-pass-9")
    assert r.status_code == 302 and "_auth_user_id" in client.session
    assert client.get(reverse("console:overview"))["Location"] == reverse("accounts:password_change")


def test_reset_needs_flag_exactly_1(monkeypatch, admin, password):
    from django.core.management import call_command

    _reset_env(monkeypatch, flag="0")
    call_command("bootstrap_admin")
    admin.refresh_from_db()
    assert admin.check_password(password)


def test_reset_ignores_non_admin_and_unknown(monkeypatch, user_a, admin, password, capsys):
    from django.core.management import call_command

    _reset_env(monkeypatch, login="alpha")  # a client login, not an admin
    call_command("bootstrap_admin")
    user_a.refresh_from_db()
    assert user_a.check_password(password)
    _reset_env(monkeypatch, login="nobody")
    call_command("bootstrap_admin")
    assert "Brand-new-admin-pass-9" not in capsys.readouterr().out + capsys.readouterr().err
    assert User.objects.filter(role=Role.ADMIN).count() == 1


def test_reset_without_an_admin_falls_back_to_first_admin(monkeypatch):
    from django.core.management import call_command

    _reset_env(monkeypatch, login="owner")
    call_command("bootstrap_admin")
    # the reset finds no admin, so the normal first-admin path creates one
    u = User.objects.get(role=Role.ADMIN)
    assert u.login_id == "owner" and u.check_password("Brand-new-admin-pass-9")
