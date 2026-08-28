"""Recompute per-locale reading time for all articles (board A8 / F1)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.content.models import Article, compute_reading_time_minutes


class Command(BaseCommand):
    help = (
        "Recompute reading_time_minutes for every article using per-locale WPM "
        "(fa=180, en=230; unknown locales fall back to 200). Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report current vs recomputed values without saving.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        changed = 0
        total = 0

        for article in Article.objects.all().order_by("id"):
            total += 1
            recomputed = compute_reading_time_minutes(
                str(article.body or ""), locale=article.locale
            )
            label = f"article:{article.pk} locale={article.locale}"
            if recomputed == article.reading_time_minutes:
                continue
            self.stdout.write(
                f"{label} {article.reading_time_minutes} -> {recomputed}"
            )
            changed += 1
            if not dry_run:
                article.reading_time_minutes = recomputed
                article.save(update_fields=["reading_time_minutes", "updated_at"])

        mode = "dry-run" if dry_run else "applied"
        self.stdout.write(
            self.style.SUCCESS(
                f"recompute_reading_time done total={total} changed={changed} mode={mode}"
            )
        )
