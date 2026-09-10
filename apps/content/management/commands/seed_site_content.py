"""Load published CMS content mirrored from static site sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from math import cos, pi, sin
from typing import Any

from django.contrib.contenttypes.models import ContentType
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
    ContentSeedRecord,
    GraphEdge,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    GraphVersionStatus,
    HomeModule,
    HomeModuleKey,
    Landing,
    LifecycleStatus,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    SelectionMode,
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
            "--overwrite-id",
            action="append",
            default=[],
            help="Refresh only this reported site content ID (repeatable).",
        )
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
        self.overwrite_ids = set(options["overwrite_id"])
        self.seen_ids = set()
        self.written_objects = set()

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
            self._seed_home_composition(dry_run, published_at, counts)
            self._seed_research_graph(dry_run, counts)

            unknown = self.overwrite_ids - self.seen_ids
            if unknown:
                raise CommandError(f"Unknown overwrite IDs: {', '.join(sorted(unknown))}")
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

    def _seed_home_composition(
        self,
        dry_run: bool,
        published_at,
        counts: SeedCounts,
    ) -> None:
        """Create the canonical shell only when a locale has no composition.

        A single existing row means the locale has been configured through the
        admin editor. Preserve that complete owner-controlled composition,
        including draft or hidden rows, on every seed run.
        """
        for locale in ("en", "fa"):
            if HomeModule.objects.filter(locale=locale).exists():
                self.stdout.write(f"home.composition.{locale}: preserve")
                continue

            self.stdout.write(
                f"home.composition.{locale}: create keys={','.join(HomeModuleKey.values)}"
            )
            for order, key in enumerate(HomeModuleKey.values):
                counts.bump("home_module", created=True)
                if dry_run:
                    continue
                HomeModule.objects.create(
                    locale=locale,
                    key=key,
                    visible=True,
                    order=order,
                    selection_mode=SelectionMode.MANUAL,
                    status=LifecycleStatus.PUBLISHED,
                    published_at=published_at,
                )

    def _seed_research_graph(self, dry_run: bool, counts: SeedCounts) -> None:
        """Create a graph from published profile/topic records when none exists.

        Labels, summaries, and links come from the locale's public records.
        Edges only express the existing profile-to-research-topic grouping.
        Any graph version created in the editor remains fully owner-controlled.
        """
        color_roles = ("research", "signature", "context")
        icon_roles = ("disc", "square", "diamond")

        for locale in ("en", "fa"):
            if GraphVersion.objects.filter(locale=locale).exists():
                self.stdout.write(f"research.graph.{locale}: preserve")
                continue

            profile = Profile.objects.public().filter(locale=locale).order_by("slug").first()
            topics = list(
                ResearchTopic.objects.public().filter(locale=locale).order_by("slug")
            )
            if profile is None or not topics:
                self.stdout.write(f"research.graph.{locale}: skip incomplete public records")
                continue

            self.stdout.write(
                f"research.graph.{locale}: create topics={len(topics)}"
            )
            counts.bump("graph_version", created=True)
            if dry_run:
                continue

            version = GraphVersion.objects.create(
                locale=locale,
                status=GraphVersionStatus.ACTIVE,
            )
            identity = GraphNode.objects.create(
                version=version,
                node_id="identity",
                label=profile.title,
                type="identity",
                accessible_label=profile.title,
                color_role="brand",
                icon_role="ring",
                weight=1,
                pos_x=0,
                pos_y=0,
                pos_z=8,
            )
            profile_type = ContentType.objects.get_for_model(Profile)
            GraphNodeRelated.objects.create(
                node=identity,
                content_type=profile_type,
                object_id=profile.pk,
            )
            topic_type = ContentType.objects.get_for_model(ResearchTopic)
            radius_x = 112
            radius_y = 76
            for index, topic in enumerate(topics):
                angle = -pi / 2 + (2 * pi * index) / len(topics)
                node = GraphNode.objects.create(
                    version=version,
                    node_id=f"research-topic-{topic.pk}",
                    label=topic.title,
                    type="research-topic",
                    summary=topic.summary,
                    accessible_label=topic.title,
                    color_role=color_roles[index % len(color_roles)],
                    icon_role=icon_roles[index % len(icon_roles)],
                    weight=1,
                    pos_x=round(cos(angle) * radius_x, 3),
                    pos_y=round(sin(angle) * radius_y, 3),
                    pos_z=(-8, 12, -2)[index % 3],
                )
                GraphNodeRelated.objects.create(
                    node=node,
                    content_type=topic_type,
                    object_id=topic.pk,
                )
                GraphEdge.objects.create(
                    version=version,
                    source=identity,
                    target=node,
                    relation_type="research-focus",
                    directed=True,
                    weight=1,
                )

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
        content_id = f"site.{kind}.{lookup['locale']}.{lookup['slug']}"
        self.seen_ids.add(content_id)
        marker = ContentSeedRecord.objects.filter(content_id=content_id).first()
        existing = (
            model.objects.filter(pk=marker.mapped_object_id).first()
            if marker and marker.mapped_model_label == model._meta.label
            else None
        )
        if existing is None and marker is None:
            existing = model.objects.filter(**lookup).first()
        overwrite = force or content_id in self.overwrite_ids
        if (marker or existing) and not overwrite:
            counts.bump(kind, created=False, skipped=True)
            self.stdout.write(f"{content_id}: preserve")
            # Adopt existing canonical rows once so later owner slug edits remain safe.
            if existing is not None and marker is None and not dry_run:
                self._record_mapping(content_id, kind, lookup, existing)
            return existing

        defaults = dict(defaults)
        if existing is not None:
            defaults.pop("status", None)
            defaults.pop("published_at", None)
        action = "create" if existing is None else "overwrite"
        self.stdout.write(f"{content_id}: {action} fields={','.join(sorted(defaults))}")
        if dry_run:
            counts.bump(kind, created=existing is None)
            return existing

        if existing is None:
            obj, created = model.objects.get_or_create(defaults=defaults, **lookup)
        else:
            obj, created = existing, False
            for key, value in defaults.items():
                setattr(obj, key, value)
            obj.save(update_fields=[*defaults, "updated_at"])
        self._record_mapping(content_id, kind, lookup, obj)
        self.written_objects.add((kind, obj.pk))
        counts.bump(kind, created=created)
        return obj

    def _record_mapping(self, content_id, kind, lookup, obj):
        ContentSeedRecord.objects.update_or_create(
            content_id=content_id,
            defaults={
                "content_type": kind,
                "locale": lookup["locale"],
                "slug": lookup["slug"],
                "mapped_model_label": obj._meta.label,
                "mapped_object_id": obj.pk,
                "payload": {},
            },
        )

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

    def _seed_research_statements(self, force, dry_run, published_at, counts: SeedCounts) -> None:
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

                project = self._upsert(
                    Project,
                    lookup=lookup,
                    defaults=defaults,
                    force=force,
                    dry_run=dry_run,
                    kind="project",
                    counts=counts,
                )
                if (
                    dry_run
                    or project is None
                    or ("project", project.pk) not in self.written_objects
                ):
                    continue

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
                    project.topics.set(ResearchTopic.objects.filter(pk__in=topic_ids))
                if publication_ids:
                    project.publications.set(Publication.objects.filter(pk__in=publication_ids))

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
