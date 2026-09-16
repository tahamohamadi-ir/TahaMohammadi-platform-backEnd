"""Read-only bilingual pairing audit for Atlas candidate records.

Preflight command for Knowledge Atlas v1 (Plan A, Task 1). It reports, per
published candidate record, whether a counterpart exists in the other locale
under the same ``translation_key``. It never writes to the database; exit code
1 signals that at least one candidate lacks a usable pairing.

The JSON report shape is documented in
``docs/contracts/ATLAS-PAYLOAD-CONTRACT.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.content.models import Profile, ResearchTopic

# Task 8 will move this allow-list into ``apps/atlas/canonical.py`` and this
# command will import it from there instead.
CANONICAL_SOURCES = {
    "profile": Profile,
    "researchtopic": ResearchTopic,
}


class Command(BaseCommand):
    help = "Read-only audit of bilingual pairing for Atlas candidate records."

    def add_arguments(self, parser):
        parser.add_argument("--json", dest="json_path", default=None)
        parser.add_argument("--models", default="profile,researchtopic")

    def handle(self, *args, **options):
        report = {"generated_at": timezone.now().isoformat(), "candidates": [], "blocking": []}
        for key in options["models"].split(","):
            model = CANONICAL_SOURCES[key.strip()]
            for row in model.objects.filter(status="published").order_by("slug", "locale"):
                partner = (
                    model.objects.filter(
                        translation_key=row.translation_key,
                        locale=("fa" if row.locale == "en" else "en"),
                    ).exists()
                    if row.translation_key
                    else False
                )
                entry = {
                    "model": model._meta.model_name,
                    "locale": row.locale,
                    "pk": row.pk,
                    "slug": row.slug,
                    "translation_key": str(row.translation_key) if row.translation_key else None,
                    "partner_locale_present": partner,
                }
                report["candidates"].append(entry)
                if not row.translation_key or not partner:
                    report["blocking"].append(entry)
        if options["json_path"]:
            Path(options["json_path"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.stdout.write(
            f"atlas_preflight: candidates={len(report['candidates'])} "
            f"blocking={len(report['blocking'])}"
        )
        if report["blocking"]:
            raise SystemExit(1)
