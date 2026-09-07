"""Publish due scheduled content without Celery (ADM-4 / DEBT-0005)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.content.models import (
    Article,
    Book,
    Collection,
    ContentSeedRecord,
    Course,
    CreativeWork,
    Download,
    Landing,
    Lesson,
    LifecycleStatus,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)
from apps.rebuild.services import (
    compute_affected_paths,
    enqueue_publication_job,
)
from apps.security.models import AuditLog

# Keep in sync with apps.api.admin_content.ENTITY_MODELS (avoid importing the router).
ENTITY_MODELS = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
    "series": Series,
    "research-topic": ResearchTopic,
    "research-statement": ResearchStatement,
    "project": Project,
    "publication": Publication,
    "book": Book,
    "talk": Talk,
    "download": Download,
    "course": Course,
    "creative-work": CreativeWork,
    "lesson": Lesson,
    "collection": Collection,
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
        published_items: list[tuple[str, Any]] = []
        failures: list[str] = []

        # Deferred import: the admin router is not imported at module load.
        # A04: scheduled publication records the same publication snapshot as
        # the admin transition, so later draft edits keep serving this version.
        from apps.api.admin_content import _record_publication_snapshot

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
                        # Owner publication gate (BACKEND-210): the same
                        # rule the admin transition enforces; a scheduled row
                        # whose seed approval triple never cleared is skipped
                        # (and reported), never published here.
                        seed = ContentSeedRecord.objects.filter(
                            mapped_model_label=model._meta.label,
                            mapped_object_id=pk,
                        ).first()
                        if seed is not None and not seed.is_publication_allowed:
                            reason = (
                                f"{label}: APPROVAL_REQUIRED "
                                f"(approval_state={seed.approval_state!r})"
                            )
                            failures.append(reason)
                            self.stderr.write(self.style.ERROR(f"blocked {reason}"))
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
                        _record_publication_snapshot(item, entity, user=None)
                        published_items.append((entity, item))
                    published_count += 1
                    self.stdout.write(self.style.SUCCESS(f"published {label}"))
                except Exception as exc:  # noqa: BLE001 — report and continue
                    failures.append(f"{label}: {exc}")
                    self.stderr.write(self.style.ERROR(f"failed {label}: {exc}"))

        if published_count and not dry_run:
            paths_by_locale: dict[str, list[str]] = {}
            for ent, it in published_items:
                loc = getattr(it, "locale", "en") or "en"
                paths_by_locale.setdefault(loc, []).extend(compute_affected_paths(ent, it))
            for loc, paths in paths_by_locale.items():
                # A03: each enqueue dispatches its own runner trigger on commit.
                enqueue_publication_job(
                    locale=loc,
                    affected_paths=list(dict.fromkeys(paths)),
                    removal_state="not_requested",
                )

        self.stdout.write(
            f"publish_scheduled_content done published={published_count} "
            f"failures={len(failures)} dry_run={dry_run}"
        )
        if failures:
            raise SystemExit(1)
