import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    AUDITOR = "auditor", "Auditor"
    CLIENT = "client", "Client"
    CLIENT_STORE = "client_store", "Client store manager"


STAFF_ROLES = {Role.ADMIN, Role.AUDITOR}
CLIENT_ROLES = {Role.CLIENT, Role.CLIENT_STORE}


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, login_id):
        return self.get(login_id__iexact=(login_id or "").strip())

    def create_user(self, login_id, password=None, **extra):
        if not login_id:
            raise ValueError("A login ID is required.")
        user = self.model(login_id=login_id.strip().lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, login_id, password=None, **extra):
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("is_superuser", True)
        extra.setdefault("name", "Audix Admin")
        return self.create_user(login_id, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    login_id = models.CharField("Login ID", max_length=64, unique=True)
    name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CLIENT)
    client = models.ForeignKey(
        "clients.Client", null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    must_change_password = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    allowed_stores = models.ManyToManyField("clients.Store", blank=True, related_name="store_users")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "login_id"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["login_id"]

    def __str__(self):
        return self.name or self.login_id

    def save(self, *args, **kwargs):
        self.login_id = (self.login_id or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_staff(self):
        # Only used by Django internals; there is no Django admin site.
        return False

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def is_auditor(self):
        return self.role == Role.AUDITOR

    @property
    def is_staff_member(self):
        return self.role in STAFF_ROLES

    @property
    def is_client_user(self):
        return self.role in CLIENT_ROLES

    @property
    def label(self):
        return f"{self.name} ({self.login_id})" if self.name else self.login_id
