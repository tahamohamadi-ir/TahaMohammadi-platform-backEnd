"""Record resolver endpoint (PU-03-resolver, ADR-0010, PRODUCT-INTERFACES-V2 §I04 & §I08).

Resolves published graph record IDs (family:id pairs) to canonical record
descriptors (WorkRef) without private-record enumeration.

Rules:
- Exact-locale publication only (no cross-locale leakage).
- Unresolved response does not distinguish private, missing or unpublished records.
- Maximum 50 unique references; preserves request order among results.
- Malformed inputs, leading zeros, non-ASCII digits and out-of-range IDs return HTTP 400.
- Error responses conform to normalized error envelope
  (PRODUCT-INTERFACES-V2 §I08 / ERROR-CONTRACT).
- Safe error messages that do not echo attacker-supplied raw reference payloads.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from django.db import models
from django.utils.html import strip_tags
from ninja import Field, Router, Schema
from ninja.responses import Status

from apps.api.admin_common import GRAPH_RELATED_FAMILIES
from apps.content.models import Course, CreativeWork, Locale
from apps.content.published import FAMILY_TO_ENTITY_KEY, resolve_published_target

RESOLVER_FAMILIES: dict[str, type[models.Model]] = {
    **GRAPH_RELATED_FAMILIES,
    "course": Course,
    "creativework": CreativeWork,
}

ROUTE_FAMILY_MAP: dict[str, str] = {
    "landing": "home",
    "profile": "about",
    "article": "blog",
    "series": "blog/series",
    "researchtopic": "research",
    "researchstatement": "research/statements",
    "project": "projects",
    "publication": "publications",
    "book": "books",
    "talk": "talks",
    "download": "resources",
    "course": "education",
    "creativework": "gallery",
}

MAX_UNIQUE_REFS = 50

# Canonical ASCII positive decimal integer: no leading zeros, max 19 digits (fits signed 64-bit int)
_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$", re.ASCII)
MAX_ID = 9223372036854775807  # Signed 64-bit BigAutoField maximum


class WorkRefOut(Schema):
    """Canonical public reference descriptor for a published record."""

    family: str
    id: str
    locale: str
    slug: str
    title: str
    summary: str
    routeFamily: str
    courseSlug: str | None = None


class UnresolvedRefOut(Schema):
    """Reference that could not be resolved to a published exact-locale record."""

    family: str
    id: str


class RecordResolveOut(Schema):
    """Batch reference resolution response."""

    items: list[WorkRefOut] = Field(default_factory=list)
    unresolved: list[UnresolvedRefOut] = Field(default_factory=list)


class ErrorEnvelopeOut(Schema):
    """Normalized error envelope (PRODUCT-INTERFACES-V2 §I08, ERROR-CONTRACT)."""

    code: str
    message: str
    field_errors: dict[str, list[str]] = Field(default_factory=dict)
    request_id: str


def _make_request_id(request) -> str:
    return getattr(request, "request_id", None) or str(uuid.uuid4())


def _error_response(
    request,
    status: int,
    code: str,
    message: str,
    field_errors: dict[str, list[str]] | None = None,
) -> Status:
    return Status(
        status,
        ErrorEnvelopeOut(
            code=code,
            message=message,
            field_errors=field_errors or {},
            request_id=_make_request_id(request),
        ),
    )


def _extract_summary(family: str, obj: Any) -> str:
    summary = ""
    if family == "article":
        summary = getattr(obj, "excerpt", "") or ""
    elif family == "researchtopic":
        summary = getattr(obj, "summary", "") or ""
    elif family == "researchstatement":
        body = getattr(obj, "body", "") or ""
        summary = strip_tags(body).strip()
    elif family == "project":
        summary = getattr(obj, "objective", "") or ""
    elif family in ("publication", "talk"):
        summary = getattr(obj, "abstract", "") or ""
    elif family in ("book", "download", "course", "creativework", "series"):
        summary = getattr(obj, "description", "") or ""
    elif family == "profile":
        summary = getattr(obj, "short_bio", "") or getattr(obj, "seo_description", "") or ""
    elif family == "landing":
        summary = getattr(obj, "seo_description", "") or ""

    summary = str(summary).strip()
    if not summary:
        summary = str(getattr(obj, "title", "") or "").strip()
    return summary


record_resolver_router = Router()


@record_resolver_router.get(
    "/v1/records/{locale}/resolve",
    response={
        200: RecordResolveOut,
        400: ErrorEnvelopeOut,
        404: ErrorEnvelopeOut,
    },
    summary="Resolve published graph record IDs to canonical record descriptors",
)
def resolve_records(request, locale: str, refs: str = ""):
    """Resolve comma-separated family:id pairs to canonical WorkRef objects.

    Fails closed: 404 for unsupported locales, 400 for malformed input or >50
    unique references. Unresolved records do not disclose existence or status.
    All errors return normalized error envelope (§I08).
    """
    if locale not in Locale.values:
        return _error_response(
            request,
            404,
            "NOT_FOUND",
            f"Locale '{locale}' not found.",
        )

    if not refs or not refs.strip():
        return RecordResolveOut(items=[], unresolved=[])

    raw_tokens = [tok.strip() for tok in refs.split(",")]
    parsed_tokens: list[tuple[str, str]] = []

    for tok in raw_tokens:
        if not tok:
            return _error_response(
                request,
                400,
                "INVALID_INPUT",
                "Malformed refs parameter: empty reference entry.",
                {"refs": ["Empty reference entry."]},
            )
        parts = tok.split(":")
        if len(parts) != 2:
            return _error_response(
                request,
                400,
                "INVALID_INPUT",
                "Malformed reference format. Must be in family:id format.",
                {"refs": ["Reference must match family:id format."]},
            )
        family, id_str = parts[0].strip(), parts[1].strip()
        if not family or not id_str:
            return _error_response(
                request,
                400,
                "INVALID_INPUT",
                "Malformed reference entry: family and id cannot be empty.",
                {"refs": ["Family and id cannot be empty."]},
            )
        if family not in RESOLVER_FAMILIES:
            return _error_response(
                request,
                400,
                "INVALID_INPUT",
                "Unknown family in reference.",
                {"refs": ["Specified content family is not supported."]},
            )
        if not _ID_RE.match(id_str):
            return _error_response(
                request,
                400,
                "INVALID_INPUT",
                "Invalid reference ID. ID must be a positive decimal integer matching [1-9][0-9]*.",
                {
                    "refs": [
                        "Reference ID must be a canonical positive integer matching [1-9][0-9]*."
                    ]
                },
            )
        if int(id_str) > MAX_ID:
            return _error_response(
                request,
                400,
                "INVALID_INPUT",
                "Reference ID exceeds storage range.",
                {"refs": ["Reference ID exceeds maximum allowed integer value."]},
            )
        parsed_tokens.append((family, id_str))

    seen: set[tuple[str, str]] = set()
    ordered_unique_refs: list[tuple[str, str]] = []
    for pair in parsed_tokens:
        if pair not in seen:
            seen.add(pair)
            ordered_unique_refs.append(pair)

    if len(ordered_unique_refs) > MAX_UNIQUE_REFS:
        return _error_response(
            request,
            400,
            "INVALID_INPUT",
            f"Too many references requested. Maximum allowed is {MAX_UNIQUE_REFS} references.",
            {"refs": [f"Maximum unique references limit ({MAX_UNIQUE_REFS}) exceeded."]},
        )

    # Group requested IDs by family
    ids_by_family: dict[str, list[int]] = {}
    for family, id_str in ordered_unique_refs:
        ids_by_family.setdefault(family, []).append(int(id_str))

    resolved_map: dict[tuple[str, str], WorkRefOut] = {}
    for family, pks in ids_by_family.items():
        model = RESOLVER_FAMILIES[family]
        public_mgr = getattr(getattr(model, "objects", None), "public", None)
        if public_mgr is None:
            continue
        records = list(public_mgr().filter(locale=locale, pk__in=pks))
        entity_key = FAMILY_TO_ENTITY_KEY.get(family, family)
        for obj in records:
            obj_id = str(obj.pk)
            work_ref = WorkRefOut(
                family=family,
                id=obj_id,
                locale=str(obj.locale),
                slug=str(getattr(obj, "slug", "") or ""),
                title=str(getattr(obj, "title", "") or ""),
                summary=_extract_summary(family, obj),
                routeFamily=ROUTE_FAMILY_MAP[family],
                courseSlug=None,
            )
            resolved_map[(family, obj_id)] = work_ref
        # A04: still-published (snapshot-backed) records stay resolvable.
        live_ids = {str(obj.pk) for obj in records}
        for pk in pks:
            if str(pk) in live_ids:
                continue
            target = resolve_published_target(model, entity_key, pk, locale)
            if target is None:
                continue
            resolved_map[(family, str(pk))] = WorkRefOut(
                family=family,
                id=str(target.pk),
                locale=str(target.locale),
                slug=str(getattr(target, "slug", "") or ""),
                title=str(getattr(target, "title", "") or ""),
                summary=_extract_summary(family, target),
                routeFamily=ROUTE_FAMILY_MAP[family],
                courseSlug=None,
            )

    items: list[WorkRefOut] = []
    unresolved: list[UnresolvedRefOut] = []
    for family, id_str in ordered_unique_refs:
        key = (family, id_str)
        if key in resolved_map:
            items.append(resolved_map[key])
        else:
            unresolved.append(UnresolvedRefOut(family=family, id=id_str))

    return RecordResolveOut(items=items, unresolved=unresolved)
