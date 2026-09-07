"""Public published-only projection of a story composition document."""

from __future__ import annotations

import html as html_lib
import re

from django.conf import settings
from django.utils.text import slugify

from apps.composition.blocks import KIND_STORY
from apps.composition.models import CompositionPage
from apps.content.html_sanitize import sanitize_html
from apps.media.models import Media

_MATH_TAG = re.compile(r"<\s*math\b", re.IGNORECASE)

_MEDIA_KEYS = ("mediaId", "mediaIds", "beforeMediaId", "afterMediaId")

# Bounded canonical ASCII ID validation aligned with signed 64-bit BigAutoField
# storage and apps/api/record_resolver.py (PRODUCT-INTERFACES-V2 §I04 R1).
_CANONICAL_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$", re.ASCII)
_MAX_CANONICAL_ID = 9223372036854775807


def _coerce_canonical_pk(value) -> int | None:
    """Return canonical PK int, or None for any malformed value (never raises).

    Rejects bools, floats, leading zeros, non-ASCII digits, zero/negative,
    overlong and out-of-range values without ValueError.
    """
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, int):
        if 1 <= value <= _MAX_CANONICAL_ID:
            return value
        return None
    if isinstance(value, str):
        if not _CANONICAL_ID_RE.fullmatch(value):
            return None
        try:
            pk = int(value)
        except ValueError:
            return None
        if pk > _MAX_CANONICAL_ID:
            return None
        return pk
    return None


def sanitize_story_text(raw: str) -> str:
    """Sanitize story text/quote HTML with the same allowlist as articles."""
    return sanitize_html(raw)


def sanitize_math_html(raw: str) -> str:
    """MathML when present; otherwise escaped plain text inside ``<pre>``."""
    value = raw or ""
    if _MATH_TAG.search(value):
        return sanitize_html(value)
    escaped = html_lib.escape(value.strip(), quote=True)
    return f'<pre class="story-math">{escaped}</pre>'


def _public_media_url(request, media: Media) -> str:
    relative = media.file.url
    if request is not None:
        return request.build_absolute_uri(relative)
    media_url = str(settings.MEDIA_URL)
    prefix = media_url if media_url.startswith("/") else f"/{media_url}"
    return prefix.rstrip("/") + "/" + str(media.file.name).lstrip("/")


def _collect_media_ids(page: CompositionPage) -> set[int]:
    ids: set[int] = set()
    for section in page.sections.all():
        if not section.enabled:
            continue
        for block in section.blocks.all():
            if not block.enabled:
                continue
            settings = block.settings or {}
            media_id = settings.get("mediaId")
            pk = _coerce_canonical_pk(media_id)
            if pk is not None:
                ids.add(pk)
            media_ids = settings.get("mediaIds")
            if isinstance(media_ids, list):
                for item in media_ids:
                    item_pk = _coerce_canonical_pk(item)
                    if item_pk is not None:
                        ids.add(item_pk)
            for media_key in ("beforeMediaId", "afterMediaId"):
                media_id = settings.get(media_key)
                pk = _coerce_canonical_pk(media_id)
                if pk is not None:
                    ids.add(pk)
    return ids


def _public_media_map(request, ids: set[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = Media.objects.active_public().filter(pk__in=ids)
    return {
        row.pk: {
            "id": row.pk,
            "url": _public_media_url(request, row),
            "mime": row.mime,
            "alt": row.alt_text,
            "altFa": row.alt_text_fa,
            "altEn": row.alt_text_en,
            "title": row.title,
        }
        for row in rows
    }


def _collect_download_ids(page: CompositionPage) -> set[int]:
    ids: set[int] = set()
    for section in page.sections.all():
        if not section.enabled:
            continue
        for block in section.blocks.all():
            if not block.enabled:
                continue
            settings = block.settings or {}
            dl_id = settings.get("downloadId")
            pk = _coerce_canonical_pk(dl_id)
            if pk is not None:
                ids.add(pk)
    return ids


def _public_download_map(
    request, ids: set[int], locale: str
) -> dict[int, dict]:
    """Published-only download enrichment with gated-delivery preservation.

    Same publication/access policy as the existing download endpoints
    (``Download.objects.public()`` + ``public_media_is_downloadable()`` +
    exact story locale + publication-snapshot fallback). Ineligible downloads
    (restricted/metadata-only/draft/future/inactive/wrong-locale/missing) are
    omitted entirely so no direct storage URL is disclosed. Eligible entries
    keep the existing ``{id,title,slug,url,mime,size}`` shape for no-JS consumers.
    """
    if not ids:
        return {}
    if locale not in ("fa", "en"):
        return {}
    from apps.content.models import Download
    from apps.content.published import resolve_published_target

    res: dict[int, dict] = {}
    for pk in ids:
        if not isinstance(pk, int) or isinstance(pk, bool):
            continue
        if not (1 <= pk <= _MAX_CANONICAL_ID):
            continue
        target = resolve_published_target(Download, "download", pk, locale)
        if target is None:
            continue
        # Exact-locale guard (resolve already filters, belt-and-braces).
        if getattr(target, "locale", None) != locale:
            continue
        try:
            downloadable = target.public_media_is_downloadable()
        except Exception:  # noqa: BLE001 — fail closed on unexpected media state
            continue
        if not downloadable:
            continue
        media = getattr(target, "media", None)
        if media is None:
            continue
        try:
            media_url = _public_media_url(request, media)
        except Exception:  # noqa: BLE001 — fail closed, disclose nothing
            continue
        res[pk] = {
            "id": target.pk,
            "title": getattr(target, "title", ""),
            "slug": getattr(target, "slug", ""),
            "url": media_url,
            "mime": getattr(media, "mime", None),
            "size": getattr(media, "size", None),
        }
    return res


def _filter_public_related_records(records, locale: str) -> list[dict]:
    """Filter story related ``[{family,id}]`` to exact-locale published targets.

    Preserves request order, omits missing/private/draft/archived/other-locale
    references without inventing slugs. IDs are normalized to canonical decimal
    strings per §I03 (``id`` stays a string on the wire).
    """
    if not isinstance(records, list):
        return []
    if locale not in ("fa", "en"):
        return []
    from apps.api.admin_common import CONTENT_RELATED_FAMILIES
    from apps.content.published import FAMILY_TO_ENTITY_KEY, resolve_published_target

    filtered: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        family = item.get("family")
        raw_id = item.get("id")
        if not isinstance(family, str):
            continue
        family_str = family.lower() if isinstance(family, str) else ""
        # Validation allowlist is the 13-family story catalog; resolution uses
        # the shared content registry (includes future lesson/collection) so
        # unknown families are omitted, never linked.
        model = CONTENT_RELATED_FAMILIES.get(family_str)
        if model is None:
            continue
        if isinstance(raw_id, bool):
            continue
        if isinstance(raw_id, int):
            if not (1 <= raw_id <= _MAX_CANONICAL_ID):
                continue
            pk = raw_id
            id_str = str(raw_id)
        elif isinstance(raw_id, str):
            if not _CANONICAL_ID_RE.fullmatch(raw_id):
                continue
            try:
                pk = int(raw_id)
            except ValueError:
                continue
            if pk > _MAX_CANONICAL_ID:
                continue
            id_str = raw_id
        else:
            continue
        try:
            target = resolve_published_target(
                model, FAMILY_TO_ENTITY_KEY.get(family_str, family_str), pk, locale
            )
        except Exception:  # noqa: BLE001 — fail closed, omit on resolver error
            continue
        if target is None:
            continue
        if getattr(target, "locale", None) != locale:
            continue
        filtered.append({"family": family_str, "id": id_str})
    return filtered


def _project_settings(
    block_type: str,
    settings: dict,
    media_map: dict[int, dict],
    download_map: dict[int, dict] | None = None,
    seen_heading_ids: set[str] | None = None,
    locale: str = "",
) -> dict:
    projected: dict = {}
    if download_map is None:
        download_map = {}
    for key, value in settings.items():
        if key == "html" and block_type == "math":
            projected[key] = sanitize_math_html(value if isinstance(value, str) else "")
        elif key == "items" and isinstance(value, list):
            items = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                projected_item = dict(item)
                body = projected_item.get("body")
                if isinstance(body, str):
                    projected_item["body"] = sanitize_story_text(body)
                items.append(projected_item)
            projected[key] = items
        elif key == "records" and block_type == "related":
            # Resolved below with exact-locale published policy; skip verbatim copy.
            continue
        elif key in ("body", "text", "caption", "source", "label") and isinstance(value, str):
            projected[key] = sanitize_story_text(value) if key in ("body",) else value
        elif key not in _MEDIA_KEYS:
            projected[key] = value
    # Related blocks: omit unpublished/missing/other-locale refs, preserve order.
    if block_type == "related":
        projected["records"] = _filter_public_related_records(
            settings.get("records"), locale
        )

    if block_type == "heading":
        text_val = str(settings.get("text") or "").strip()
        raw_slug = slugify(text_val, allow_unicode=True)
        base_id = raw_slug if raw_slug else "heading"
        heading_id = base_id
        if seen_heading_ids is not None:
            counter = 1
            while heading_id in seen_heading_ids:
                heading_id = f"{base_id}-{counter}"
                counter += 1
            seen_heading_ids.add(heading_id)
        projected["id"] = heading_id

    if block_type == "file":
        dl_id = settings.get("downloadId")
        pk = _coerce_canonical_pk(dl_id)
        if pk is not None and pk in download_map:
            projected["download"] = download_map[pk]
            projected["file"] = download_map[pk]

    for media_key, out_key in (
        ("mediaId", "media"),
        ("beforeMediaId", "beforeMedia"),
        ("afterMediaId", "afterMedia"),
    ):
        media_id = settings.get(media_key)
        pk = _coerce_canonical_pk(media_id)
        if pk is not None and pk in media_map:
            projected[out_key] = media_map[pk]
    media_ids = settings.get("mediaIds")
    if isinstance(media_ids, list):
        items = []
        for item in media_ids:
            item_pk = _coerce_canonical_pk(item)
            if item_pk is not None and item_pk in media_map:
                items.append(media_map[item_pk])
        projected["media"] = items
    return projected


def _collect_snapshot_media_ids(snap_sections: list[dict]) -> set[int]:
    ids: set[int] = set()
    for section in snap_sections:
        if not section.get("enabled", True):
            continue
        for block in section.get("blocks", []):
            if not block.get("enabled", True):
                continue
            settings = block.get("settings") or {}
            media_id = settings.get("mediaId")
            pk = _coerce_canonical_pk(media_id)
            if pk is not None:
                ids.add(pk)
            media_ids = settings.get("mediaIds")
            if isinstance(media_ids, list):
                for item in media_ids:
                    item_pk = _coerce_canonical_pk(item)
                    if item_pk is not None:
                        ids.add(item_pk)
            for media_key in ("beforeMediaId", "afterMediaId"):
                media_id = settings.get(media_key)
                pk = _coerce_canonical_pk(media_id)
                if pk is not None:
                    ids.add(pk)
    return ids


def _collect_snapshot_download_ids(snap_sections: list[dict]) -> set[int]:
    ids: set[int] = set()
    for section in snap_sections:
        if not section.get("enabled", True):
            continue
        for block in section.get("blocks", []):
            if not block.get("enabled", True):
                continue
            settings = block.get("settings") or {}
            dl_id = settings.get("downloadId")
            pk = _coerce_canonical_pk(dl_id)
            if pk is not None:
                ids.add(pk)
    return ids


def public_story_document(page: CompositionPage | None, locale: str, request=None) -> dict | None:
    """Return a published, locale-matching story tree, or None for fallback HTML."""
    if page is None:
        return None
    if page.kind != KIND_STORY:
        return None
    if page.locale != locale:
        return None
    if page.status == "archived":
        return None

    from apps.content.models import PublicationSnapshot

    pub_snap = (
        PublicationSnapshot.objects.filter(
            entity_key="composition",
            object_id=page.pk,
        )
        .order_by("-published_at", "-id")
        .first()
    )

    if pub_snap is not None:
        snap_data = pub_snap.snapshot
        if not isinstance(snap_data, dict):
            return None
        if snap_data.get("locale") != locale:
            return None
        snap_sections = snap_data.get("sections", [])
        media_ids = _collect_snapshot_media_ids(snap_sections)
        download_ids = _collect_snapshot_download_ids(snap_sections)
        media_map = _public_media_map(request, media_ids)
        download_map = _public_download_map(request, download_ids, locale)
        seen_heading_ids: set[str] = set()
        sections = []
        for s in snap_sections:
            if not s.get("enabled", True):
                continue
            blocks = []
            for b in s.get("blocks", []):
                if not b.get("enabled", True):
                    continue
                block_type = b.get("blockType", "")
                blocks.append(
                    {
                        "blockType": block_type,
                        "settings": _project_settings(
                            block_type,
                            b.get("settings", {}),
                            media_map,
                            download_map=download_map,
                            seen_heading_ids=seen_heading_ids,
                            locale=locale,
                        ),
                    }
                )
            sections.append(
                {
                    "layout": s.get("layout", "1col"),
                    "ratio": s.get("ratio", ""),
                    "blocks": blocks,
                }
            )
        return {
            "locale": snap_data.get("locale", page.locale),
            "title": snap_data.get("title", page.title),
            "sections": sections,
        }

    if page.status != "published":
        return None

    media_map = _public_media_map(request, _collect_media_ids(page))
    download_map = _public_download_map(request, _collect_download_ids(page), locale)
    seen_heading_ids: set[str] = set()
    sections = []
    for section in page.sections.all():
        if not section.enabled:
            continue
        blocks = []
        for block in section.blocks.all():
            if not block.enabled:
                continue
            blocks.append(
                {
                    "blockType": block.block_type,
                    "settings": _project_settings(
                        block.block_type,
                        block.settings or {},
                        media_map,
                        download_map=download_map,
                        seen_heading_ids=seen_heading_ids,
                        locale=locale,
                    ),
                }
            )
        sections.append(
            {
                "layout": section.layout,
                "ratio": section.ratio,
                "blocks": blocks,
            }
        )
    return {
        "locale": page.locale,
        "title": page.title,
        "sections": sections,
    }
