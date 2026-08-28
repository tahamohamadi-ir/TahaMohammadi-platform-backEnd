"""Publish due scheduled content without Celery (ADM-4 / DEBT-0005)."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.content.models import (
    Article,
    Landing,
    LifecycleStatus,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
)
from apps.rebuild.services import invoke_static_rebuild
from apps.security.models import AuditLog

# Keep in sync with apps.api.admin_content.ENTITY_MODELS (avoid importing the router).
ENTITY_MODELS = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
    "research-topic": ResearchTopic,
    "research-statement": ResearchStatement,
    "project": Project,
    "publication": Publication,
}


class Command(BaseCommand):
    help = (
        "Publish content rows with status=scheduled and scheduled_for <= now. "
        "Idempotent; safe to run from a systemd timer or cron."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List due rows without changing status.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        now = timezone.now()
        published_count = 0
        failures: list[str] = []

        for entity, model in ENTITY_MODELS.items():
            due_ids = list(
                model.objects.filter(
                    status=LifecycleStatus.SCHEDULED,
                    scheduled_for__isnull=False,
                    scheduled_for__lte=now,
                )
                .order_by("scheduled_for", "id")
                .values_list("pk", flat=True)
            )
            for pk in due_ids:
                label = f"{entity}:{pk}"
                if dry_run:
                    self.stdout.write(f"due {label}")
                    published_count += 1
                    continue
                try:
                    with transaction.atomic():
                        item = model.objects.select_for_update().get(pk=pk)
                        if (
                            item.status != LifecycleStatus.SCHEDULED
                            or item.scheduled_for is None
                            or item.scheduled_for > timezone.now()
                        ):
                            continue
                        old_status = item.status
                        item.status = LifecycleStatus.PUBLISHED
                        if item.published_at is None:
                            item.published_at = timezone.now()
                        item.scheduled_for = None
                        item.save(
                            update_fields=["status", "published_at", "scheduled_for", "updated_at"]
                        )
                        AuditLog.objects.create(
                            user=None,
                            action=f"lifecycle.{old_status}->published",
                            model_name=entity,
                            object_id=str(pk),
                            ip="",
                            detail="reason=publish_scheduled_content",
                        )
                    published_count += 1
                    self.stdout.write(self.style.SUCCESS(f"published {label}"))
                except Exception as exc:  # noqa: BLE001 — report and continue
                    failures.append(f"{label}: {exc}")
                    self.stderr.write(self.style.ERROR(f"failed {label}: {exc}"))

        if published_count and not dry_run:
            invoke_static_rebuild()

        self.stdout.write(
            f"publish_scheduled_content done published={published_count} "
            f"failures={len(failures)} dry_run={dry_run}"
        )
        if failures:
            raise SystemExit(1)
