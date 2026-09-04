"""Owner content seed v1.1 import (BACKEND-060..070).

Imports ``content-records.v1.1-seed.json`` into :class:`ContentSeedRecord` and
maps mappable rows onto existing typed CMS models as ``draft`` only.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db import transaction

from apps.content.models import (
    Article,
    ContentSeedRecord,
    Landing,
    LifecycleStatus,
    Locale,
    Profile,
    ProfileCertificate,
    ProfileEducation,
    ProfileExperience,
    ProfileSocialLink,
    Project,
    ProjectType,
    ResearchStatement,
    ResearchTopic,
)
from apps.siteconfig.models import SiteSettings

logger = logging.getLogger(__name__)

DEFAULT_SEED_FILENAME = "content-records.v1.1-seed.json"
DEFAULT_SETTINGS_FILENAME = "seed-settings.json"
LOCALE_UND = "und"
ADMIN_ONLY_VISIBILITY = "admin-only"
DRAFT_STATUS = LifecycleStatus.DRAFT


@dataclass
class ImportStats:
    records_upserted: int = 0
    records_created: int = 0
    typed_mapped: int = 0
    typed_skipped: int = 0
    skipped_admin_only: int = 0
    by_content_type: dict[str, int] = field(default_factory=dict)

    def bump_record(self, *, created: bool, content_type: str) -> None:
        self.records_upserted += 1
        if created:
            self.records_created += 1
        self.by_content_type[content_type] = self.by_content_type.get(content_type, 0) + 1


def backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def workspace_root() -> Path:
    return backend_root().parent


def default_seed_path() -> Path:
    return (
        workspace_root()
        / "Docs/01-product/owner-content-seed-v1/cms-package"
        / DEFAULT_SEED_FILENAME
    )


def default_settings_path() -> Path:
    return (
        workspace_root()
        / "Docs/01-product/owner-content-seed-v1/cms-package/supplement"
        / DEFAULT_SETTINGS_FILENAME
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _draft_defaults() -> dict[str, Any]:
    return {
        "status": DRAFT_STATUS,
        "published_at": None,
    }


def _seo_fields(record: dict[str, Any]) -> dict[str, str]:
    seo = record.get("seo") or {}
    return {
        "seo_title": (seo.get("title") or record.get("title") or "")[:200],
        "seo_description": (seo.get("description") or record.get("excerpt") or "")[:5000],
    }


def _is_admin_only(record: dict[str, Any]) -> bool:
    status = record.get("status") or {}
    return status.get("visibility") == ADMIN_ONLY_VISIBILITY


def _is_public_empty_state(record: dict[str, Any]) -> bool:
    """Public-safe seed states (seed.empty.*) copy routes / unavailable surfaces.

    Restricted to seed-provided facts: public visibility plus an explicit
    ``data.empty_state`` flag. Admin-only documents never qualify.
    """
    status = record.get("status") or {}
    data = record.get("data") or {}
    return status.get("visibility") == "public" and data.get("empty_state") is True


def _locale_or_none(record: dict[str, Any]) -> str | None:
    locale = record.get("locale")
    if locale in (Locale.EN, Locale.FA):
        return locale
    return None


def _period_label(record: dict[str, Any]) -> str:
    dates = record.get("dates") or {}
    start = dates.get("start")
    end = dates.get("end")
    if start and end:
        return f"{start}–{end}"
    if start:
        return str(start)
    if end:
        return str(end)
    return ""


def _split_role_organization(title: str) -> tuple[str, str]:
    for sep in (" — ", " – ", " - "):
        if sep in title:
            role, organization = title.split(sep, 1)
            return role.strip(), organization.strip()
    return title.strip(), ""


def _repository_url(record: dict[str, Any]) -> str:
    for link in record.get("links") or []:
        if link.get("rel") == "repository" and link.get("url"):
            return str(link["url"])
    return ""


def _contact_url(record: dict[str, Any]) -> str:
    data = record.get("data") or {}
    if data.get("url"):
        return str(data["url"])
    for link in record.get("links") or []:
        if link.get("url"):
            return str(link["url"])
    return record.get("body_markdown") or ""


def _ensure_profile(locale: str) -> Profile:
    profile, _ = Profile.objects.get_or_create(
        locale=locale,
        slug="about",
        defaults={
            "title": "About" if locale == Locale.EN else "درباره",
            **_draft_defaults(),
        },
    )
    if profile.status != DRAFT_STATUS:
        profile.status = DRAFT_STATUS
        profile.published_at = None
        profile.save(update_fields=["status", "published_at"])
    return profile


def apply_seed_settings(settings_path: Path) -> SiteSettings:
    """Apply supplement defaults where SiteSettings has matching fields.

    The raw policy payload is also persisted (BACKEND-211 / ADMIN-281) so the
    site settings admin can label seed-managed surfaces instead of guessing.
    """
    settings_row = SiteSettings.get_singleton()
    if not settings_path.exists():
        return settings_row

    payload = load_json(settings_path)
    settings_row.seed_policy = payload
    if not payload.get("show_phone", False):
        settings_row.contact_phone = ""
        settings_row.contact_phone_intl = ""
    if not payload.get("show_public_city_country", False):
        settings_row.contact_location = ""
    if not payload.get("public_cv_download", False):
        settings_row.current_cv_media = None
    if not payload.get("public_resume_download", False):
        settings_row.current_resume_media = None
    settings_row.contact_form_enabled = not payload.get("contact_form_only", False)
    settings_row.save()
    return settings_row


def upsert_seed_record(
    record: dict[str, Any],
    *,
    package_version: str,
    stats: ImportStats,
) -> ContentSeedRecord:
    status = record.get("status") or {}
    content_id = record["content_id"]
    content_type = record["content_type"]
    obj, created = ContentSeedRecord.objects.update_or_create(
        content_id=content_id,
        defaults={
            "content_type": content_type,
            "locale": record.get("locale") or LOCALE_UND,
            "slug": record.get("slug") or "",
            "title": record.get("title") or "",
            "approval_state": status.get("approval_state") or "",
            "publication_state": status.get("publication_state") or "",
            "translation_state": status.get("translation_state") or "",
            "visibility": status.get("visibility") or "",
            "payload": record,
            "package_version": package_version,
        },
    )
    stats.bump_record(created=created, content_type=content_type)
    return obj


def map_typed_model(
    record: dict[str, Any],
    seed_row: ContentSeedRecord,
    stats: ImportStats,
) -> None:
    if _is_admin_only(record):
        stats.skipped_admin_only += 1
        stats.typed_skipped += 1
        logger.info("Skipping typed mapping for admin-only seed row %s", record["content_id"])
        return

    content_type = record["content_type"]
    mapper = _TYPED_MAPPERS.get(content_type)
    if content_type == "document" and _is_public_empty_state(record):
        mapper = _map_route_copy
    if mapper is None:
        stats.typed_skipped += 1
        return

    model_label, object_id = mapper(record)
    if model_label and object_id:
        seed_row.mapped_model_label = model_label
        seed_row.mapped_object_id = object_id
        seed_row.save(update_fields=["mapped_model_label", "mapped_object_id", "updated_at"])
        stats.typed_mapped += 1
    else:
        stats.typed_skipped += 1


def _map_profile_identity(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    data = record.get("data") or {}
    settings_row = SiteSettings.get_singleton()
    if data.get("name"):
        settings_row.brand_name = str(data["name"])[:200]
    if data.get("headline"):
        settings_row.tagline = str(data["headline"])[:500]
    settings_row.save()

    landing, _ = Landing.objects.update_or_create(
        locale=locale,
        slug="home",
        defaults={
            "title": record.get("title") or data.get("name") or "Home",
            "body": record.get("body_markdown") or "",
            **_draft_defaults(),
            **_seo_fields(record),
        },
    )
    return "content.Landing", landing.pk


def _map_profile_bio(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    profile, _ = Profile.objects.update_or_create(
        locale=locale,
        slug="about",
        defaults={
            "title": record.get("title") or ("About" if locale == Locale.EN else "درباره"),
            "body": record.get("body_markdown") or "",
            "short_bio": record.get("excerpt") or "",
            "long_bio": record.get("body_markdown") or "",
            **_draft_defaults(),
            **_seo_fields(record),
        },
    )
    return "content.Profile", profile.pk


def _map_research_statement(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = ResearchStatement.objects.update_or_create(
        locale=locale,
        slug=record.get("slug") or "research-statement",
        defaults={
            "title": record.get("title") or "Research Statement",
            "body": record.get("body_markdown") or "",
            **_draft_defaults(),
        },
    )
    return "content.ResearchStatement", obj.pk


def _map_research_focus(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = ResearchTopic.objects.update_or_create(
        locale=locale,
        slug=record.get("slug") or "",
        defaults={
            "title": record.get("title") or "",
            "summary": record.get("excerpt") or record.get("body_markdown") or "",
            "motivation": record.get("body_markdown") or "",
            **_draft_defaults(),
        },
    )
    return "content.ResearchTopic", obj.pk


def _map_project(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    code_url = _repository_url(record)
    obj, _ = Project.objects.update_or_create(
        locale=locale,
        slug=record.get("slug") or "",
        defaults={
            "title": record.get("title") or "",
            "objective": record.get("excerpt") or "",
            "methods_summary": record.get("body_markdown") or "",
            "project_type": ProjectType.RESEARCH,
            "code_url": code_url,
            "code_availability": "public" if code_url else "not_applicable",
            **_draft_defaults(),
        },
    )
    return "content.Project", obj.pk


def _map_writing(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = Article.objects.update_or_create(
        locale=locale,
        slug=record.get("slug") or "",
        defaults={
            "title": record.get("title") or "",
            "excerpt": record.get("excerpt") or "",
            "body": record.get("body_markdown") or "",
            **_draft_defaults(),
        },
    )
    return "content.Article", obj.pk


def _map_education(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    data = record.get("data") or {}
    profile = _ensure_profile(locale)
    defaults = {
        "institution": data.get("institution") or "",
        "degree": record.get("title") or "",
        "field": data.get("field") or "",
        "period": _period_label(record),
        "gpa": data.get("gpa") or "",
        "thesis": data.get("thesis_title") or record.get("body_markdown") or "",
        "slug": record.get("slug") or "",
        "detail_body": record.get("body_markdown") or "",
        "ordering": record.get("sort_order") or 0,
    }
    obj, _ = ProfileEducation.objects.update_or_create(
        profile=profile,
        slug=record.get("slug") or "",
        defaults=defaults,
    )
    return "content.ProfileEducation", obj.pk


def _map_experience(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    role, organization = _split_role_organization(record.get("title") or "")
    profile = _ensure_profile(locale)
    obj, _ = ProfileExperience.objects.update_or_create(
        profile=profile,
        slug=record.get("slug") or "",
        defaults={
            "organization": organization,
            "role": role,
            "period": _period_label(record),
            "bullets": [record.get("body_markdown") or record.get("excerpt") or ""],
            "detail_body": record.get("body_markdown") or "",
            "ordering": record.get("sort_order") or 0,
        },
    )
    return "content.ProfileExperience", obj.pk


def _map_credential(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    data = record.get("data") or {}
    profile = _ensure_profile(locale)
    detail = data.get("recorded_completion") or record.get("excerpt") or ""
    obj, _ = ProfileCertificate.objects.update_or_create(
        profile=profile,
        slug=record.get("slug") or "",
        defaults={
            "name": record.get("title") or "",
            "detail": detail,
            "detail_body": record.get("body_markdown") or "",
            "ordering": record.get("sort_order") or 0,
        },
    )
    return "content.ProfileCertificate", obj.pk


def _map_availability(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    profile = _ensure_profile(locale)
    profile.availability = record.get("body_markdown") or record.get("excerpt") or ""
    profile.status = DRAFT_STATUS
    profile.published_at = None
    profile.save(update_fields=["availability", "status", "published_at"])
    return "content.Profile", profile.pk


def _map_contact_link(record: dict[str, Any]) -> tuple[str, int | None]:
    content_id = record.get("content_id") or ""
    url = _contact_url(record)
    settings_row = SiteSettings.get_singleton()
    if content_id == "contact.email":
        settings_row.contact_email = re.sub(r"^mailto:", "", url, flags=re.I)
    elif content_id == "contact.linkedin":
        settings_row.contact_linkedin = url
    elif content_id == "contact.orcid":
        settings_row.contact_orcid = url
    elif content_id == "contact.github":
        for locale in (Locale.EN, Locale.FA):
            profile = _ensure_profile(locale)
            ProfileSocialLink.objects.update_or_create(
                profile=profile,
                platform="GitHub",
                defaults={"url": url, "ordering": 0},
            )
        settings_row.save()
        return "siteconfig.SiteSettings", settings_row.pk
    elif content_id == "contact.employer":
        data = record.get("data") or {}
        settings_row.contact_employer = data.get("employer") or record.get("title") or ""
        settings_row.contact_employer_url = url
    else:
        return "", None
    settings_row.save()
    return "siteconfig.SiteSettings", settings_row.pk


def _map_route_copy(record: dict[str, Any]) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = Landing.objects.update_or_create(
        locale=locale,
        slug=record.get("slug") or "",
        defaults={
            "title": record.get("title") or "",
            "body": record.get("body_markdown") or "",
            **_draft_defaults(),
            **_seo_fields(record),
        },
    )
    return "content.Landing", obj.pk


def _map_seed_only(record: dict[str, Any]) -> tuple[str, int | None]:
    return "", None


_TYPED_MAPPERS = {
    "profile_identity": _map_profile_identity,
    "profile_bio": _map_profile_bio,
    "research_statement": _map_research_statement,
    "research_focus": _map_research_focus,
    "engineering_statement": _map_seed_only,
    "project": _map_project,
    "writing": _map_writing,
    "education": _map_education,
    "experience": _map_experience,
    "credential": _map_credential,
    "availability": _map_availability,
    "contact_link": _map_contact_link,
    "route_copy": _map_route_copy,
    "document": _map_seed_only,
    "legal_notice": _map_seed_only,
}


@transaction.atomic
def import_content_seed_package(
    *,
    seed_path: Path,
    settings_path: Path | None = None,
) -> ImportStats:
    payload = load_json(seed_path)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Seed package must contain a top-level 'records' array.")

    package_version = str(payload.get("package_version") or "")
    stats = ImportStats()
    settings_path = settings_path or default_settings_path()
    apply_seed_settings(settings_path)

    for record in records:
        if not isinstance(record, dict) or not record.get("content_id"):
            continue
        seed_row = upsert_seed_record(record, package_version=package_version, stats=stats)
        map_typed_model(record, seed_row, stats)

    return stats
