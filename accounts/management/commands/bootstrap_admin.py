import os

from django.core.management.base import BaseCommand

from accounts.models import Role, User


class Command(BaseCommand):
    help = ("Create the first admin from ADMIN_LOGIN_ID / ADMIN_INITIAL_PASSWORD if no admin exists. Idempotent. "
            "With ADMIN_RESET_PASSWORD=1, reset that admin's password instead (recovery).")

    def handle(self, *args, **options):
        if self.reset_admin_password():
            return
        if User.objects.filter(role=Role.ADMIN).exists():
            self.stdout.write("bootstrap_admin: an admin already exists, nothing to do.")
            return
        login_id = os.environ.get("ADMIN_LOGIN_ID", "").strip()
        password = os.environ.get("ADMIN_INITIAL_PASSWORD", "")
        if not login_id or not password:
            self.stdout.write("bootstrap_admin: ADMIN_LOGIN_ID / ADMIN_INITIAL_PASSWORD not set, skipping.")
            return
        if User.objects.filter(login_id__iexact=login_id).exists():
            self.stderr.write(f"bootstrap_admin: login ID {login_id!r} is taken by a non-admin user, skipping.")
            return
        force = os.environ.get("ADMIN_FORCE_PASSWORD_CHANGE", "1").strip() not in ("0", "false", "False", "no")
        User.objects.create_user(
            login_id,
            password,
            name=os.environ.get("ADMIN_NAME", "Audix Admin") or "Audix Admin",
            email=os.environ.get("ADMIN_EMAIL", ""),
            role=Role.ADMIN,
            must_change_password=force,
        )
        self.stdout.write(self.style.SUCCESS(f"bootstrap_admin: created admin {login_id.lower()!r}."))

    def reset_admin_password(self) -> bool:
        """Recovery: ADMIN_RESET_PASSWORD=1 resets the named admin's password. Never prints the password."""
        if os.environ.get("ADMIN_RESET_PASSWORD", "").strip() != "1":
            return False
        login_id = os.environ.get("ADMIN_LOGIN_ID", "").strip()
        password = os.environ.get("ADMIN_INITIAL_PASSWORD", "")
        if not login_id or not password:
            self.stderr.write("bootstrap_admin: ADMIN_RESET_PASSWORD=1 needs ADMIN_LOGIN_ID and ADMIN_INITIAL_PASSWORD.")
            return False
        user = User.objects.filter(login_id__iexact=login_id, role=Role.ADMIN).first()
        if user is None:
            self.stderr.write(f"bootstrap_admin: no admin with login ID {login_id.lower()!r}, nothing reset.")
            return False
        user.set_password(password)
        user.is_active = True
        user.must_change_password = True
        user.save()
        from axes.models import AccessAttempt

        AccessAttempt.objects.filter(username__iexact=login_id).delete()
        try:
            from activity.log import log
            from activity.models import ActionType

            log(None, ActionType.ADMIN_CHANGE, "Admin password reset by bootstrap_admin", user=user)
        except Exception:
            pass
        self.stdout.write(self.style.SUCCESS(f"bootstrap_admin: password reset for '{user.login_id}'"))
        return True
