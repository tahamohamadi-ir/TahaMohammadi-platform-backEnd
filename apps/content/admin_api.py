from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.content.models import LifecycleStatus, Locale, Profile
from apps.content.profile_api import (
    parse_profile_payload,
    replace_profile_content,
    resolve_translation_status,
    serialize_profile_detail,
    validate_profile_payload,
)
from apps.security.models import AuditLog

PROFILE_PREFETCH = (
    "skills",
    "experience_entries__story__sections__blocks",
    "education_entries",
    "publication_entries",
    "research_projects",
    "certificate_entries",
    "social_links",
)


def _json_error(status: int, code: str, detail: str, **extra) -> JsonResponse:
    payload = {"code": code, "detail": detail}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _require_admin_session(request):
    if not request.user.is_authenticated:
        return _json_error(401, "AUTH_REQUIRED", "Authentication is required.")
    if not request.user.is_staff:
        return _json_error(403, "FORBIDDEN", "Staff access is required.")
    if getattr(request.user, "otp_device", None) is None:
        return _json_error(403, "OTP_REQUIRED", "A verified TOTP session is required.")
    return None


def _serialize_admin_profile(profile: Profile) -> dict[str, object]:
    data = serialize_profile_detail(profile)
    data["status"] = profile.status
    data["revision"] = profile.revision
    data["translationStatus"] = resolve_translation_status(profile)
    return data


@require_http_methods(["GET", "PUT"])
def admin_profile_detail(request, locale: str, slug: str):
    auth_error = _require_admin_session(request)
    if auth_error is not None:
        return auth_error

    profile = (
        Profile.objects.filter(locale=locale, slug=slug)
        .prefetch_related(*PROFILE_PREFETCH)
        .first()
    )
    if profile is None:
        return _json_error(404, "NOT_FOUND", "Profile not found.")

    if request.method == "GET":
        return JsonResponse(_serialize_admin_profile(profile))

    expected_revision = request.headers.get("If-Match", "").strip().strip('"')
    if not expected_revision:
        return _json_error(
            428,
            "PRECONDITION_REQUIRED",
            "If-Match header with the current revision is required.",
        )
    if expected_revision != str(profile.revision):
        AuditLog.objects.create(
            user=request.user,
            action="admin.profile.conflict",
            model_name="profile",
            object_id=str(profile.pk),
            ip=(request.META.get("REMOTE_ADDR") or "")[:45],
            detail=(
                f"profile {profile.locale}/{profile.slug} "
                f"stale={expected_revision} current={profile.revision}"
            ),
        )
        return _json_error(
            409,
            "REVISION_CONFLICT",
            "The profile has changed since it was loaded.",
            currentRevision=profile.revision,
        )

    try:
        payload = validate_profile_payload(
            parse_profile_payload(request.body),
            locale=locale,
        )
        profile = replace_profile_content(
            profile,
            payload,
            actor=request.user,
            ip=request.META.get("REMOTE_ADDR") or "",
        )
    except ValidationError as exc:
        message = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
        return JsonResponse(
            {"code": "VALIDATION_ERROR", "detail": message},
            status=400,
        )

    return JsonResponse(_serialize_admin_profile(profile))


@require_http_methods(["POST"])
def admin_profile_create_sibling(request, locale: str, slug: str, target_locale: str):
    auth_error = _require_admin_session(request)
    if auth_error is not None:
        return auth_error

    if target_locale not in Locale.values:
        return _json_error(404, "NOT_FOUND", "Locale not found.")
    if target_locale == locale:
        return _json_error(
            400,
            "INVALID_TARGET_LOCALE",
            "The sibling locale must be different from the current locale.",
        )

    source_profile = Profile.objects.filter(locale=locale, slug=slug).first()
    if source_profile is None:
        return _json_error(404, "NOT_FOUND", "Profile not found.")

    existing_sibling = Profile.objects.filter(
        translation_key=source_profile.translation_key,
        locale=target_locale,
    ).first()
    if existing_sibling is not None:
        return _json_error(
            409,
            "PROFILE_LOCALE_EXISTS",
            "The sibling locale already exists for this profile family.",
            editorUrl=f"/admin/content/profile/{existing_sibling.pk}",
        )

    conflicting_slug = Profile.objects.filter(
        locale=target_locale,
        slug=source_profile.slug,
    ).first()
    if conflicting_slug is not None:
        return _json_error(
            409,
            "SLUG_CONFLICT",
            "That locale already has a different profile using this slug.",
        )

    try:
        with transaction.atomic():
            created_profile = Profile.objects.create(
                locale=target_locale,
                slug=source_profile.slug,
                title="",
                body="",
                seo_title="",
                seo_description="",
                short_bio="",
                long_bio="",
                availability="",
                status=LifecycleStatus.DRAFT,
                translation_key=source_profile.translation_key,
                published_at=None,
            )
            Profile.objects.filter(pk=created_profile.pk).update(updated_at=source_profile.updated_at)
    except IntegrityError:
        return _json_error(
            409,
            "PROFILE_LOCALE_EXISTS",
            "The sibling locale already exists for this profile family.",
        )

    created_profile.refresh_from_db()

    AuditLog.objects.create(
        user=request.user,
        action="admin.profile.sibling_created",
        model_name="profile",
        object_id=str(created_profile.pk),
        ip=(request.META.get("REMOTE_ADDR") or "")[:45],
        detail=(
            f"profile family {source_profile.translation_key} "
            f"source={source_profile.locale}/{source_profile.slug} "
            f"created={created_profile.locale}/{created_profile.slug}"
        ),
    )

    return JsonResponse(
        {
            "editorUrl": f"/admin/content/profile/{created_profile.pk}",
            "profile": _serialize_admin_profile(
                Profile.objects.filter(pk=created_profile.pk).prefetch_related(*PROFILE_PREFETCH).get()
            ),
        },
        status=201,
    )
