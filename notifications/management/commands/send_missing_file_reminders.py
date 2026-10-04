from django.core.management.base import BaseCommand

from notifications.emails import send_missing_files_reminder


class Command(BaseCommand):
    help = "Email admins and auditors a list of audits waiting for files (run daily)."

    def handle(self, *args, **opts):
        n = send_missing_files_reminder()
        self.stdout.write("Reminder sent." if n else "No reminder sent (nothing waiting, disabled or no staff emails).")
