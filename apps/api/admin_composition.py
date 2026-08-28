"""Custom admin page-composition API (ADR-0026, ADM-3).

Staff + OTP protected endpoints over the ``CompositionPage`` aggregate:
``GET /`` list, ``POST /`` create, ``GET /schema`` block/section metadata,
``GET /{id}`` detail and ``PUT /{id}`` full-document optimistically-locked
update (``If-Match``) that replaces the section/block tree. Unsafe methods
additionally enforce the same-origin CSRF baseline.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from ninja import Field, Router, Schema

from apps.api.admin_common import (
    AdminError,
    _check_csrf,
    _parse_positive_int,
    _require_admin_otp,
)
from apps.composition.blocks import (
    KIND_LANDING,
    SECTION_LAYOUT_RATIOS,
    VALID_KINDS,
    BlockValidationError,
    allowed_block_types,
    composition_schema,
    validate_block_settings,
)
from apps.composition.models import CompositionBlock, CompositionPage, CompositionSection

composition_router = Router()

VALID_LOCALES = ("fa", "en")
VALID_STATUSES = ("draft", "review", "published", "archived")
KEY_RE = re.compile(r"^[a-z0-9-]+$")
MAX_SECTIONS = 50
MAX_BLOCKS_PER_SECTION = 100


def _parse_if_match(header: str | None) -> datetime | None:
    """Normalize the If-Match header into an aware datetime, or None."""
    raw = (header or "").strip().strip('"')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _serialize_updated_at(dt: datetime) -> str:
    """Format like DjangoJSONEncoder (ECMA-262, millisecond precision, Z for UTC)."""
    r = dt.isoformat()
    if dt.microsecond:
        r = r[:23] + r[26:]
    if r.endswith("+00:00"):
        r = r.removesuffix("+00:00") + "Z"
    return r


def _if_match_matches(header: str | None, item) -> bool:
    """True when the If-Match timestamp equals the row's current updated_at.

    The admin API serializes ``updatedAt`` at millisecond precision (Django's
    ECMA-262 encoder), so a round-tripped If-Match carries at most millisecond
    precision; compare both sides at that precision to tolerate the truncation.
    """
    expected = _parse_if_match(header)
    if expected is None:
        return False
    current = item.updated_at
    try:
        if expected.tzinfo is None or current.tzinfo is None:
            return False
        expected_ms = expected.astimezone(UTC).replace(
            microsecond=(expected.microsecond // 1000) * 1000
        )
        current_ms = current.astimezone(UTC).replace(
            microsecond=(current.microsecond // 1000) * 1000
        )
        return expected_ms == current_ms
    except (TypeError, ValueError):
        return False


class AdminConflictError(AdminError):
    """409 optimistic-lock conflict carrying the server's current updated_at."""

    def __init__(self, current_updated_at: str):
        super().__init__(
            409,
            "CONFLICT",
            "The record was modified by someone else.",
            fields=None,
        )
        self.current_updated_at = current_updated_at


def _admin_conflict_handler(request, exc: AdminConflictError):
    return JsonResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "currentUpdatedAt": exc.current_updated_at,
        },
        status=exc.status,
    )


class BlockFieldSpecOut(Schema):
    key: str
    label: str
    type: str
    options: list[str] | None = None


class BlockTypeOut(Schema):
    type: str
    labelFa: str
    required: list[str] = Field(default_factory=list)
    fields: list[BlockFieldSpecOut] = Field(default_factory=list)


class SectionLayoutOut(Schema):
    value: str
    label: str
    ratios: list[str] = Field(default_factory=list)


class CompositionSchemaOut(Schema):
    kind: str = KIND_LANDING
    blockTypes: list[BlockTypeOut] = Field(default_factory=list)
    sectionLayouts: list[SectionLayoutOut] = Field(default_factory=list)


class CompositionListItemOut(Schema):
    """Compact admin row for the composition list view."""

    id: int
    key: str
    kind: str
    locale: str
    title: str
    status: str
    publishedAt: datetime | None
    updatedAt: datetime


class CompositionListOut(Schema):
    """Paginated composition list envelope."""

    items: list[CompositionListItemOut] = Field(default_factory=list)
    page: int
    pageSize: int
    total: int


class CompositionBlockOut(Schema):
    id: int
    position: int
    blockType: str
    settings: dict = Field(default_factory=dict)
    enabled: bool


class CompositionSectionOut(Schema):
    id: int
    position: int
    layout: str
    ratio: str
    enabled: bool
    blocks: list[CompositionBlockOut] = Field(default_factory=list)


class CompositionDetailOut(Schema):
    id: int
    key: str
    kind: str
    locale: str
    title: str
    status: str
    publishedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    sections: list[CompositionSectionOut] = Field(default_factory=list)


class CompositionCreateIn(Schema):
    key: str
    locale: str
    title: str
    status: str = "draft"
    kind: str = KIND_LANDING


class CompositionBlockUpdateIn(Schema):
    blockType: str
    settings: dict = Field(default_factory=dict)
    enabled: bool = True


class CompositionSectionUpdateIn(Schema):
    layout: str = "1col"
    ratio: str = ""
    enabled: bool = True
    blocks: list[CompositionBlockUpdateIn] = Field(default_factory=list)


class CompositionUpdateIn(Schema):
    title: str | None = None
    status: str | None = None
    sections: list[CompositionSectionUpdateIn]


def _serialize_page(page) -> CompositionDetailOut:
    """Serialize a CompositionPage with its ordered section/block tree."""
    return CompositionDetailOut(
        id=page.pk,
        key=page.key,
        kind=page.kind,
        locale=page.locale,
        title=page.title,
        status=page.status,
        publishedAt=page.published_at,
        createdAt=page.created_at,
        updatedAt=page.updated_at,
        sections=[
            CompositionSectionOut(
                id=section.pk,
                position=section.position,
                layout=section.layout,
                ratio=section.ratio,
                enabled=section.enabled,
                blocks=[
                    CompositionBlockOut(
                        id=block.pk,
                        position=block.position,
                        blockType=block.block_type,
                        settings=block.settings,
                        enabled=block.enabled,
                    )
                    for block in section.blocks.all()
                ],
            )
            for section in page.sections.all()
        ],
    )


def _validate_sections(
    sections: list[CompositionSectionUpdateIn], kind: str = KIND_LANDING
) -> None:
    """Validate the full section/block document (semantic, not ninja-parsed)."""
    if len(sections) > MAX_SECTIONS:
        raise AdminError(
            400,
            "VALIDATION",
            f"sections must contain at most {MAX_SECTIONS} entries.",
        )
    for i, section in enumerate(sections):
        layout = section.layout
        if layout not in SECTION_LAYOUT_RATIOS:
            raise AdminError(
                400,
                "VALIDATION",
                f"sections[{i}].layout is invalid.",
                fields={f"sections[{i}].layout": [f"Invalid layout '{layout}'."]},
            )
        ratio = section.ratio or ""
        if ratio not in SECTION_LAYOUT_RATIOS[layout]:
            raise AdminError(
                400,
                "VALIDATION",
                f"sections[{i}].ratio is invalid.",
                fields={
                    f"sections[{i}].ratio": [
                        f"Invalid ratio '{ratio}' for layout '{layout}'."
                    ]
                },
            )
        if len(section.blocks) > MAX_BLOCKS_PER_SECTION:
            raise AdminError(
                400,
                "VALIDATION",
                f"sections[{i}] must contain at most {MAX_BLOCKS_PER_SECTION} blocks.",
                fields={
                    f"sections[{i}]": [f"At most {MAX_BLOCKS_PER_SECTION} blocks are allowed."]
                },
            )
        allowed = set(allowed_block_types(kind))
        for j, block in enumerate(section.blocks):
            if block.blockType not in allowed:
                raise AdminError(
                    400,
                    "VALIDATION",
                    f"sections[{i}].blocks[{j}].blockType is invalid.",
                    fields={
                        f"sections[{i}].blocks[{j}].blockType": [
                            f"Unknown block type '{block.blockType}'."
                        ]
                    },
                )
            try:
                validate_block_settings(block.blockType, block.settings, kind=kind)
            except BlockValidationError as exc:
                raise AdminError(
                    400,
                    "VALIDATION",
                    str(exc),
                    fields={f"sections[{i}].blocks[{j}].settings": [str(exc)]},
                ) from None


@composition_router.get(
    "/schema",
    response=CompositionSchemaOut,
    summary="Composition schema metadata.",
)
def composition_schema_view(request, kind: str = KIND_LANDING):
    _require_admin_otp(request)
    if kind not in VALID_KINDS:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid kind. Expected one of: {', '.join(VALID_KINDS)}.",
        )
    return composition_schema(kind)


@composition_router.get("", response=CompositionListOut, summary="List composition pages.")
def composition_list(
    request,
    q: str | None = None,
    locale: str | None = None,
    status: str | None = None,
    page: str = "1",
    pageSize: str = "20",
):
    _require_admin_otp(request)
    if locale is not None and locale not in VALID_LOCALES:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}.",
        )
    if status is not None and status not in VALID_STATUSES:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}.",
        )
    page_num = _parse_positive_int(request, "page", page, default=1, max_value=1_000_000)
    page_size = _parse_positive_int(request, "pageSize", pageSize, default=20, max_value=100)

    qs = CompositionPage.objects.all().order_by("-updated_at", "-id")
    if locale is not None:
        qs = qs.filter(locale=locale)
    if status is not None:
        qs = qs.filter(status=status)
    if q is not None:
        qs = qs.filter(Q(key__icontains=q) | Q(title__icontains=q))

    total = qs.count()
    items = qs[(page_num - 1) * page_size : page_num * page_size]
    return CompositionListOut(
        items=[
            CompositionListItemOut(
                id=item.pk,
                key=item.key,
                kind=item.kind,
                locale=item.locale,
                title=item.title,
                status=item.status,
                publishedAt=item.published_at,
                updatedAt=item.updated_at,
            )
            for item in items
        ],
        page=page_num,
        pageSize=page_size,
        total=total,
    )


@composition_router.post(
    "",
    response={201: CompositionDetailOut},
    summary="Create a composition page.",
)
def composition_create(request, payload: CompositionCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    if payload.locale not in VALID_LOCALES:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}.",
        )
    if payload.status not in VALID_STATUSES:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}.",
        )
    if payload.kind not in VALID_KINDS:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid kind. Expected one of: {', '.join(VALID_KINDS)}.",
        )
    key = payload.key.strip()
    title = payload.title.strip()
    if not key or not KEY_RE.fullmatch(key):
        raise AdminError(400, "VALIDATION", "key must match ^[a-z0-9-]+$.")
    if not title:
        raise AdminError(400, "VALIDATION", "title must not be empty.")
    if CompositionPage.objects.filter(key=key).exists():
        raise AdminError(409, "DUPLICATE", "A page with this key already exists.")
    try:
        with transaction.atomic():
            page = CompositionPage.objects.create(
                key=key,
                kind=payload.kind,
                locale=payload.locale,
                title=title,
                status=payload.status,
                published_at=timezone.now() if payload.status == "published" else None,
            )
    except IntegrityError:
        raise AdminError(409, "DUPLICATE", "A page with this key already exists.") from None
    return _serialize_page(page)


@composition_router.get(
    "/{page_id}",
    response=CompositionDetailOut,
    summary="Composition page detail.",
)
def composition_detail(request, page_id: int):
    _require_admin_otp(request)
    page = (
        CompositionPage.objects.prefetch_related("sections__blocks")
        .filter(pk=page_id)
        .first()
    )
    if page is None:
        raise AdminError(404, "NOT_FOUND", "Composition page not found.")
    return _serialize_page(page)


@composition_router.put(
    "/{page_id}",
    response=CompositionDetailOut,
    summary="Replace a composition page (optimistic locking).",
)
def composition_update(request, page_id: int, payload: CompositionUpdateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    try:
        with transaction.atomic():
            # Row lock (Postgres) so two concurrent PUTs cannot both pass
            # If-Match; the compare happens under the lock.
            page = CompositionPage.objects.select_for_update().get(pk=page_id)
            if not _if_match_matches(request.headers.get("If-Match"), page):
                raise AdminConflictError(_serialize_updated_at(page.updated_at))
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise AdminError(400, "VALIDATION", "title must not be empty.")
                page.title = title
            if payload.status is not None:
                if payload.status not in VALID_STATUSES:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}.",
                    )
                page.status = payload.status
            if payload.sections is not None:
                _validate_sections(payload.sections, kind=page.kind)
                page.sections.all().delete()
                for position, section in enumerate(payload.sections):
                    new_section = CompositionSection.objects.create(
                        page=page,
                        position=position,
                        layout=section.layout,
                        ratio=section.ratio or "",
                        enabled=section.enabled,
                    )
                    CompositionBlock.objects.bulk_create(
                        [
                            CompositionBlock(
                                section=new_section,
                                position=block_position,
                                block_type=block.blockType,
                                settings=block.settings,
                                enabled=block.enabled,
                            )
                            for block_position, block in enumerate(section.blocks)
                        ]
                    )
            if (
                payload.status == "published"
                and page.published_at is None
            ):
                page.published_at = timezone.now()
            page.save()
    except CompositionPage.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Composition page not found.") from None
    page = CompositionPage.objects.prefetch_related("sections__blocks").get(pk=page.pk)
    return _serialize_page(page)


from apps.api.admin_api import admin_api  # noqa: E402

admin_api.exception_handler(AdminConflictError)(_admin_conflict_handler)
