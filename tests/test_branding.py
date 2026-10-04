"""Branding: assets in use, one active nav item, prototype wording."""

import re

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from tests import factories as f

pytestmark = pytest.mark.django_db
ASSOC = "brand/associated-with-ca-india-vkna.png"


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def active_items(html):
    nav = html.split('class="sidebar', 1)[1].split("</aside>", 1)[0]
    return re.findall(r'<a class="nav-link"[^>]*aria-current="page"[^>]*>.*?<span>([^<]+)</span>', nav, flags=re.S)


def test_login_uses_brand_assets(client):
    html = client.get(reverse("accounts:login")).content.decode()
    assert ASSOC in html
    assert 'class="xl"' in html and 'class="xd"' in html  # x mark from assets/x-mark.svg
    assert "SOLUTIONS &amp; CO" in html and "Welcome to Audix Vault" in html
    assert "Forgot your password? Contact Audix Solutions &amp; Co and we will issue a new one." in html


def test_footer_has_image_chip(user_a):
    html = http_for(user_a).get(reverse("portal:dashboard")).content.decode()
    footer = html.split('<footer class="footer">', 1)[1]
    assert 'class="assoc-chip"' in footer and ASSOC in footer
    assert "Powered by <b>Audix Solutions &amp; Co</b>" in footer


def test_xmark_comes_from_asset_file(settings):
    from core.templatetags.audix import xmark_svg

    raw = (settings.BASE_DIR / "assets" / "x-mark.svg").read_text()
    for points in re.findall(r'points="([^"]+)"', raw):
        assert points in xmark_svg()


def test_brand_static_is_served(client):
    from django.contrib.staticfiles import finders

    assert finders.find(ASSOC) and finders.find("brand/x-mark.svg")


@pytest.mark.parametrize("name,expected", [
    ("console:overview", "Overview"), ("audits:new", "Add audit"), ("audits:list", "Audits"),
    ("console:clients", "Clients"), ("console:settings", "Settings"), ("console:activity", "Activity log"),
    ("console:import", "Import old data"), ("portal:aging", "Aging report"), ("portal:compare", "Compare"),
])
def test_admin_sidebar_highlights_one_item(admin, client_a, name, expected):
    html = http_for(admin).get(reverse(name)).content.decode()
    assert active_items(html) == [expected]


def test_admin_sidebar_on_entry_and_edit_pages(admin, client_a):
    h = http_for(admin)
    assert active_items(h.get(reverse("audits:new") + f"?client={client_a.pk}").content.decode()) == ["Add audit"]
    audit = f.make_audit(client_a, publish=False)
    assert active_items(h.get(reverse("audits:edit", args=[audit.pk])).content.decode()) == ["Audits"]
    assert active_items(h.get(reverse("audits:detail", args=[audit.pk])).content.decode()) == ["Audits"]


@pytest.mark.parametrize("name,expected", [
    ("portal:dashboard", "Dashboard"), ("portal:tracker", "Audit tracker"), ("portal:aging", "Aging report"),
    ("portal:compare", "Compare"), ("portal:audits", "All audits"),
])
def test_client_sidebar_highlights_one_item(user_a, name, expected):
    assert active_items(http_for(user_a).get(reverse(name)).content.decode()) == [expected]


def test_client_badge_and_wording(user_a, client_a):
    f.make_audit(client_a)
    html = http_for(user_a).get(reverse("portal:dashboard") + "?period=weekly").content.decode()
    assert "Welcome back, Alpha Retail" in html
    assert '<span class="mono" aria-hidden="true">AR</span>' in html
    assert "2 stores across 2 cities" in html
    assert "Net variance, last 4 weeks" in html and "vs previous 4 weeks" in html
    assert "Shortage, below the line" in html
