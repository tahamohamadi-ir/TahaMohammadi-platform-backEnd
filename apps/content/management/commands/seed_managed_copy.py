"""Initialize the localized owner-managed copy dictionary without publishing it."""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.siteconfig.models import LocalizedSiteSettings

DEFAULT_COPY_PATH = Path(__file__).resolve().parents[3] / "siteconfig/seeds/public-copy.json"


class Command(BaseCommand):
    help = "Add missing managed-copy keys for en/fa; preserve owner values and published snapshots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", default=str(DEFAULT_COPY_PATH), help="JSON with en/fa objects."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without writing changes."
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read managed copy: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"en", "fa"}:
            raise CommandError("Managed copy must contain exactly en and fa objects.")
        for locale, entries in payload.items():
            if not isinstance(entries, dict) or len(entries) > 1500:
                raise CommandError(f"{locale}: expected an object with at most 1500 keys.")
            for key, value in entries.items():
                if (
                    not re.fullmatch(r"[a-z][a-zA-Z0-9._-]{0,119}", key)
                    or not isinstance(value, str)
                    or len(value) > 10000
                ):
                    raise CommandError(f"{locale}.{key}: invalid copy key or string value.")

        with transaction.atomic():
            for locale, entries in payload.items():
                row = (
                    LocalizedSiteSettings.objects.select_for_update().filter(locale=locale).first()
                )
                current = row.managed_copy if row else {}
                if not isinstance(current, dict):
                    raise CommandError(f"{locale}: existing managed copy must be an object.")
                missing = {key: value for key, value in entries.items() if key not in current}
                if len(current) + len(missing) > 1500:
                    raise CommandError(f"{locale}: merged managed copy exceeds 1500 keys.")
                action = "create draft" if row is None else "add missing keys"
                self.stdout.write(
                    f"{locale}: {action}; added={','.join(sorted(missing)) or '(none)'}; "
                    f"preserved={len(entries) - len(missing)}"
                )
                if options["dry_run"]:
                    continue
                if row is None:
                    # Creation cannot publish or create a published snapshot.
                    LocalizedSiteSettings.objects.create(locale=locale, managed_copy=missing)
                elif missing:
                    row.managed_copy = {**current, **missing}
                    row.save(update_fields=["managed_copy", "updated_at"])
        if options["dry_run"]:
            self.stdout.write("Dry run: no database changes written.")
