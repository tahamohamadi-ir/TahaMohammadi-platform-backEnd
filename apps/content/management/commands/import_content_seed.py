"""Import owner content seed v1.1 package (BACKEND-060..070)."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.content.services.content_seed_import import (
    default_seed_path,
    default_settings_path,
    import_content_seed_package,
)


class Command(BaseCommand):
    help = (
        "Import owner content seed v1.1 records from content-records.v1.1-seed.json "
        "as draft/not-public. Re-runs upsert by stable content_id."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--file",
            dest="seed_file",
            default=str(default_seed_path()),
            help="Path to content-records.v1.1-seed.json (default: workspace cms-package).",
        )
        parser.add_argument(
            "--settings-file",
            default=str(default_settings_path()),
            help="Path to supplement/seed-settings.json.",
        )

    def handle(self, *args, **options) -> None:
        seed_path = Path(options["seed_file"]).resolve()
        settings_path = Path(options["settings_file"]).resolve()

        if not seed_path.exists():
            raise CommandError(f"Seed file not found: {seed_path}")

        try:
            json.loads(seed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Seed file is not valid JSON: {seed_path}") from exc

        if not settings_path.exists():
            self.stdout.write(
                self.style.WARNING(f"Seed settings file not found (skipping): {settings_path}")
            )

        stats = import_content_seed_package(
            seed_path=seed_path,
            settings_path=settings_path if settings_path.exists() else None,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Imported content seed: "
                f"records={stats.records_upserted} "
                f"(created={stats.records_created}), "
                f"typed_mapped={stats.typed_mapped}, "
                f"typed_skipped={stats.typed_skipped}, "
                f"admin_only={stats.skipped_admin_only}"
            )
        )
        for content_type, total in sorted(stats.by_content_type.items()):
            self.stdout.write(f"  {content_type}: {total}")
