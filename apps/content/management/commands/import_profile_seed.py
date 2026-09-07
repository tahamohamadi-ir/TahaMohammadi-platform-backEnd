from __future__ import annotations

import json
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.content.models import (
    ContentSeedRecord,
    LifecycleStatus,
    Locale,
    Profile,
    ProfileCertificate,
    ProfileEducation,
    ProfileExperience,
    ProfilePublication,
    ProfileResearchProject,
    ProfileSkill,
    ProfileSocialLink,
)

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "seeds" / "profile.seed.json"
)
DEFAULT_TITLES = {
    Locale.EN: "About",
    Locale.FA: "درباره",
}


def _detail_route_kwargs(item: dict) -> dict:
    translation_key = item.get("translationKey") or item.get("translation_key")
    return {
        "slug": item.get("slug", ""),
        "translation_key": uuid.UUID(translation_key) if translation_key else None,
        "detail_body": item.get("detailBody") or item.get("detail_body") or "",
    }


class Command(BaseCommand):
    help = "Import the CMS About/Profile seed artifact derived from apps/web/src/data/profile*.ts."

    def add_arguments(self, parser):
        parser.add_argument("--overwrite-id", action="append", default=[],
                            choices=["profile.en", "profile.fa"],
                            help="Refresh the selected profile, replacing its seed child lists; "
                                 "publication state and revision are preserved.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report changes and roll back the import transaction.")
        parser.add_argument(
            "--path",
            default=str(DEFAULT_SEED_PATH),
            help="Path to the committed profile seed JSON artifact.",
        )

    def handle(self, *args, **options):
        seed_path = Path(options["path"]).resolve()
        if not seed_path.exists():
            raise CommandError(f"Seed file not found: {seed_path}")

        try:
            payload = json.loads(seed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Seed file is not valid JSON: {seed_path}") from exc

        profiles = payload.get("profiles")
        if not isinstance(profiles, dict):
            raise CommandError("Seed JSON must contain a top-level 'profiles' object.")

        translation_key = uuid.UUID(payload.get("translationKey", str(uuid.uuid4())))
        selected = set(options["overwrite_id"])
        if selected - {"profile.en", "profile.fa"}:
            raise CommandError("Overwrite IDs must be profile.en or profile.fa.")
        imported_locales: list[str] = []
        with transaction.atomic():
            for locale in (Locale.EN, Locale.FA):
                locale_payload = profiles.get(locale)
                if not isinstance(locale_payload, dict):
                    raise CommandError(f"Missing locale payload for '{locale}'.")
                content_id = f"profile.{locale}"
                marker = ContentSeedRecord.objects.filter(content_id=content_id).first()
                existing = (Profile.objects.filter(pk=marker.mapped_object_id).first()
                            if marker and marker.mapped_model_label == "content.Profile" else None)
                if existing is None:
                    existing = Profile.objects.filter(locale=locale, slug="about").first()
                if (marker or existing) and content_id not in selected:
                    self.stdout.write(f"{content_id}: preserve profile and all children")
                    continue
                profile, _created = (existing, False) if existing else Profile.objects.get_or_create(
                    locale=locale,
                    slug="about",
                    defaults={"title": DEFAULT_TITLES[locale]},
                )
                profile.translation_key = translation_key
                profile.title = DEFAULT_TITLES[locale]
                profile.body = locale_payload.get("longBio") or locale_payload.get("shortBio", "")
                profile.seo_title = DEFAULT_TITLES[locale]
                profile.seo_description = locale_payload.get("shortBio", "")
                profile.short_bio = locale_payload.get("shortBio", "")
                profile.long_bio = locale_payload.get("longBio", "")
                profile.availability = locale_payload.get("availability", "")
                if _created:
                    profile.status = LifecycleStatus.PUBLISHED
                    profile.published_at = timezone.now()
                    profile.revision = 1
                profile.full_clean()
                profile.save()

                self._replace_children(profile, locale_payload)
                ContentSeedRecord.objects.update_or_create(
                    content_id=content_id,
                    defaults={"content_type": "profile", "locale": locale,
                              "mapped_model_label": "content.Profile", "mapped_object_id": profile.pk,
                              "payload": locale_payload},
                )
                action = "create" if _created else "overwrite"
                self.stdout.write(
                    f"{content_id}: {action} fields=translation_key,title,body,seo_title,"
                    "seo_description,short_bio,long_bio,availability; "
                    "replace children=skills,experience,education,publications,researchProjects,"
                    "certificates,socials"
                )
                imported_locales.append(locale)
            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write("Dry run: all database changes rolled back.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported profile seed for locales: {', '.join(imported_locales)}"
            )
        )

    def _replace_children(self, profile: Profile, payload: dict):
        profile.skills.all().delete()
        profile.experience_entries.all().delete()
        profile.education_entries.all().delete()
        profile.publication_entries.all().delete()
        profile.research_projects.all().delete()
        profile.certificate_entries.all().delete()
        profile.social_links.all().delete()

        for index, item in enumerate(payload.get("skills", [])):
            ProfileSkill.objects.create(
                profile=profile,
                ordering=index,
                category=item["category"],
                name=item["name"],
                source=item["source"],
                **_detail_route_kwargs(item),
            )
        for index, item in enumerate(payload.get("experience", [])):
            ProfileExperience.objects.create(
                profile=profile,
                ordering=index,
                organization=item["organization"],
                role=item["role"],
                period=item["period"],
                location=item.get("location", ""),
                website=item.get("website", ""),
                bullets=item.get("bullets", []),
                **_detail_route_kwargs(item),
            )
        for index, item in enumerate(payload.get("education", [])):
            ProfileEducation.objects.create(
                profile=profile,
                ordering=index,
                institution=item["institution"],
                degree=item["degree"],
                field=item["field"],
                period=item["period"],
                gpa=item.get("gpa", ""),
                thesis=item.get("thesis", ""),
                **_detail_route_kwargs(item),
            )
        for index, item in enumerate(payload.get("publications", [])):
            ProfilePublication.objects.create(
                profile=profile,
                ordering=index,
                title=item["title"],
                status=item["status"],
                **_detail_route_kwargs(item),
            )
        for index, item in enumerate(payload.get("researchProjects", [])):
            ProfileResearchProject.objects.create(
                profile=profile,
                ordering=index,
                title=item["title"],
                summary=item["summary"],
                url=item.get("url", ""),
                link_label=item.get("linkLabel", ""),
                **_detail_route_kwargs(item),
            )
        for index, item in enumerate(payload.get("certificates", [])):
            ProfileCertificate.objects.create(
                profile=profile,
                ordering=index,
                name=item["name"],
                detail=item.get("detail", ""),
                **_detail_route_kwargs(item),
            )
        for index, item in enumerate(payload.get("socials", [])):
            ProfileSocialLink.objects.create(
                profile=profile,
                ordering=index,
                platform=item["platform"],
                url=item["url"],
            )
