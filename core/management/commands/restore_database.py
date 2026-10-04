from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import backup


class Command(BaseCommand):
    help = "Restore the database from a backup in the backup bucket. Destructive: needs --yes."

    def add_arguments(self, parser):
        parser.add_argument("--key", help="Backup key (default: the latest db/ backup)")
        parser.add_argument("--yes", action="store_true", help="Confirm that the current data will be replaced")

    def handle(self, *args, **opts):
        if not opts["yes"]:
            raise CommandError("This replaces the current database. Run again with --yes to confirm.")
        try:
            client = backup.backup_client()
        except Exception as e:
            raise CommandError(str(e))
        key = opts.get("key") or backup.latest_db_key(client, settings.BACKUP_S3_BUCKET_NAME)
        if not key:
            raise CommandError("No database backup found.")
        data = client.get_object(Bucket=settings.BACKUP_S3_BUCKET_NAME, Key=key)["Body"].read()
        backup.restore_sql(data)
        self.stdout.write(self.style.SUCCESS(f"Restored from {key}."))
