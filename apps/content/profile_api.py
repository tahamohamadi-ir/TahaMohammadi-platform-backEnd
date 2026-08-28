from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.composition.blocks import KIND_STORY
from apps.composition.models import CompositionPage
from apps.composition.projection import public_story_document
from apps.content.models import (
    LifecycleStatus,
    Profile,
    ProfileCertificate,
    ProfileEducation,
    ProfileExperience,
    ProfilePublication,
    ProfileResearchProject,
    ProfileSkill,
    ProfileSocialLink,
)
from apps.security.models import AuditLog

TRANSLATION_STATUS_MISSING = "MISSING"
TRANSLATION_STATUS_INCOMPLETE = "INCOMPLETE"
TRANSLATION_STATUS_COMPLETE = "COMPLETE"
TRANSLATION_STATUS_OUTDATED = "OUTDATED"


def _strip(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_string(payload: dict[str, Any], field: str) -> str:
    value = _strip(payload.get(field))
    if not value:
        raise ValidationError({field: "This field is required."})
    return value


def _optional_string(payload: dict[str, Any], field: str) -> str:
    return _strip(payload.get(field))


def _require_list(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValidationError({field: "This field must be a list."})
    return value


def _validate_bullets(payload: dict[str, Any], field: str = "bullets") -> list[str]:
    bullets = payload.get(field, [])
    if not isinstance(bullets, list) or not all(_strip(item) for item in bullets):
        raise ValidationError({field: "Bullets must be a list of non-empty strings."})
    return [_strip(item) for item in bullets]


LATIN_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _optional_latin_slug(payload: dict[str, Any], field: str = "slug") -> str:
    value = _strip(payload.get(field))
    if value and not LATIN_SLUG_PATTERN.fullmatch(value):
        raise ValidationError(
            {field: "Slug must use lowercase Latin letters, numbers, and hyphens."}
        )
    return value


def _optional_translation_key(payload: dict[str, Any]) -> uuid.UUID | None:
    value = _strip(payload.get("translationKey")) or _strip(payload.get("translation_key"))
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(
            {"translationKey": "translationKey must be a valid UUID."}
        ) from exc


def _optional_detail_body(payload: dict[str, Any]) -> str:
    return _optional_string(payload, "detailBody") or _optional_string(payload, "detail_body")


def _optional_story_fk(payload: dict[str, Any], *, locale: str) -> CompositionPage | None:
    raw = payload.get("storyId", payload.get("story_id"))
    if raw in (None, ""):
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"storyId": "storyId must be an integer."}) from exc
    page = CompositionPage.objects.filter(pk=pk).first()
    if page is None:
        raise ValidationError({"storyId": "Invalid story composition reference."})
    if getattr(page, "kind", None) != KIND_STORY:
        raise ValidationError({"storyId": "storyId must reference a story composition."})
    if page.locale != locale:
        raise ValidationError({"storyId": "storyId locale must match the profile locale."})
    return page


def _validate_detail_route_fields(
    payload: dict[str, Any],
    *,
    locale: str | None = None,
    allow_story: bool = False,
) -> dict[str, Any]:
    slug = _optional_latin_slug(payload)
    detail_body = _optional_detail_body(payload)
    translation_key = _optional_translation_key(payload)
    if detail_body and not slug:
        raise ValidationError({"slug": "Slug is required when detailBody is provided."})
    fields: dict[str, Any] = {
        "slug": slug,
        "detail_body": detail_body,
        "translation_key": translation_key,
    }
    if allow_story:
        if locale is None:
            raise ValidationError({"storyId": "Profile locale is required for storyId."})
        fields["story"] = _optional_story_fk(payload, locale=locale)
        if fields["story"] is not None and not slug:
            raise ValidationError({"slug": "Slug is required when storyId is provided."})
    return fields


def _serialize_detail_route_fields(row: Any, *, locale: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if row.slug:
        data["slug"] = row.slug
    if row.translation_key:
        data["translationKey"] = str(row.translation_key)
    if row.detail_body:
        data["detailBody"] = row.detail_body
    story = getattr(row, "story", None)
    if story is not None:
        data["storyId"] = story.pk
        if locale:
            projected = public_story_document(story, locale)
            if projected is not None:
                data["story"] = projected
    return data


def serialize_profile_summary(profile: Profile) -> dict[str, Any]:
    return {
        "locale": profile.locale,
        "slug": profile.slug,
        "title": profile.title,
        "seoTitle": profile.seo_title,
        "seoDescription": profile.seo_description,
        "shortBio": profile.short_bio,
        "longBio": profile.long_bio,
        "availability": profile.availability,
        "publishedAt": profile.published_at.isoformat() if profile.published_at else None,
        "availableLocales": profile.available_locales(),
    }


def serialize_profile_detail(profile: Profile) -> dict[str, Any]:
    data = serialize_profile_summary(profile)
    data["skills"] = [
        {
            "category": row.category,
            "name": row.name,
            "source": row.source,
            **_serialize_detail_route_fields(row),
        }
        for row in profile.skills.all()
    ]
    data["experience"] = [
        {
            "organization": row.organization,
            "role": row.role,
            "period": row.period,
            "location": row.location,
            "website": row.website,
            "bullets": row.bullets,
            **_serialize_detail_route_fields(row, locale=profile.locale),
        }
        for row in profile.experience_entries.all()
    ]
    data["education"] = [
        {
            "institution": row.institution,
            "degree": row.degree,
            "field": row.field,
            "period": row.period,
            "gpa": row.gpa,
            "thesis": row.thesis,
            **_serialize_detail_route_fields(row),
        }
        for row in profile.education_entries.all()
    ]
    data["publications"] = [
        {
            "title": row.title,
            "status": row.status,
            **_serialize_detail_route_fields(row),
        }
        for row in profile.publication_entries.all()
    ]
    data["researchProjects"] = [
        {
            "title": row.title,
            "summary": row.summary,
            "url": row.url,
            "linkLabel": row.link_label,
            **_serialize_detail_route_fields(row),
        }
        for row in profile.research_projects.all()
    ]
    data["certificates"] = [
        {
            "name": row.name,
            "detail": row.detail,
            **_serialize_detail_route_fields(row),
        }
        for row in profile.certificate_entries.all()
    ]
    data["socials"] = [
        {"platform": row.platform, "url": row.url}
        for row in profile.social_links.all()
    ]
    return data


def build_translation_unavailable(
    locale: str,
    slug: str,
    available_locales: Sequence[str],
) -> dict[str, Any]:
    return {
        "code": "TRANSLATION_UNAVAILABLE",
        "detail": "The requested translation is not available.",
        "locale": locale,
        "slug": slug,
        "availableLocales": sorted(set(available_locales)),
    }


def resolve_translation_status(profile: Profile) -> dict[str, Any]:
    alternatives = list(
        Profile.objects.filter(translation_key=profile.translation_key)
        .exclude(pk=profile.pk)
        .order_by("locale")
    )
    if not alternatives:
        return {
            "status": TRANSLATION_STATUS_MISSING,
            "availableLocales": [profile.locale],
            "alternateSlug": "",
            "alternateLocale": "",
        }

    alternate = alternatives[0]
    available_locales = sorted({profile.locale, *(row.locale for row in alternatives)})
    required_root_fields = [
        alternate.title,
        alternate.slug,
        alternate.short_bio,
        alternate.availability,
    ]
    has_content = any(
        (
            alternate.skills.exists(),
            alternate.experience_entries.exists(),
            alternate.education_entries.exists(),
            alternate.publication_entries.exists(),
            alternate.research_projects.exists(),
            alternate.certificate_entries.exists(),
            alternate.social_links.exists(),
        )
    )
    if not all(_strip(value) for value in required_root_fields) or not has_content:
        status = TRANSLATION_STATUS_INCOMPLETE
    elif alternate.updated_at < profile.updated_at:
        status = TRANSLATION_STATUS_OUTDATED
    else:
        status = TRANSLATION_STATUS_COMPLETE

    return {
        "status": status,
        "availableLocales": available_locales,
        "alternateSlug": alternate.slug,
        "alternateLocale": alternate.locale,
    }


def parse_profile_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError({"body": "Invalid JSON body."}) from exc

    if not isinstance(payload, dict):
        raise ValidationError({"body": "JSON body must be an object."})
    return payload


def validate_profile_payload(
    payload: dict[str, Any],
    *,
    locale: str,
) -> dict[str, Any]:
    status = _optional_string(payload, "status") or LifecycleStatus.DRAFT
    if status not in LifecycleStatus.values:
        raise ValidationError({"status": "Unsupported lifecycle status."})
    if locale not in ("fa", "en"):
        raise ValidationError({"locale": "Unsupported locale."})

    validated = {
        "title": _require_string(payload, "title"),
        "slug": _require_string(payload, "slug"),
        "status": status,
        "seo_title": _optional_string(payload, "seoTitle"),
        "seo_description": _optional_string(payload, "seoDescription"),
        "short_bio": _require_string(payload, "shortBio"),
        "long_bio": _optional_string(payload, "longBio"),
        "availability": _require_string(payload, "availability"),
        "skills": [],
        "experience": [],
        "education": [],
        "publications": [],
        "research_projects": [],
        "certificates": [],
        "socials": [],
    }

    for item in _require_list(payload, "skills"):
        if not isinstance(item, dict):
            raise ValidationError({"skills": "Each item must be an object."})
        validated["skills"].append(
            {
                "category": _require_string(item, "category"),
                "name": _require_string(item, "name"),
                "source": _require_string(item, "source"),
                **_validate_detail_route_fields(item),
            }
        )

    for item in _require_list(payload, "experience"):
        if not isinstance(item, dict):
            raise ValidationError({"experience": "Each item must be an object."})
        validated["experience"].append(
            {
                "organization": _require_string(item, "organization"),
                "role": _require_string(item, "role"),
                "period": _require_string(item, "period"),
                "location": _optional_string(item, "location"),
                "website": _optional_string(item, "website"),
                "bullets": _validate_bullets(item),
                **_validate_detail_route_fields(item, locale=locale, allow_story=True),
            }
        )

    for item in _require_list(payload, "education"):
        if not isinstance(item, dict):
            raise ValidationError({"education": "Each item must be an object."})
        validated["education"].append(
            {
                "institution": _require_string(item, "institution"),
                "degree": _require_string(item, "degree"),
                "field": _require_string(item, "field"),
                "period": _require_string(item, "period"),
                "gpa": _optional_string(item, "gpa"),
                "thesis": _optional_string(item, "thesis"),
                **_validate_detail_route_fields(item),
            }
        )

    for item in _require_list(payload, "publications"):
        if not isinstance(item, dict):
            raise ValidationError({"publications": "Each item must be an object."})
        validated["publications"].append(
            {
                "title": _require_string(item, "title"),
                "status": _require_string(item, "status"),
                **_validate_detail_route_fields(item),
            }
        )

    for item in _require_list(payload, "researchProjects"):
        if not isinstance(item, dict):
            raise ValidationError({"researchProjects": "Each item must be an object."})
        validated["research_projects"].append(
            {
                "title": _require_string(item, "title"),
                "summary": _require_string(item, "summary"),
                "url": _optional_string(item, "url"),
                "link_label": _optional_string(item, "linkLabel"),
                **_validate_detail_route_fields(item),
            }
        )

    for item in _require_list(payload, "certificates"):
        if not isinstance(item, dict):
            raise ValidationError({"certificates": "Each item must be an object."})
        validated["certificates"].append(
            {
                "name": _require_string(item, "name"),
                "detail": _optional_string(item, "detail"),
                **_validate_detail_route_fields(item),
            }
        )

    for item in _require_list(payload, "socials"):
        if not isinstance(item, dict):
            raise ValidationError({"socials": "Each item must be an object."})
        validated["socials"].append(
            {
                "platform": _require_string(item, "platform"),
                "url": _require_string(item, "url"),
            }
        )

    return validated


@transaction.atomic
def replace_profile_content(
    profile: Profile,
    payload: dict[str, Any],
    *,
    actor,
    ip: str,
) -> Profile:
    profile.title = payload["title"]
    profile.slug = payload["slug"]
    profile.status = payload["status"]
    profile.seo_title = payload["seo_title"]
    profile.seo_description = payload["seo_description"]
    profile.short_bio = payload["short_bio"]
    profile.long_bio = payload["long_bio"]
    profile.availability = payload["availability"]
    profile.body = payload["long_bio"] or payload["short_bio"]
    if profile.status == LifecycleStatus.PUBLISHED and profile.published_at is None:
        from django.utils import timezone

        profile.published_at = timezone.now()
    if profile.status != LifecycleStatus.PUBLISHED:
        profile.published_at = None
    profile.revision += 1
    profile.full_clean()
    profile.save()

    profile.skills.all().delete()
    profile.experience_entries.all().delete()
    profile.education_entries.all().delete()
    profile.publication_entries.all().delete()
    profile.research_projects.all().delete()
    profile.certificate_entries.all().delete()
    profile.social_links.all().delete()

    for index, item in enumerate(payload["skills"]):
        ProfileSkill.objects.create(profile=profile, ordering=index, **item)
    for index, item in enumerate(payload["experience"]):
        ProfileExperience.objects.create(profile=profile, ordering=index, **item)
    for index, item in enumerate(payload["education"]):
        ProfileEducation.objects.create(profile=profile, ordering=index, **item)
    for index, item in enumerate(payload["publications"]):
        ProfilePublication.objects.create(profile=profile, ordering=index, **item)
    for index, item in enumerate(payload["research_projects"]):
        ProfileResearchProject.objects.create(profile=profile, ordering=index, **item)
    for index, item in enumerate(payload["certificates"]):
        ProfileCertificate.objects.create(profile=profile, ordering=index, **item)
    for index, item in enumerate(payload["socials"]):
        ProfileSocialLink.objects.create(profile=profile, ordering=index, **item)

    AuditLog.objects.create(
        user=actor,
        action="admin.profile.updated",
        model_name="profile",
        object_id=str(profile.pk),
        ip=ip[:45],
        detail=f"profile {profile.locale}/{profile.slug} revision={profile.revision}",
    )
    return profile
