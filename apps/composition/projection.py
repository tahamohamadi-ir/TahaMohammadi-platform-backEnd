"""Public published-only projection of a story composition document."""

from __future__ import annotations

import html as html_lib
import re

from django.conf import settings

from apps.composition.blocks import KIND_STORY
from apps.composition.models import CompositionPage
from apps.content.html_sanitize import sanitize_html
from apps.media.models import Media

_MATH_TAG = re.compile(r"<\s*math\b", re.IGNORECASE)

_MEDIA_KEYS = ("mediaId", "mediaIds", "beforeMediaId", "afterMediaId")


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
            if isinstance(media_id, int):
                ids.add(media_id)
            elif isinstance(media_id, str) and media_id.isdigit():
                ids.add(int(media_id))
            media_ids = settings.get("mediaIds")
            if isinstance(media_ids, list):
                for item in media_ids:
                    if isinstance(item, int):
                        ids.add(item)
                    elif isinstance(item, str) and item.isdigit():
                        ids.add(int(item))
            for media_key in ("beforeMediaId", "afterMediaId"):
                media_id = settings.get(media_key)
                if isinstance(media_id, int):
                    ids.add(media_id)
                elif isinstance(media_id, str) and media_id.isdigit():
                    ids.add(int(media_id))
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


def _project_settings(block_type: str, settings: dict, media_map: dict[int, dict]) -> dict:
    projected: dict = {}
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
        elif key in ("body", "text", "caption", "source", "label") and isinstance(value, str):
            projected[key] = sanitize_story_text(value) if key in ("body",) else value
        elif key not in _MEDIA_KEYS:
            projected[key] = value
    for media_key, out_key in (
        ("mediaId", "media"),
        ("beforeMediaId", "beforeMedia"),
        ("afterMediaId", "afterMedia"),
    ):
        media_id = settings.get(media_key)
        pk = None
        if isinstance(media_id, int):
            pk = media_id
        elif isinstance(media_id, str) and media_id.isdigit():
            pk = int(media_id)
        if pk is not None and pk in media_map:
            projected[out_key] = media_map[pk]
    media_ids = settings.get("mediaIds")
    if isinstance(media_ids, list):
        items = []
        for item in media_ids:
            item_pk = item if isinstance(item, int) else None
            if isinstance(item, str) and item.isdigit():
                item_pk = int(item)
            if item_pk is not None and item_pk in media_map:
                items.append(media_map[item_pk])
        projected["media"] = items
    return projected


def public_story_document(page: CompositionPage | None, locale: str, request=None) -> dict | None:
    """Return a published, locale-matching story tree, or None for fallback HTML."""
    if page is None:
        return None
    if page.kind != KIND_STORY:
        return None
    if page.status != "published":
        return None
    if page.locale != locale:
        return None
    media_map = _public_media_map(request, _collect_media_ids(page))
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
                        block.block_type, block.settings or {}, media_map
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
