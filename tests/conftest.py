import pytest
from django.core.cache import cache

from accounts.models import Role, User
from clients.models import Category, Client, Store
from tests import factories as f


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def password():
    return "Correct-horse-42"


@pytest.fixture
def admin(db, password):
    return User.objects.create_user("boss", password, name="Boss", role=Role.ADMIN, must_change_password=False)


@pytest.fixture
def auditor(db, password):
    return User.objects.create_user("aud", password, name="Aud", role=Role.AUDITOR, must_change_password=False)


@pytest.fixture
def client_a(db):
    return f.make_client("Alpha Retail")


@pytest.fixture
def client_b(db):
    return f.make_client("Beta Stores")


@pytest.fixture
def user_a(client_a, password):
    return User.objects.create_user("alpha", password, name="Alpha HO", role=Role.CLIENT, client=client_a,
                                    must_change_password=False)


@pytest.fixture
def user_b(client_b, password):
    return User.objects.create_user("beta", password, name="Beta HO", role=Role.CLIENT, client=client_b,
                                    must_change_password=False)


@pytest.fixture
def admin_client(client, admin):
    client.force_login(admin)
    return client


@pytest.fixture
def auditor_client(client, auditor):
    client.force_login(auditor)
    return client


__all__ = ["Category", "Client", "Store"]
