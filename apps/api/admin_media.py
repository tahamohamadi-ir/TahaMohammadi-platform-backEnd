"""Custom admin media API (ADR-0026, ADM-2).

Staff + OTP protected endpoints over the private-by-default ``Media`` library:
``GET /`` list (search/type/active filters + paging), ``POST /`` multipart
upload, ``GET /orphans`` (rows with zero usage references), ``GET /{id}``
detail and ``PUT /{id}`` optimistically-locked update (``If-Match``). Unsafe
methods additionally enforce the same-origin CSRF baseline.

Content image FKs (featured / diagram / screenshot) register in
``MEDIA_REFERENCE_FIELDS`` so orphan counting reflects Media-library usage.
Composition block JSON ``settings`` (``mediaId``/``mediaIds``/
``beforeMediaId``/``afterMediaId``, board B11) are scanned as well so a
block-referenced file is never misreported as an orphan.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from ninja import File, Form, Router, Schema
from ninja.files import UploadedFile

from apps.api.admin_common import (
    AdminError,
    _check_csrf,
    _parse_positive_int,
    _require_admin_otp,
)
from apps.composition.models import CompositionBlock
from apps.media.models import Media
from apps.media.sniff import mime_family, sniff_mime

media_router = Router()

# ("app.Model", "field") pairs referencing a Media row via Django FK.
MEDIA_REFERENCE_FIELDS: list[tuple[str, str]] = [
    ("content.Article", "featured_image"),
    ("content.ProjectDiagram", "diagram_image"),
    ("content.ProjectScreenshot", "screenshot_image"),
    ("content.ResearchStatement", "statement_pdf"),
]

# JSON keys inside composition block ``settings`` that may hold Media PKs
# (int or numeric string). Keep in sync with apps.composition.projection.
MEDIA_JSON_SETTINGS_KEYS = ("mediaId", "beforeMediaId", "afterMediaId")
MEDIA_JSON_LIST_KEYS = ("mediaIds",)


def _json_settings_media_pks(settings: object) -> set[int]:
    """Media PKs referenced by one composition block ``settings`` payload."""
    pks: set[int] = set()
    if not isinstance(settings, dict):
        return pks

    def _as_pk(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    for key in MEDIA_JSON_SETTINGS_KEYS:
        pk = _as_pk(settings.get(key))
        if pk is not None:
            pks.add(pk)
    for key in MEDIA_JSON_LIST_KEYS:
        value = settings.get(key)
        if isinstance(value, list):
            for item in value:
                pk = _as_pk(item)
                if pk is not None:
                    pks.add(pk)
    return pks


def composition_json_usage_count(media) -> int:
    """Composition blocks whose JSON ``settings`` reference ``media`` (B11)."""
    found = 0
    rows = CompositionBlock.objects.values_list("settings", flat=True).iterator()
    for settings in rows:
        if media.pk in _json_settings_media_pks(settings):
            found += 1
    return found


def media_usage_count(media) -> int:
    """Total references to ``media`` across registered FKs + composition JSON."""
    total = 0
    for model_path, field in MEDIA_REFERENCE_FIELDS:
        app, m = model_path.split(".")
        model = django_apps.get_model(app, m)
        total += model.objects.filter(**{f"{field}__pk": media.pk}).count()
    total += composition_json_usage_count(media)
    return total


class _SniffedMime:
    def __init__(self, mime: str):
        self.mime = mime


def _sniff_upload(upload: UploadedFile):
    """Detect the MIME of an uploaded file from its content (magic bytes)."""
    mime = sniff_mime(upload)
    return _SniffedMime(mime) if mime else None


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


class MediaItemOut(Schema):
    """Admin media row: metadata + storage URL + usage count."""

    id: int
    title: str
    altText: str
    altTextFa: str
    altTextEn: str
    mime: str
    size: int
    isActive: bool
    url: str
    usageCount: int = 0
    createdAt: datetime
    updatedAt: datetime

    @staticmethod
    def resolve_altText(obj) -> str:
        return obj.alt_text

    @staticmethod
    def resolve_altTextFa(obj) -> str:
        return obj.alt_text_fa

    @staticmethod
    def resolve_altTextEn(obj) -> str:
        return obj.alt_text_en

    @staticmethod
    def resolve_isActive(obj) -> bool:
        return obj.is_active

    @staticmethod
    def resolve_createdAt(obj) -> datetime:
        return obj.created_at

    @staticmethod
    def resolve_updatedAt(obj) -> datetime:
        return obj.updated_at

    @staticmethod
    def resolve_usageCount(obj) -> int:
        return media_usage_count(obj)

    @staticmethod
    def resolve_url(obj, context) -> str:
        request = context["request"]
        return request.build_absolute_uri(obj.file.url)


class MediaListOut(Schema):
    """Paginated media list envelope."""

    items: list[MediaItemOut]
    page: int
    pageSize: int
    total: int


class MediaCreateIn(Schema):
    """Multipart create metadata (the uploaded bytes arrive via the ``file`` param)."""

    title: str | None = None
    altTextFa: str | None = None
    altTextEn: str | None = None


class MediaUpdateIn(Schema):
    """Optimistically-locked partial update payload."""

    title: str | None = None
    altText: str | None = None
    altTextFa: str | None = None
    altTextEn: str | None = None
    isActive: bool | None = None


def _apply_filters(request, q, type, active, page, pageSize):
    _require_admin_otp(request)
    page_num = _parse_positive_int(request, "page", page, default=1, max_value=1_000_000)
    page_size = _parse_positive_int(request, "pageSize", pageSize, default=20, max_value=100)

    qs = Media.objects.all().order_by("-created_at")
    if q is not None:
        qs = qs.filter(title__icontains=q)
    if type is not None:
        if type not in ("image", "pdf", "video", "audio"):
            raise AdminError(
                400,
                "VALIDATION",
                "Invalid type. Expected image, pdf, video, or audio.",
            )
        if type == "pdf":
            qs = qs.filter(mime="application/pdf")
        else:
            qs = qs.filter(mime__startswith=f"{type}/")
    if active is not None:
        if active == "true":
            qs = qs.filter(is_active=True)
        elif active == "false":
            qs = qs.filter(is_active=False)
        else:
            raise AdminError(400, "VALIDATION", "Invalid active. Expected true or false.")
    return qs, page_num, page_size


@media_router.get("", response=MediaListOut, summary="List media.")
def media_list(
    request,
    q: str | None = None,
    type: str | None = None,
    active: str | None = None,
    page: str = "1",
    pageSize: str = "20",
):
    qs, page_num, page_size = _apply_filters(request, q, type, active, page, pageSize)
    total = qs.count()
    items = qs[(page_num - 1) * page_size : page_num * page_size]
    return {
        "items": list(items),
        "page": page_num,
        "pageSize": page_size,
        "total": total,
    }


@media_router.post(
    "",
    response={201: MediaItemOut},
    summary="Upload media (multipart).",
)
def media_create(
    request,
    payload: MediaCreateIn = Form(...),  # noqa: B008
    file: UploadedFile | None = File(None),  # noqa: B008
):
    _require_admin_otp(request)
    _check_csrf(request)
    upload = file
    if upload is None:
        raise AdminError(400, "VALIDATION", "file required.")
    title = (payload.title or "").strip() or upload.name
    media = Media(
        file=upload,
        title=title,
        alt_text_fa=payload.altTextFa or "",
        alt_text_en=payload.altTextEn or "",
    )
    try:
        media.full_clean()
    except ValidationError as exc:
        raise AdminError(
                400,
                "VALIDATION",
                "Invalid media.",
                fields=exc.message_dict,
            ) from None
    media.save()
    return media


@media_router.get(
    "/orphans",
    response=MediaListOut,
    summary="List media with zero usage references.",
)
def media_orphans(
    request,
    q: str | None = None,
    type: str | None = None,
    active: str | None = None,
    page: str = "1",
    pageSize: str = "20",
):
    qs, page_num, page_size = _apply_filters(request, q, type, active, page, pageSize)
    # Manual scan: usage references are cheap to count (FK registry plus a
    # bounded JSON scan over composition blocks) and the dataset is a
    # personal-site media library, so filtering the ordered set in Python is
    # bounded and acceptable.
    ordered = list(qs)
    orphans = [m for m in ordered if media_usage_count(m) == 0]
    total = len(orphans)
    page_rows = orphans[(page_num - 1) * page_size : page_num * page_size]
    return {
        "items": page_rows,
        "page": page_num,
        "pageSize": page_size,
        "total": total,
    }


@media_router.get("/{media_id}", response=MediaItemOut, summary="Media detail.")
def media_detail(request, media_id: int):
    _require_admin_otp(request)
    media = Media.objects.filter(pk=media_id).first()
    if media is None:
        raise AdminError(404, "NOT_FOUND", "Media not found.")
    return media


@media_router.put(
    "/{media_id}",
    response=MediaItemOut,
    summary="Update media (optimistic locking).",
)
def media_update(request, media_id: int, payload: MediaUpdateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    try:
        with transaction.atomic():
            # Row lock (Postgres) so two concurrent PUTs cannot both pass
            # If-Match; the compare happens under the lock.
            media = Media.objects.select_for_update().get(pk=media_id)
            if not _if_match_matches(request.headers.get("If-Match"), media):
                raise AdminConflictError(_serialize_updated_at(media.updated_at))
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise AdminError(400, "VALIDATION", "title must not be empty.")
                media.title = title
            if payload.altText is not None:
                media.alt_text = payload.altText
            if payload.altTextFa is not None:
                media.alt_text_fa = payload.altTextFa
            if payload.altTextEn is not None:
                media.alt_text_en = payload.altTextEn
            if payload.isActive is not None:
                media.is_active = payload.isActive
            try:
                media.full_clean()
            except ValidationError as exc:
                raise AdminError(
                400,
                "VALIDATION",
                "Invalid media.",
                fields=exc.message_dict,
            ) from None
            media.save()
    except Media.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Media not found.") from None
    return media


@media_router.post(
    "/{media_id}/replace",
    response=MediaItemOut,
    summary="Replace the media file (MIME-family compatible).",
)
def media_replace(
    request,
    media_id: int,
    file: UploadedFile | None = File(None),  # noqa: B008
):
    _require_admin_otp(request)
    _check_csrf(request)
    if file is None:
        raise AdminError(400, "VALIDATION", "file required.")
    try:
        with transaction.atomic():
            media = Media.objects.select_for_update().get(pk=media_id)
            previous_mime = media.mime or ""
            kind = _sniff_upload(file)
            if kind is None:
                raise AdminError(400, "VALIDATION", "Unsupported file type.")
            new_mime = kind.mime
            if mime_family(previous_mime) != mime_family(new_mime):
                raise AdminError(
                    400,
                    "VALIDATION",
                    "Replacement must be the same MIME family (image, pdf, video, or audio).",
                )
            media.file = file
            try:
                media.full_clean()
            except ValidationError as exc:
                raise AdminError(
                400,
                "VALIDATION",
                "Invalid media.",
                fields=exc.message_dict,
            ) from None
            media.save()
    except Media.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Media not found.") from None
    return media


from apps.api.admin_api import admin_api  # noqa: E402

admin_api.exception_handler(AdminConflictError)(_admin_conflict_handler)
