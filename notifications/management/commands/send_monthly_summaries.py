from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from clients.models import Client
from notifications.emails import send_monthly_summary


class Command(BaseCommand):
    help = "Email each active client a summary of last month (run on the 1st, e.g. Railway cron '0 3 1 * *')."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="YYYY-MM (default: last month)")

    def handle(self, *args, **opts):
        if opts.get("month"):
            try:
                year, month = (int(x) for x in opts["month"].split("-"))
                date(year, month, 1)
            except ValueError:
                raise CommandError("Use --month YYYY-MM")
        else:
            first = timezone.localdate().replace(day=1)
            prev = date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)
            year, month = prev.year, prev.month
        sent = 0
        for client in Client.objects.filter(is_active=True):
            sent += 1 if send_monthly_summary(client, year, month) else 0
        self.stdout.write(f"Monthly summaries sent: {sent}")
