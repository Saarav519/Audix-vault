from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import backup
from core.models import AppSettings


class Command(BaseCommand):
    help = "pg_dump the database (gzip) into the backup bucket under db/. Run daily from Railway cron."

    def handle(self, *args, **opts):
        try:
            client = backup.backup_client()
        except Exception as e:
            raise CommandError(str(e))
        data = backup.dump_database()
        key = f"{backup.DB_PREFIX}audix-{timezone.now():%Y%m%d-%H%M%S}.sql.gz"
        client.put_object(Bucket=settings.BACKUP_S3_BUCKET_NAME, Key=key, Body=data, ContentType="application/gzip")
        s = AppSettings.objects.get_or_create(pk=1)[0]
        s.last_backup_at = timezone.now()
        s.last_backup_note = f"{key} ({len(data) // 1024} KB)"
        s.save()
        self.stdout.write(self.style.SUCCESS(f"Backup written to {key} ({len(data)} bytes)."))
