"""Admin media presentation API (Track AB-04).

Staff + OTP protected additive endpoints over the private-by-default
``Media`` rows: ``PATCH /{id}/presentation`` (focal point, rights
statements, license linkage, captions) and ``GET /licenses`` (reference
list backing the AF license select box). The existing media upload/list
endpoints (``apps/api/admin_media.py``) stay untouched.

Frozen contract (docs/plan/TRACK-AB-admin-backend-api-task-list.md, AB-04):

- PATCH ``/{id}/presentation`` (If-Match)
  -> body accepts a SUBSET of ``{focal_x, focal_y, rights_statement_fa,
     rights_statement_en, license_id, caption_fa, caption_en}``;
     200 ``{id, updatedAt}``; 428 PRECONDITION_REQUIRED when the header
     is missing; 409 STALE_REVISION on revision mismatch
- GET ``/licenses`` -> ``[{id, name}]`` ordered by name

Registration: the routes attach DIRECTLY to the ``NinjaAPI`` root router
group via ``register_media_ext(api)`` instead of ``add_router``. The
legacy media API registers an untyped ``media/<media_id>`` pattern, and
Django tries URL patterns in registration order -- an appended literal
``media/licenses`` route would be captured by ``media_detail`` and
rejected as 422 (``licenses`` is not an int). Direct registration puts
the literal routes in the root group, which precedes every
``add_router`` group, mirroring how ``content/<entity>/bulk-archive``
resolves before ``content/<entity>/<id>``.

Explicit-null semantics: an explicit ``null`` CLEARS the targeted field
-- ``focal_x``/``focal_y``/``license_id`` become SQL NULL; the text
fields (``rights_statement_*``, ``caption_*``, blank-default columns)
become the empty string ``""``. Omitted keys leave the stored value
untouched.

Error tokens (ProblemDetails ``{code, message, fields: {path: [token]}}``):
``UNKNOWN_FIELD`` for any body key outside the frozen subset,
``OUT_OF_RANGE`` for focal values outside 0..100, ``UNKNOWN_LICENSE``
for an unknown ``license_id``, ``TOO_LONG`` for captions over the
column bound, plus the shared If-Match pair. Focal values are accepted
at any precision and quantized to two decimal places (ROUND_HALF_UP) to
match the ``Decimal(5, 2)`` columns; the 0..100 check runs BEFORE
quantization.

Stable error tokens are declared once in ``apps/api/admin_common.py`` and
imported from there (graduated by the AB-07 consolidation packet).
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from ninja import Schema

from apps.api.admin_common import (
    NOT_FOUND,
    OUT_OF_RANGE,
    TOO_LONG,
    UNKNOWN_FIELD,
    UNKNOWN_LICENSE,
    VALIDATION,
    AdminError,
    _audit_log,
    _check_csrf,
    _format_revision,
    _require_admin_otp,
    _require_if_match,
)
from apps.media.models import Media, MediaLicense

# Body keys accepted by PATCH /{id}/presentation (frozen AB-04 subset).
ALLOWED_PRESENTATION_FIELDS = frozenset(
    {
        "focal_x",
        "focal_y",
        "rights_statement_fa",
        "rights_statement_en",
        "license_id",
        "caption_fa",
        "caption_en",
    }
)

PATCHABLE_TEXT_FIELDS = (
    "rights_statement_fa",
    "rights_statement_en",
    "caption_fa",
    "caption_en",
)

FOCAL_MIN = Decimal("0")
FOCAL_MAX = Decimal("100")
FOCAL_QUANT = Decimal("0.01")
CAPTION_MAX = 300  # Media.caption_fa / caption_en CharField bound.


class MediaPresentationPatchIn(Schema):
    """Subset patch payload; explicit null clears (see module docstring)."""

    focal_x: float | None = None
    focal_y: float | None = None
    rights_statement_fa: str | None = None
    rights_statement_en: str | None = None
    license_id: int | None = None
    caption_fa: str | None = None
    caption_en: str | None = None


class MediaPresentationOut(Schema):
    """PATCH success body: row id plus the new If-Match revision."""

    id: int
    updatedAt: str


class MediaLicenseOut(Schema):
    """Reference row backing the AF license select box."""

    id: int
    name: str


def _require_row_revision(request, media: Media) -> None:
    """If-Match gate for one row; the row's updatedAt doubles as the revision."""
    _require_if_match(
        request,
        current=media.updated_at,
        missing_message="An If-Match revision is required. GET the media first.",
        stale_message="The media row was modified by someone else.",
    )


def _quantize_focal(value: float) -> Decimal:
    """Quantize a focal percent to two decimal places (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(FOCAL_QUANT, rounding=ROUND_HALF_UP)


def _reject_unknown_fields(request) -> None:
    """Reject any body key outside the frozen subset with UNKNOWN_FIELD."""
    try:
        raw = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AdminError(400, VALIDATION, "Malformed JSON body.") from None
    if not isinstance(raw, dict):
        raise AdminError(400, VALIDATION, "A JSON object body is required.")
    unknown = sorted(str(key) for key in raw if key not in ALLOWED_PRESENTATION_FIELDS)
    if unknown:
        raise AdminError(
            400,
            VALIDATION,
            "Unknown field(s) in payload.",
            fields={key: [UNKNOWN_FIELD] for key in unknown},
        )


def _patch_problems(data: dict) -> dict[str, list[str]]:
    """Collect every semantic problem into one ProblemDetails fields map."""
    problems: dict[str, list[str]] = {}
    for axis in ("focal_x", "focal_y"):
        value = data.get(axis)
        if value is None:
            continue
        number = Decimal(str(value))
        if number < FOCAL_MIN or number > FOCAL_MAX:
            problems.setdefault(axis, []).append(OUT_OF_RANGE)
    license_id = data.get("license_id")
    if license_id is not None and not MediaLicense.objects.filter(pk=license_id).exists():
        problems.setdefault("license_id", []).append(UNKNOWN_LICENSE)
    for field in ("caption_fa", "caption_en"):
        value = data.get(field)
        if value is not None and len(value) > CAPTION_MAX:
            problems.setdefault(field, []).append(TOO_LONG)
    return problems


def _validation_error(problems: dict[str, list[str]]) -> AdminError:
    return AdminError(
        400,
        VALIDATION,
        "Media presentation payload failed validation.",
        fields=problems,
    )


def media_presentation_update(request, media_id: int, payload: MediaPresentationPatchIn):
    """PATCH /api/v1/admin/media/{id}/presentation (optimistic locking)."""
    _require_admin_otp(request)
    _check_csrf(request)
    _reject_unknown_fields(request)
    data = payload.model_dump(exclude_unset=True)
    problems = _patch_problems(data)
    if problems:
        raise _validation_error(problems)
    with transaction.atomic():
        media = Media.objects.select_for_update().filter(pk=media_id).first()
        if media is None:
            raise AdminError(404, NOT_FOUND, "Media not found.")
        _require_row_revision(request, media)
        update_fields: list[str] = []
        for axis in ("focal_x", "focal_y"):
            if axis in data:
                value = data[axis]
                setattr(media, axis, None if value is None else _quantize_focal(value))
                update_fields.append(axis)
        if "license_id" in data:
            media.license_id = data["license_id"]
            update_fields.append("license")
        for field in PATCHABLE_TEXT_FIELDS:
            if field in data:
                setattr(media, field, data[field] if data[field] is not None else "")
                update_fields.append(field)
        if update_fields:
            update_fields.append("updated_at")
            media.save(update_fields=update_fields)
    _audit_log(
        request,
        action="media.presentation_update",
        model_name="media",
        object_id=str(media_id),
        detail=(
            f"PATCH /api/v1/admin/media/{media_id}/presentation -> 200; "
            f"fields={sorted(data)}"
        ),
    )
    return MediaPresentationOut(id=media.id, updatedAt=_format_revision(media.updated_at))


def media_licenses(request):
    """GET /api/v1/admin/media/licenses (backs the AF select box)."""
    _require_admin_otp(request)
    return list(MediaLicense.objects.order_by("name").values("id", "name"))


def register_media_ext(api) -> None:
    """Attach the AB-04 routes to the API root group (see module docstring).

    Direct ``api.get``/``api.patch`` registration (instead of ``add_router``)
    is required so the literal ``media/licenses`` path resolves before the
    legacy untyped ``media/<media_id>`` pattern.
    """
    api.get(
        "/media/licenses",
        response=list[MediaLicenseOut],
        summary="Reference list of licenses ordered by name (backs the AF select box).",
    )(media_licenses)
    api.patch(
        "/media/{media_id}/presentation",
        response=MediaPresentationOut,
        summary="Update media presentation metadata (optimistic locking via If-Match).",
    )(media_presentation_update)
