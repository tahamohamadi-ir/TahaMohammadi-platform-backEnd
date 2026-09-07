"""Prune aggregate analytics events older than 13 months (§I07)."""

from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analytics.models import AggregateEvent

DEFAULT_RETENTION_DAYS = 396  # ~13 calendar months


class Command(BaseCommand):
    help = "Prune aggregate analytics events older than 13 months (rolling retention)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Retention period in days (default: {DEFAULT_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many records would be deleted without deleting them.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]

        cutoff_date = timezone.now().date() - datetime.timedelta(days=days)
        stale_qs = AggregateEvent.objects.filter(date__lt=cutoff_date)
        count = stale_qs.count()

        if dry_run:
            self.stdout.write(
                f"[DRY-RUN] Found {count} aggregate event records older than "
                f"{cutoff_date} ({days} days) to prune."
            )
            return

        deleted_count, _ = stale_qs.delete()
        self.stdout.write(
            f"Successfully pruned {deleted_count} aggregate event records older than {cutoff_date}."
        )
