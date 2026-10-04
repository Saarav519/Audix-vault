"""Sign-in page: password show/hide toggle and page content."""

from pathlib import Path

from django.conf import settings
from django.urls import reverse


def test_login_page_renders(client, db):
    html = client.get(reverse("accounts:login")).content.decode()
    assert 'type="password"' in html and "js/app.js" in html
    assert "Secure client portal" in html and "Stock variance at a glance" in html
    assert "Associated with" in html


def test_password_toggle_script():
    js = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text()
    assert 'input[type="password"]' in js and "data-pw-toggle" in js
    assert "Show password" in js and "Hide password" in js
    # the field goes back to a password field before the form is sent
    assert 'addEventListener("submit"' in js
