"""Load published CMS content mirrored from static site sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.content.data.site_content import (
    ARTICLES,
    LANDINGS,
    PROFILES,
    PROJECTS,
    PUBLICATIONS,
    RESEARCH_STATEMENTS,
    RESEARCH_TOPICS,
)
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


@dataclass
class SeedCounts:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    def bump(self, kind: str, *, created: bool, skipped: bool = False) -> None:
        if skipped:
            self.skipped += 1
            return
        if created:
            self.created += 1
        else:
            self.updated += 1
        self.by_type[kind] = self.by_type.get(kind, 0) + 1


class Command(BaseCommand):
    help = (
        "Seed published Landing, Profile, research topics/statements, publications, "
        "projects, and articles from apps/content/data/site_content.py (static site mirror)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update existing rows for the canonical slugs instead of skipping them.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print actions without writing to the database.",
        )

    def handle(self, *args, **options) -> None:
        force: bool = options["force"]
        dry_run: bool = options["dry_run"]
        published_at = timezone.now() - timedelta(days=1)
        counts = SeedCounts()

        if dry_run:
            self.stdout.write("Dry run — no database writes.")

        with transaction.atomic():
            self._seed_landings(force, dry_run, published_at, counts)
            self._seed_profiles(force, dry_run, published_at, counts)
            self._seed_research_statements(force, dry_run, published_at, counts)
            topic_map = self._seed_research_topics(force, dry_run, published_at, counts)
            publication_map = self._seed_publications(force, dry_run, published_at, counts)
            self._seed_projects(
                force,
                dry_run,
                published_at,
                counts,
                topic_map,
                publication_map,
            )
            self._seed_articles(force, dry_run, published_at, counts)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                "Seed complete: "
                f"created={counts.created}, updated={counts.updated}, skipped={counts.skipped}"
            )
        )
        for kind, total in sorted(counts.by_type.items()):
            self.stdout.write(f"  {kind}: {total}")

    def _upsert(
        self,
        model,
        *,
        lookup: dict[str, Any],
        defaults: dict[str, Any],
        force: bool,
        dry_run: bool,
        kind: str,
        counts: SeedCounts,
    ):
        existing = model.objects.filter(**lookup).first()
        if existing and not force:
            counts.bump(kind, created=False, skipped=True)
            return existing

        if dry_run:
            action = "create" if existing is None else "update"
            self.stdout.write(f"[dry-run] {kind} {action} {lookup}")
            counts.bump(kind, created=existing is None)
            return existing

        obj, created = model.objects.update_or_create(
            defaults=defaults,
            **lookup,
        )
        counts.bump(kind, created=created)
        return obj

    def _published_defaults(self, published_at) -> dict[str, Any]:
        return {
            "status": LifecycleStatus.PUBLISHED,
            "published_at": published_at,
        }

    def _seed_landings(self, force, dry_run, published_at, counts: SeedCounts) -> None:
        for locale_code, payload in LANDINGS.items():
            self._upsert(
                Landing,
                lookup={"locale": locale_code, "slug": payload["slug"]},
                defaults={
                    **self._published_defaults(published_at),
                    "title": payload["title"],
                    "body": payload["body"],
                    "seo_title": payload["seo_title"],
                    "seo_description": payload["seo_description"],
                },
                force=force,
                dry_run=dry_run,
                kind="landing",
                counts=counts,
            )

    def _seed_profiles(self, force, dry_run, published_at, counts: SeedCounts) -> None:
        for locale_code, payload in PROFILES.items():
            self._upsert(
                Profile,
                lookup={"locale": locale_code, "slug": payload["slug"]},
                defaults={
                    **self._published_defaults(published_at),
                    "title": payload["title"],
                    "body": payload["body"],
                    "seo_title": payload["seo_title"],
                    "seo_description": payload["seo_description"],
                },
                force=force,
                dry_run=dry_run,
                kind="profile",
                counts=counts,
            )

    def _seed_research_statements(
        self, force, dry_run, published_at, counts: SeedCounts
    ) -> None:
        for locale_code, payload in RESEARCH_STATEMENTS.items():
            self._upsert(
                ResearchStatement,
                lookup={"locale": locale_code, "slug": payload["slug"]},
                defaults={
                    **self._published_defaults(published_at),
                    "title": payload["title"],
                    "body": payload["body"],
                },
                force=force,
                dry_run=dry_run,
                kind="research_statement",
                counts=counts,
            )

    def _seed_research_topics(
        self, force, dry_run, published_at, counts: SeedCounts
    ) -> dict[tuple[str, str], ResearchTopic]:
        topic_map: dict[tuple[str, str], ResearchTopic] = {}
        for locale_code, rows in RESEARCH_TOPICS.items():
            for payload in rows:
                obj = self._upsert(
                    ResearchTopic,
                    lookup={"locale": locale_code, "slug": payload["slug"]},
                    defaults={
                        **self._published_defaults(published_at),
                        "title": payload["title"],
                        "summary": payload["summary"],
                        "motivation": payload["motivation"],
                        "problems": payload["problems"],
                        "research_questions": payload["research_questions"],
                        "methods": payload["methods"],
                        "future_directions": payload["future_directions"],
                    },
                    force=force,
                    dry_run=dry_run,
                    kind="research_topic",
                    counts=counts,
                )
                if obj is not None:
                    topic_map[(locale_code, payload["slug"])] = obj
        return topic_map

    def _seed_publications(
        self, force, dry_run, published_at, counts: SeedCounts
    ) -> dict[tuple[str, str], Publication]:
        publication_map: dict[tuple[str, str], Publication] = {}
        for locale_code, rows in PUBLICATIONS.items():
            for payload in rows:
                obj = self._upsert(
                    Publication,
                    lookup={"locale": locale_code, "slug": payload["slug"]},
                    defaults={
                        **self._published_defaults(published_at),
                        "title": payload["title"],
                        "authors": payload["authors"],
                        "venue": payload["venue"],
                    },
                    force=force,
                    dry_run=dry_run,
                    kind="publication",
                    counts=counts,
                )
                if obj is not None:
                    publication_map[(locale_code, payload["slug"])] = obj
        return publication_map

    def _seed_projects(
        self,
        force,
        dry_run,
        published_at,
        counts: SeedCounts,
        topic_map: dict[tuple[str, str], ResearchTopic],
        publication_map: dict[tuple[str, str], Publication],
    ) -> None:
        for locale_code, rows in PROJECTS.items():
            for payload in rows:
                lookup = {"locale": locale_code, "slug": payload["slug"]}
                defaults = {
                    **self._published_defaults(published_at),
                    "title": payload["title"],
                    "project_type": payload["project_type"],
                    "objective": payload["objective"],
                    "methods_summary": payload["methods_summary"],
                    "role": payload["role"],
                    "license": payload["license"],
                    "code_availability": payload["code_availability"],
                    "data_availability": payload["data_availability"],
                    "demo_availability": payload["demo_availability"],
                    "code_url": payload["code_url"],
                    "show_on_projects": payload.get("show_on_projects", True),
                }

                if dry_run:
                    self._upsert(
                        Project,
                        lookup=lookup,
                        defaults=defaults,
                        force=force,
                        dry_run=True,
                        kind="project",
                        counts=counts,
                    )
                    continue

                existing = Project.objects.filter(**lookup).first()
                if existing and not force:
                    counts.bump("project", created=False, skipped=True)
                    continue

                project, created = Project.objects.update_or_create(
                    defaults=defaults,
                    **lookup,
                )
                counts.bump("project", created=created)

                topic_ids = [
                    topic_map[(locale_code, slug)].pk
                    for slug in payload["topic_slugs"]
                    if (locale_code, slug) in topic_map
                ]
                publication_ids = [
                    publication_map[(locale_code, slug)].pk
                    for slug in payload["publication_slugs"]
                    if (locale_code, slug) in publication_map
                ]
                if topic_ids:
                    project.topics.set(
                        ResearchTopic.objects.filter(pk__in=topic_ids)
                    )
                if publication_ids:
                    project.publications.set(
                        Publication.objects.filter(pk__in=publication_ids)
                    )

        if not dry_run and not force:
            missing_topics = sum(
                1
                for locale_code, rows in PROJECTS.items()
                for payload in rows
                for slug in payload["topic_slugs"]
                if (locale_code, slug) not in topic_map
            )
            if missing_topics:
                raise CommandError(
                    "Some project topic links were missing after seed; re-run with --force."
                )

    def _seed_articles(self, force, dry_run, published_at, counts: SeedCounts) -> None:
        for locale_code, rows in ARTICLES.items():
            for payload in rows:
                self._upsert(
                    Article,
                    lookup={"locale": locale_code, "slug": payload["slug"]},
                    defaults={
                        **self._published_defaults(published_at),
                        "title": payload["title"],
                        "excerpt": payload["excerpt"],
                        "body": payload["body"],
                        "license": payload["license"],
                    },
                    force=force,
                    dry_run=dry_run,
                    kind="article",
                    counts=counts,
                )
