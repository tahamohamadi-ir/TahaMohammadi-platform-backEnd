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
    records_preserved: int = 0
    changes: list[str] = field(default_factory=list)
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


@dataclass
class MappingContext:
    overwrite: bool = False
    settings_writable: bool = False
    existing_profile_ids: set[int] = field(default_factory=set)
    seed_row: ContentSeedRecord | None = None
    changes: list[str] = field(default_factory=list)

    def upsert(self, model, *, defaults, **lookup):
        existing = None
        if self.seed_row and self.seed_row.mapped_model_label == model._meta.label:
            existing = model.objects.filter(pk=self.seed_row.mapped_object_id).first()
        if existing is None:
            existing = model.objects.filter(**lookup).first()
        if existing is not None and not self.overwrite:
            return existing, False
        if existing is None:
            obj, created = model.objects.get_or_create(defaults=defaults, **lookup)
            self.changes.append(f"{model._meta.label} create fields={','.join(sorted(defaults))}")
            return obj, created
        # Refresh content only; seed imports never transition an existing publication.
        changed = []
        for key, value in defaults.items():
            if key in {"status", "published_at"}:
                continue
            if getattr(existing, key) != value:
                setattr(existing, key, value)
                changed.append(key)
        if changed:
            existing.save(update_fields=[*changed, "updated_at"]
                          if hasattr(existing, "updated_at") else changed)
            self.changes.append(f"{model._meta.label} update fields={','.join(sorted(changed))}")
        return existing, False


def _ensure_profile(locale: str) -> Profile:
    # Child imports must not reset their parent's publication state.
    profile, _ = Profile.objects.get_or_create(
        locale=locale,
        slug="about",
        defaults={
            "title": "About" if locale == Locale.EN else "درباره",
            **_draft_defaults(),
        },
    )
    return profile


def apply_seed_settings(
    settings_path: Path,
    *,
    overwrite: bool = False,
    existing_before_import: bool | None = None,
) -> SiteSettings:
    """Apply supplement defaults where SiteSettings has matching fields.

    The raw policy payload is also persisted (BACKEND-211 / ADMIN-281) so the
    site settings admin can label seed-managed surfaces instead of guessing.
    """
    settings_row, created = SiteSettings.objects.get_or_create(site_key="default")
    if not settings_path.exists():
        return settings_row

    payload = load_json(settings_path)
    prior_policy = settings_row.seed_policy
    settings_row.seed_policy = payload
    settings_is_customized = (
        settings_row.brand_name != "Taha Mohammadi"
        or (settings_row.contact_phone and settings_row.contact_phone != "+98 910 235 5374")
        or prior_policy is not None
    )
    if existing_before_import is None:
        should_apply_defaults = True
    else:
        should_apply_defaults = overwrite or not settings_is_customized

    if should_apply_defaults:
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
    overwrite: bool = False,
) -> ContentSeedRecord:
    status = record.get("status") or {}
    content_id = record["content_id"]
    content_type = record["content_type"]
    save_record = (ContentSeedRecord.objects.update_or_create if overwrite
                   else ContentSeedRecord.objects.get_or_create)
    obj, created = save_record(
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
    if not created and not overwrite:
        stats.records_preserved += 1
    return obj


def map_typed_model(
    record: dict[str, Any],
    seed_row: ContentSeedRecord,
    stats: ImportStats,
    context: MappingContext,
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

    model_label, object_id = mapper(record, context)
    if model_label and object_id:
        seed_row.mapped_model_label = model_label
        seed_row.mapped_object_id = object_id
        seed_row.save(update_fields=["mapped_model_label", "mapped_object_id", "updated_at"])
        stats.typed_mapped += 1
    else:
        stats.typed_skipped += 1


def _map_profile_identity(
    record: dict[str, Any], context: MappingContext
) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    data = record.get("data") or {}
    settings_row = SiteSettings.get_singleton()
    if context.settings_writable and data.get("name"):
        settings_row.brand_name = str(data["name"])[:200]
    if context.settings_writable and data.get("headline"):
        settings_row.tagline = str(data["headline"])[:500]
    if context.settings_writable:
        settings_row.save()
        context.changes.append("siteconfig.SiteSettings identity fields=brand_name,tagline")

    landing, _ = context.upsert(Landing,
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


def _map_profile_bio(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    profile, _ = context.upsert(Profile,
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


def _map_research_statement(
    record: dict[str, Any], context: MappingContext
) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = context.upsert(ResearchStatement,
        locale=locale,
        slug=record.get("slug") or "research-statement",
        defaults={
            "title": record.get("title") or "Research Statement",
            "body": record.get("body_markdown") or "",
            **_draft_defaults(),
        },
    )
    return "content.ResearchStatement", obj.pk


def _map_research_focus(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = context.upsert(ResearchTopic,
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


def _map_project(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    code_url = _repository_url(record)
    obj, _ = context.upsert(Project,
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


def _map_writing(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = context.upsert(Article,
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


def _map_education(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
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
    obj, _ = context.upsert(ProfileEducation,
        profile=profile,
        slug=record.get("slug") or "",
        defaults=defaults,
    )
    return "content.ProfileEducation", obj.pk


def _map_experience(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    role, organization = _split_role_organization(record.get("title") or "")
    profile = _ensure_profile(locale)
    obj, _ = context.upsert(ProfileExperience,
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


def _map_credential(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    data = record.get("data") or {}
    profile = _ensure_profile(locale)
    detail = data.get("recorded_completion") or record.get("excerpt") or ""
    obj, _ = context.upsert(ProfileCertificate,
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


def _map_availability(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    profile = _ensure_profile(locale)
    if context.overwrite or profile.pk not in context.existing_profile_ids:
        profile.availability = record.get("body_markdown") or record.get("excerpt") or ""
        profile.save(update_fields=["availability"])
        context.changes.append("content.Profile update fields=availability")
    return "content.Profile", profile.pk


def _map_contact_link(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    content_id = record.get("content_id") or ""
    url = _contact_url(record)
    settings_row = SiteSettings.get_singleton()
    if not context.settings_writable and content_id != "contact.github":
        return "siteconfig.SiteSettings", settings_row.pk
    if content_id == "contact.email":
        settings_row.contact_email = re.sub(r"^mailto:", "", url, flags=re.I)
    elif content_id == "contact.linkedin":
        settings_row.contact_linkedin = url
    elif content_id == "contact.orcid":
        settings_row.contact_orcid = url
    elif content_id == "contact.github":
        for locale in (Locale.EN, Locale.FA):
            profile = _ensure_profile(locale)
            context.upsert(ProfileSocialLink,
                profile=profile,
                platform="GitHub",
                defaults={"url": url, "ordering": 0},
            )
        return "siteconfig.SiteSettings", settings_row.pk
    elif content_id == "contact.employer":
        data = record.get("data") or {}
        settings_row.contact_employer = data.get("employer") or record.get("title") or ""
        settings_row.contact_employer_url = url
    else:
        return "", None
    settings_row.save()
    context.changes.append(f"siteconfig.SiteSettings contact fields={content_id}")
    return "siteconfig.SiteSettings", settings_row.pk


def _map_route_copy(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
    locale = _locale_or_none(record)
    if locale is None:
        return "", None
    obj, _ = context.upsert(Landing,
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


def _map_seed_only(record: dict[str, Any], context: MappingContext) -> tuple[str, int | None]:
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
    overwrite_ids: set[str] | None = None,
    dry_run: bool = False,
) -> ImportStats:
    payload = load_json(seed_path)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Seed package must contain a top-level 'records' array.")
    records = [r for r in records if isinstance(r, dict) and r.get("content_id")]
    overwrite_ids = set(overwrite_ids or ())
    known_ids = {record["content_id"] for record in records} | {"site.settings"}
    unknown = overwrite_ids - known_ids
    if unknown:
        raise ValueError(f"Unknown overwrite content IDs: {', '.join(sorted(unknown))}")
    package_version = str(payload.get("package_version") or "")
    stats = ImportStats()
    settings_path = settings_path or default_settings_path()
    settings_existed = SiteSettings.objects.filter(site_key="default").exists()
    profiles_existed = set(Profile.objects.values_list("pk", flat=True))
    apply_seed_settings(
        settings_path,
        overwrite="site.settings" in overwrite_ids,
        existing_before_import=settings_existed,
    )
    settings_action = "preserve" if settings_existed else "create"
    if "site.settings" in overwrite_ids:
        settings_action = "overwrite policy/contact visibility/download settings"
    stats.changes.append(f"site.settings: {settings_action}")

    for record in records:
        content_id = record["content_id"]
        overwrite = content_id in overwrite_ids
        existed = ContentSeedRecord.objects.filter(content_id=content_id).exists()
        seed_row = upsert_seed_record(record, package_version=package_version,
                                      stats=stats, overwrite=overwrite)
        if existed and not overwrite:
            # A mapped record may have been renamed or removed by its owner.
            # Never resurrect it or its deleted children from the old seed.
            stats.typed_skipped += 1
            stats.changes.append(f"{content_id}: preserve")
            continue
        context = MappingContext(
            overwrite=overwrite,
            settings_writable=not settings_existed or overwrite,
            existing_profile_ids=profiles_existed,
            seed_row=seed_row,
        )
        map_typed_model(record, seed_row, stats, context)
        action = "overwrite" if existed else "create metadata"
        stats.changes.append(f"{content_id}: {action}; " + ("; ".join(context.changes)
                             or "typed content preserved/unmapped"))
    if dry_run:
        transaction.set_rollback(True)
    return stats
