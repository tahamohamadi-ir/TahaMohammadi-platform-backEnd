"""Public projection helpers extracted from the Ninja public API (S1).

Keep published-only rules here so routers stay schema/DTO thin. Do not expose
drafts, private media, or preview URLs through these helpers.
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.content.html_sanitize import sanitize_html


def sanitize_public_richtext(raw: str) -> str:
    """Re-sanitize rich HTML for public projection (local allowlist; ADR-0022)."""
    return sanitize_html(raw)


def published_for_locale(queryset: QuerySet, locale: str) -> QuerySet:
    """Filter a content queryset to ``.public()`` rows for one locale.

    Callers must start from a manager that already implements ``public()``
    (published-only). This helper does not invent visibility rules.
    """
    return queryset.public().filter(locale=locale)
