"""Admin home composition API (Track AB-00/AB-02).

Staff + OTP protected CRUD over the BK-01 ``HomeModule`` rows of one locale
under ``/api/v1/admin/home-modules/*``. Draft and published rows are both
admin-visible here; the public gate stays ``visible_for_locale``.

Frozen contract (docs/plan/TRACK-AB-admin-backend-api-task-list.md, AB-02):

- GET  ``/{locale}``            -> 200 {revision, modules: [...]} ordered by order
- PUT  ``/{locale}`` (If-Match) -> full-array bulk save (replace-all semantics);
                                  200 {revision}; 428 PRECONDITION_REQUIRED when
                                  the header is missing; 409 STALE_REVISION on
                                  revision mismatch
- POST ``/{locale}/validate``   -> same-body dry run; 200 {} | 400 ProblemDetails

Errors are ProblemDetails ``{code, message, fields: {path: [token]}}`` bodies
with the stable field tokens ``UNKNOWN_KEY | DUPLICATE_ORDER | BAD_ENUM``.
Validation runs before any write, so an atomic reject keeps prior rows.

``revision`` mirrors the public convention (ISO timestamp of the latest
``updated_at`` across the locale's rows, millisecond precision, empty string
when the locale has no rows yet).

Stable error tokens are declared once in ``apps/api/admin_common.py`` and
imported from there (graduated by the AB-07 consolidation packet).
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from ninja import Router, Schema

from apps.api.admin_common import (
    BAD_ENUM,
    DUPLICATE_KEY,
    DUPLICATE_ORDER,
    NOT_FOUND,
    TOO_LONG,
    UNKNOWN_KEY,
    VALIDATION,
    AdminError,
    _audit_log,
    _check_csrf,
    _format_revision,
    _require_admin_otp,
    _require_if_match,
)
from apps.content.models import HomeModule, HomeModuleKey, Locale, SelectionMode

home_router = Router()

VALID_LOCALES = tuple(Locale.values)
CANONICAL_KEYS = tuple(HomeModuleKey.values)
VALID_SELECTION_MODES = tuple(SelectionMode.values)

PROVENANCE_NOTE_MAX = 300


class HomeModuleIn(Schema):
    """One module slot in a PUT/validate payload."""

    key: str
    visible: bool = False
    order: int
    selection_mode: str = "manual"
    provenance_note: str = ""


class HomeModulesPutIn(Schema):
    """Full-array bulk save / dry-run payload."""

    modules: list[HomeModuleIn]


class HomeModuleAdminOut(Schema):
    """Admin projection of one HomeModule row."""

    key: str
    visible: bool
    order: int
    selection_mode: str
    provenance_note: str


class HomeModulesAdminOut(Schema):
    """GET response: locale-level revision plus every row of the locale."""

    revision: str
    modules: list[HomeModuleAdminOut]


class HomeModulesRevisionOut(Schema):
    """PUT response: the new locale-level revision."""

    revision: str


def _locale_rows(locale: str) -> list[HomeModule]:
    return list(HomeModule.objects.filter(locale=locale).order_by("order", "id"))


def _latest_updated_at(rows: list[HomeModule]) -> datetime | None:
    return max((row.updated_at for row in rows), default=None)


def _current_revision(rows: list[HomeModule]) -> str:
    current = _latest_updated_at(rows)
    return "" if current is None else _format_revision(current)


def _require_valid_locale(locale: str) -> None:
    if locale not in VALID_LOCALES:
        raise AdminError(404, NOT_FOUND, "Unknown locale.")


def _require_current_revision(request, rows: list[HomeModule]) -> None:
    """If-Match gate: the locale revision is the latest row ``updated_at``."""
    _require_if_match(
        request,
        current=_latest_updated_at(rows),
        missing_message="An If-Match revision is required. GET the composition first.",
        stale_message="The home composition was modified by someone else.",
    )


def _validate_modules(modules: list[HomeModuleIn]) -> None:
    """Pure validation: canonical keys, selection_mode enum, order permutation.

    Collects every problem into one ProblemDetails body so the client can
    render all hints at once; raises 400 VALIDATION when any problem exists.
    """
    problems: dict[str, list[str]] = {}
    seen_keys: set[str] = set()
    orders: list[int] = []
    for index, module in enumerate(modules):
        path = f"modules[{index}]"
        if module.key not in CANONICAL_KEYS:
            problems.setdefault(f"{path}.key", []).append(UNKNOWN_KEY)
        elif module.key in seen_keys:
            problems.setdefault(f"{path}.key", []).append(DUPLICATE_KEY)
        seen_keys.add(module.key)
        orders.append(module.order)
        if module.selection_mode not in VALID_SELECTION_MODES:
            problems.setdefault(f"{path}.selection_mode", []).append(BAD_ENUM)
        if len(module.provenance_note) > PROVENANCE_NOTE_MAX:
            problems.setdefault(f"{path}.provenance_note", []).append(TOO_LONG)
    count = len(orders)
    if count and (len(set(orders)) != count or sorted(orders) != list(range(1, count + 1))):
        problems.setdefault("modules", []).append(DUPLICATE_ORDER)
    if problems:
        raise AdminError(
            400,
            VALIDATION,
            "Home module payload failed validation.",
            fields=problems,
        )


def _save_modules(
    locale: str, modules: list[HomeModuleIn], rows: list[HomeModule]
) -> list[HomeModule]:
    """Upsert/delete inside one transaction so the payload matches exactly."""
    with transaction.atomic():
        existing = {
            row.key: row
            for row in HomeModule.objects.select_for_update().filter(locale=locale)
        }
        payload_by_key = {module.key: module for module in modules}
        for key, row in existing.items():
            if key not in payload_by_key:
                row.delete()
        for key, module in payload_by_key.items():
            row = existing.get(key)
            if row is None:
                HomeModule.objects.create(
                    locale=locale,
                    key=key,
                    visible=module.visible,
                    order=module.order,
                    selection_mode=module.selection_mode,
                    provenance_note=module.provenance_note,
                )
            else:
                row.visible = module.visible
                row.order = module.order
                row.selection_mode = module.selection_mode
                row.provenance_note = module.provenance_note
                row.save(
                    update_fields=[
                        "visible",
                        "order",
                        "selection_mode",
                        "provenance_note",
                        "updated_at",
                    ]
                )
        return _locale_rows(locale)


@home_router.get(
    "/{locale}",
    response=HomeModulesAdminOut,
    summary="Every home module row of a locale (draft+published), ordered.",
)
def home_modules_get(request, locale: str):
    _require_admin_otp(request)
    _require_valid_locale(locale)
    rows = _locale_rows(locale)
    return HomeModulesAdminOut(
        revision=_current_revision(rows),
        modules=[
            HomeModuleAdminOut(
                key=row.key,
                visible=row.visible,
                order=row.order,
                selection_mode=row.selection_mode,
                provenance_note=row.provenance_note,
            )
            for row in rows
        ],
    )


@home_router.put(
    "/{locale}",
    response=HomeModulesRevisionOut,
    summary="Full-array bulk save with optimistic locking via If-Match.",
)
def home_modules_put(request, locale: str, payload: HomeModulesPutIn):
    _require_admin_otp(request)
    _check_csrf(request)
    _require_valid_locale(locale)
    _validate_modules(payload.modules)
    rows = _locale_rows(locale)
    _require_current_revision(request, rows)
    fresh = _save_modules(locale, payload.modules, rows)
    _audit_log(
        request,
        action="home_modules.update",
        model_name="home",
        object_id=locale,
        detail=f"PUT /api/v1/admin/home-modules/{locale} -> 200; modules={len(payload.modules)}",
    )
    return HomeModulesRevisionOut(revision=_current_revision(fresh))


@home_router.post(
    "/{locale}/validate",
    summary="Dry-run validation of a home composition payload (no writes).",
)
def home_modules_validate(request, locale: str, payload: HomeModulesPutIn):
    _require_admin_otp(request)
    _check_csrf(request)
    _require_valid_locale(locale)
    _validate_modules(payload.modules)
    return {}
