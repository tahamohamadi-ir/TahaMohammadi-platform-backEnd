"""Build public (or build-time) URLs for Media library rows."""

from __future__ import annotations

from django.conf import settings

from apps.media.models import Media


def public_media_url(media: Media, request=None) -> str:
    """Return an absolute URL when ``request`` is set; otherwise a site-relative path."""
    relative = media.file.url
    if request is not None:
        return request.build_absolute_uri(relative)
    media_url = str(settings.MEDIA_URL)
    prefix = media_url if media_url.startswith("/") else f"/{media_url}"
    return prefix.rstrip("/") + "/" + str(media.file.name).lstrip("/")


def public_media_focal_ref(media: Media) -> dict | None:
    """Focal point as percent floats, or ``None`` when unset/partial (BK-03)."""
    if media.focal_x is None or media.focal_y is None:
        return None
    return {"x": float(media.focal_x), "y": float(media.focal_y)}


def public_media_ref(
    media: Media | None, request=None, *, locale: str | None = None
) -> dict | None:
    """Published-only media payload (plus ``focal`` when set), or ``None``.

    Missing/inactive media -> ``None``; an unset or partial focal point is
    omitted from the payload rather than nulled (empty value => key absent).
    """
    if media is None or not media.is_active:
        return None
    alt = media.alt_text or ""
    if locale == "fa" and (media.alt_text_fa or "").strip():
        alt = media.alt_text_fa
    elif locale == "en" and (media.alt_text_en or "").strip():
        alt = media.alt_text_en
    ref = {
        "url": public_media_url(media, request),
        "alt": alt,
        "mime": media.mime or "",
        "title": media.title or "",
        "size": int(media.size or 0),
    }
    focal = public_media_focal_ref(media)
    if focal is not None:
        ref["focal"] = focal
    return ref
