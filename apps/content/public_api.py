from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.content.models import Profile
from apps.content.profile_api import (
    build_translation_unavailable,
    serialize_profile_detail,
    serialize_profile_summary,
)

PROFILE_PREFETCH = (
    "skills",
    "experience_entries__story__sections__blocks",
    "education_entries",
    "publication_entries",
    "research_projects",
    "certificate_entries",
    "social_links",
)


@require_GET
def public_profile_list(request, locale: str):
    profiles = (
        Profile.objects.public()
        .filter(locale=locale)
        .prefetch_related(*PROFILE_PREFETCH)
        .order_by("slug")
    )
    return JsonResponse([serialize_profile_summary(profile) for profile in profiles], safe=False)


@require_GET
def public_profile_detail(request, locale: str, slug: str):
    profile = (
        Profile.objects.public()
        .filter(locale=locale, slug=slug)
        .prefetch_related(*PROFILE_PREFETCH)
        .first()
    )
    if profile is not None:
        return JsonResponse(serialize_profile_detail(profile))

    available_locales = list(
        Profile.objects.public()
        .filter(slug=slug)
        .order_by("locale")
        .values_list("locale", flat=True)
    )
    if available_locales:
        return JsonResponse(
            build_translation_unavailable(locale, slug, available_locales),
            status=404,
        )
    return JsonResponse({"detail": "profile not found"}, status=404)
