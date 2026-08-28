from __future__ import annotations

from django.http import Http404
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse

from apps.content.models import Locale, Profile
from apps.content.profile_api import resolve_translation_status, serialize_profile_detail
from apps.security.decorators import staff_otp_required

PROFILE_PREFETCH = (
    "skills",
    "experience_entries",
    "education_entries",
    "publication_entries",
    "research_projects",
    "certificate_entries",
    "social_links",
)

LOCALE_LABELS = {
    Locale.EN: {"code": "EN", "name": "English", "dir": "ltr"},
    Locale.FA: {"code": "FA", "name": "فارسی", "dir": "rtl"},
}


def _locale_toggle(locale: str) -> dict[str, str]:
    return LOCALE_LABELS.get(locale, LOCALE_LABELS[Locale.EN])


def _profile_has_body(profile: Profile) -> bool:
    return bool((profile.short_bio or "").strip() or (profile.long_bio or "").strip())


def _profile_has_section_content(profile: Profile) -> bool:
    return any(
        (
            profile.skills.exists(),
            profile.experience_entries.exists(),
            profile.education_entries.exists(),
            profile.publication_entries.exists(),
            profile.research_projects.exists(),
            profile.certificate_entries.exists(),
            profile.social_links.exists(),
        )
    )


def _profile_completeness(profile: Profile | None) -> dict[str, bool]:
    if profile is None:
        return {"title": False, "slug": False, "body": False, "seo": False}
    return {
        "title": bool((profile.title or "").strip()),
        "slug": bool((profile.slug or "").strip()),
        "body": _profile_has_body(profile) and _profile_has_section_content(profile),
        "seo": bool((profile.seo_title or "").strip() and (profile.seo_description or "").strip()),
    }


def _profile_queryset():
    return Profile.objects.prefetch_related(*PROFILE_PREFETCH)


def _profile_alt_map(profile: Profile) -> dict[str, Profile]:
    siblings = (
        _profile_queryset()
        .filter(translation_key=profile.translation_key)
        .exclude(pk=profile.pk)
        .order_by("locale")
    )
    return {row.locale: row for row in siblings}


def _build_locale_tabs(profile: Profile) -> list[dict[str, object]]:
    current_toggle = _locale_toggle(profile.locale)
    alternates = _profile_alt_map(profile)
    translation_status = resolve_translation_status(profile)
    rows: list[dict[str, object]] = [
        {
            "locale": profile.locale,
            "label": current_toggle["name"],
            "badge": "CURRENT",
            "href": reverse("admin_profile_detail_page", args=[profile.locale, profile.slug]),
            "isCurrent": True,
            "completeness": _profile_completeness(profile),
        }
    ]

    expected_locales = [Locale.EN, Locale.FA]
    for locale in expected_locales:
        if locale == profile.locale:
            continue
        alternate = alternates.get(locale)
        toggle = _locale_toggle(locale)
        rows.append(
            {
                "locale": locale,
                "label": toggle["name"],
                "badge": translation_status["status"] if alternate else "MISSING",
                "href": (
                    reverse("admin_profile_detail_page", args=[alternate.locale, alternate.slug])
                    if alternate
                    else ""
                ),
                "isCurrent": False,
                "completeness": _profile_completeness(alternate),
                "createUrl": (
                    ""
                    if alternate
                    else reverse(
                        "admin_profile_create_sibling",
                        args=[profile.locale, profile.slug, locale],
                    )
                ),
            }
        )
    return rows


def _serialize_admin_profile(profile: Profile) -> dict[str, object]:
    data = serialize_profile_detail(profile)
    data["status"] = profile.status
    data["revision"] = profile.revision
    data["publishedAt"] = profile.published_at.isoformat() if profile.published_at else None
    data["translationStatus"] = resolve_translation_status(profile)
    return data


def _profile_summary_row(profile: Profile) -> dict[str, object]:
    toggle = _locale_toggle(profile.locale)
    translation_status = resolve_translation_status(profile)
    return {
        "title": profile.title,
        "slug": profile.slug,
        "locale": profile.locale,
        "localeLabel": toggle["name"],
        "dir": toggle["dir"],
        "status": profile.status,
        "revision": profile.revision,
        "translationStatus": translation_status["status"],
        "completeness": _profile_completeness(profile),
        "href": reverse("admin_profile_detail_page", args=[profile.locale, profile.slug]),
    }


@staff_otp_required
def profile_index(request):
    profiles = Profile.objects.order_by("locale", "slug")
    rows = [_profile_summary_row(profile) for profile in profiles]
    return render(
        request,
        "admin/profile_index.html",
        {
            "page_title": "Professional admin",
            "page_subtitle": "Same-origin profile editor",
            "header_icon": "user",
            "rows": rows,
            "total_profiles": len(rows),
            "published_profiles": sum(1 for row in rows if row["status"] == "published"),
        },
    )


@staff_otp_required
def profile_detail_page(request, locale: str, slug: str):
    profile = _profile_queryset().filter(locale=locale, slug=slug).first()
    if profile is None:
        raise Http404("Profile not found")

    toggle = _locale_toggle(profile.locale)
    breadcrumbs_items = [
        {"url": "/admin/", "label": "Admin"},
        {"url": reverse("admin_profile_index"), "label": "Profiles"},
        {"label": f"{profile.slug} ({toggle['code']})"},
    ]
    bootstrap = {
        "apiUrl": reverse("admin_profile_detail", args=[profile.locale, profile.slug]),
        "indexUrl": reverse("admin_profile_index"),
        "csrfToken": get_token(request),
        "pageLocale": profile.locale,
        "pageDir": toggle["dir"],
        "profile": _serialize_admin_profile(profile),
        "localeTabs": _build_locale_tabs(profile),
    }
    return render(
        request,
        "admin/profile_detail.html",
        {
            "page_title": profile.title or profile.slug,
            "page_subtitle": f"Profile editor · {toggle['name']}",
            "header_icon": "user",
            "breadcrumbs_items": breadcrumbs_items,
            "bootstrap": bootstrap,
            "chrome_locale": profile.locale,
            "chrome_dir": toggle["dir"],
        },
    )
