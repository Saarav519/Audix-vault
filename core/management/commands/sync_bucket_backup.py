from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import backup, storage


class Command(BaseCommand):
    help = "Copy new or changed files from the files bucket into the backup bucket under files/."

    def handle(self, *args, **opts):
        backend = storage.get_backend()
        if not isinstance(backend, storage.S3Backend):
            raise CommandError("The files bucket (S3_BUCKET_NAME) is not configured.")
        try:
            dst = backup.backup_client()
        except Exception as e:
            raise CommandError(str(e))
        copied, skipped = backup.sync_bucket(backend.client, backend.bucket, dst, settings.BACKUP_S3_BUCKET_NAME)
        self.stdout.write(self.style.SUCCESS(f"Bucket backup: {copied} copied, {skipped} already up to date."))
