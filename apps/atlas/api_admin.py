"""The Knowledge Atlas admin authoring router (Plan B, spec §9).

One router mounted at ``/api/v1/admin/atlas/*`` beside the existing
``/graph`` authoring surface. It carries **no business rules**: lifecycle
logic lives in :mod:`apps.atlas.services`, validation in
:mod:`apps.atlas.validation`, layout in :mod:`apps.atlas.layout` and the
wire payload in :mod:`apps.atlas.projection` — all Plan A modules consumed,
never re-implemented.

Shared admin primitives (``AdminError``, the guards, the audit writer) come
from :mod:`apps.api.admin_common` — the exact helper set ``/graph`` uses.
Three local compositions exist and are the only atlas-specific pieces of
this module:

* :func:`_require_current_revision` — the If-Match gate composed with the
  *Atlas wire revision* (``services.version_revision`` → ``"<pk>-<ISO>"``).
  The shared ``_require_if_match`` primitive itself is untouched (428
  ``PRECONDITION_REQUIRED`` when the header is missing, 409
  ``STALE_REVISION`` on mismatch — reuse of its error classes and status
  codes, per the plan). What adapts locally is the *comparison*: the Atlas
  revision is a compound string, so the header is validated structurally
  (``<pk>-<ISO-timestamp>``), its version-id component is checked against
  the addressed version, and its timestamp component is delegated to the
  existing millisecond-precision datetime primitive
  (``_revisions_match``). The wire value the client sends stays exactly
  ``version_revision()``'s output — the accepted client contract is not
  changed to plain ``updated_at``.
* :func:`_require_draft` — enforces the existing Atlas lifecycle: only a
  ``draft`` version is editable, an active (or archived) one answers
  409 ``IMMUTABLE_ACTIVE``. No parallel status model is introduced.
* :func:`_atlas_audit` — the shared audit writer bound to
  ``action="atlas.<verb>"`` rows.

Guard order follows the plan's precondition matrix: resolution (404) →
authenticated OTP session → CSRF → If-Match current revision → draft-only.
A guard failure raises before any model write, so a failed guarded mutation
produces **no** success ``AuditLog`` row (an ``AtlasVersion`` save only
bumps ``updated_at`` through ``auto_now`` — never audit-tagged).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from ninja import Field, Router, Schema

from apps.api.admin_common import (
    IMMUTABLE_ACTIVE,
    PRECONDITION_REQUIRED,
    STALE_REVISION,
    TAXONOMY_IN_USE,
    VALIDATION,
    VALIDATION_BLOCKED,
    AdminError,
    _audit_log,
    _check_csrf,
    _format_revision,
    _require_admin_otp,
)
from apps.atlas import keys
from apps.atlas.models import (
    AtlasGroup,
    AtlasGroupMembership,
    AtlasNode,
    AtlasNodeTranslation,
    AtlasNodeType,
    AtlasRelation,
    AtlasRelationType,
    AtlasVersion,
)
from apps.atlas.services import (
    VERSION_STATUSES,
    AlreadyActive,
    PreconditionFailed,
    ValidationFailed,
    activate_version,
    clone_version,
    recompute_layout,
    version_revision,
)
from apps.atlas.validation import _issue_dict as _atlas_issue_dict

atlas_router = Router()


# ---------------------------------------------------------------------------
# The Task-1 helpers (plan-owned: the per-route precondition + draft gate).
# ---------------------------------------------------------------------------


def _split_atlas_revision(received: str) -> tuple[str | None, datetime | None]:
    """Split an Atlas wire revision into ``(version_id, updated_at)``.

    The accepted Atlas revision (Plan A's accepted wire value,
    :func:`services.version_revision`) is ``"<pk>-<updated_at ISO>"`` —
    one ``-`` joining a positive-integer pk and an ISO-8601 timestamp.
    Returns ``(None, None)`` for any string that is not that shape, so the
    caller answers the existing stale-revision semantics instead of
    crashing or accepting.
    """
    pk_text, sep, stamp_text = received.partition("-")
    if not sep or not stamp_text or not pk_text.isdigit():
        return (None, None)
    try:
        stamp = datetime.fromisoformat(stamp_text)
    except ValueError:
        return (None, None)
    return (pk_text, stamp)


def _require_current_revision(request, version: AtlasVersion) -> None:
    """If-Match gate for one Atlas version, in the accepted wire contract.

    The client-facing revision value is Atlas':
    :func:`services.version_revision` → ``"<pk>-<updated_at ISO>"``. This
    composition adapts the *Atlas* revision to the shared primitive
    locally (never changing the shared gate's external semantics):

    1. missing header → the shared gate's 428 ``PRECONDITION_REQUIRED``;
    2. headline that is not ``<pk>-<ISO>`` → 409 ``STALE_REVISION``;
    3. pk component != the addressed version's pk → 409 ``STALE_REVISION``;
    4. timestamp component compared EXACTLY (Python ``==`` on aware
       datetimes) against ``version.updated_at`` — the Atlas wire revision
       is a full-microsecond ISO stamp, so the shared gate's millisecond
       rounding would let a genuinely stale revision within the same
       millisecond through; a mismatch → 409 ``STALE_REVISION``.
    """
    header = request.headers.get("If-Match")
    if header is None:
        raise AdminError(
            428,
            PRECONDITION_REQUIRED,
            "An If-Match revision is required. GET the Atlas version first.",
        )
    received = header.strip().strip('"')
    version_id, stamp = _split_atlas_revision(received)
    current = version.updated_at
    if version_id != str(version.pk) or stamp is None or stamp != current:
        raise AdminError(
            409,
            STALE_REVISION,
            "The Atlas version was modified by someone else.",
        )


def _require_draft(version: AtlasVersion) -> None:
    """Draft-only editing (plan Task 1): the lifecycle status stays Plan A's."""
    if version.status != "draft":
        raise AdminError(
            409,
            IMMUTABLE_ACTIVE,
            "An active Atlas version cannot be edited. Clone it first.",
        )


def _atlas_audit(
    request, *, action: str, version: AtlasVersion, status: int, detail: str = ""
) -> None:
    """Shared audit writer for the Atlas router rows (``atlas.<verb>``)."""
    _audit_log(
        request,
        action=action,
        model_name="atlas",
        object_id=str(version.pk),
        detail=(detail or f"{request.method} /api/v1/admin/atlas/... -> {status}"),
    )


# ---------------------------------------------------------------------------
# Schemas (Task 1 carries the row schema the list route needs; later tasks
# extend this module — never re-declare these).
# ---------------------------------------------------------------------------


class AtlasVersionRowOut(Schema):
    """One row of ``GET /versions`` — camelCase on the wire (§20.3)."""

    id: int
    status: str
    label: str
    revision: str
    createdAt: str
    updatedAt: str
    nodeCount: int
    relationCount: int


class AtlasVersionDetailOut(AtlasVersionRowOut):
    """Detail row: publishedAt instead of createdAt duplication (plan Task 2)."""

    publishedAt: str | None = None


class AtlasVersionCreateIn(Schema):
    """POST /versions body — a draft starts with only a label."""

    label: str = Field(min_length=1, max_length=120)


class AtlasNodePinIn(Schema):
    """An optional layout pin.

    Fields are optional at the SCHEMA level on purpose: the paired rule
    ("x and y together; z only with both") is the MODEL's ``clean()`` rule,
    which surfaces as 400 ``VALIDATION`` with ``fields`` (plan Task 3); a
    pin carrying only ``x`` must reach the model, not die at a schema 422.
    """

    x: float | None = None
    y: float | None = None
    z: float | None = None


class AtlasNodeOverridesIn(Schema):
    """Per-locale overrides of one locale (spec §5): upserted translations."""

    label: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None)
    accessibleLabel: str | None = Field(default=None, max_length=300)
    aliases: list[str] | None = Field(default=None)


class AtlasNodeWriteIn(Schema):
    """POST /versions/<id>/nodes body (spec §5 fields, camelCase on the wire)."""

    nodeTypeKey: str
    canonicalSource: str | None = Field(default=None, max_length=32)
    canonicalTranslationKey: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    )
    importance: int | None = Field(default=None, ge=0, le=100)
    visible: bool | None = None
    mobileOverviewPriority: str | None = Field(default=None, max_length=16)
    groupKeys: list[str] | None = Field(default=None)
    pin: AtlasNodePinIn | None = Field(default=None)
    overrides: dict[str, AtlasNodeOverridesIn] | None = Field(default=None)


class AtlasNodePatchIn(Schema):
    """PATCH body — every field optional; ``publicKey`` is deliberately absent
    (a node key is minted once and immutable: a PATCH carrying it answers 400)."""

    canonicalTranslationKey: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    )
    importance: int | None = Field(default=None, ge=0, le=100)
    visible: bool | None = Field(default=None)
    mobileOverviewPriority: str | None = Field(default=None, max_length=16)
    groupKeys: list[str] | None = Field(default=None)
    pin: AtlasNodePinIn | None = Field(default=None)
    overrides: dict[str, AtlasNodeOverridesIn] | None = Field(default=None)


class AtlasNodeRowOut(Schema):
    """One node row of the admin surface (Task 3 row shape)."""

    publicKey: str
    nodeTypeKey: str
    canonicalSource: str
    canonicalTranslationKey: str | None = None
    importance: int
    visible: bool
    mobileOverviewPriority: str
    groupKeys: list[str]
    pin: dict | None = None
    localeStatus: dict | None = None


class AtlasCanonicalCandidateOut(Schema):
    """One picker row (plan Task 3): identity + per-locale publish gates."""

    translationKey: str
    title: str
    localeStatus: dict[str, bool]
    publishable: dict[str, bool]


def _version_counts(version: AtlasVersion) -> tuple[int, int]:
    return (version.nodes.count(), version.relations.count())


def _version_detail(version: AtlasVersion) -> AtlasVersionDetailOut:
    """Detail row (Task 2): the row shape plus ``publishedAt``."""
    nodes, relations = _version_counts(version)
    published = version.published_at
    return AtlasVersionDetailOut(
        id=version.pk,
        status=version.status,
        label=version.label,
        revision=version_revision(version),
        createdAt=_format_revision(version.created_at),
        updatedAt=_format_revision(version.updated_at),
        nodeCount=nodes,
        relationCount=relations,
        publishedAt=(_format_revision(published) if published else None),
    )


def _version_row(version: AtlasVersion) -> AtlasVersionRowOut:
    nodes, relations = _version_counts(version)
    revision = version_revision(version)
    return AtlasVersionRowOut(
        id=version.pk,
        status=version.status,
        label=version.label,
        revision=revision,
        createdAt=str(version.created_at.isoformat()),
        updatedAt=str(version.updated_at.isoformat()),
        nodeCount=nodes,
        relationCount=relations,
    )


# ---------------------------------------------------------------------------
# Task-1 routes: the guarded read + the smallest mutation surface the tests
# (and Task 1 alone) exercise — layout POST and node PATCH.
# ---------------------------------------------------------------------------


@atlas_router.get("/versions", response=list[AtlasVersionRowOut])
def list_versions(request):
    _require_admin_otp(request)
    versions = AtlasVersion.objects.order_by("-id")
    return [_version_row(version) for version in versions]




@atlas_router.patch("/versions/{version_id}/nodes/{node_key}", response={200: AtlasNodeRowOut})
def patch_node(request, version_id: int, node_key: str, payload: AtlasNodePatchIn):
    _require_admin_otp(request)
    _check_csrf(request)
    # ``publicKey`` is deliberately absent from AtlasNodePatchIn, but ninja's
    # schema tolerates unknown keys — an attempt to re-key a node must surface
    # (400), never silently succeed with the key ignored.
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    if "publicKey" in body:
        raise AdminError(
            400,
            VALIDATION,
            "A node public key is immutable once it exists.",
            fields={"publicKey": ["cannot be edited."]},
        )
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    node = version.nodes.filter(public_key=node_key).first()
    if node is None:
        raise AdminError(404, "NOT_FOUND", "Atlas node not found.")
    _apply_node_write(node, payload)
    _atlas_audit(
        request,
        action="atlas.node.update",
        version=version,
        status=200,
        detail=f"PATCH /versions/{version.pk}/nodes/{node_key} -> 200",
    )
    return _node_row(node)


# ---------------------------------------------------------------------------
# Task-2 routes: version lifecycle (create / detail / clone / archive).
# List counts/status reuse the Task-1 row serializer; lifecycle rules stay in
# apps.atlas.services (create = a bare draft row; clone = clone_version; the
# one inline status rule is the plan's own: archiving a DRAFT is refused 409).
# ---------------------------------------------------------------------------


@atlas_router.post("/versions", response={201: AtlasVersionDetailOut})
def create_version(request, payload: AtlasVersionCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.create(status=VERSION_STATUSES.DRAFT, label=payload.label)
    _atlas_audit(
        request,
        action="atlas.version.create",
        version=version,
        status=201,
        detail=f"POST /versions label={payload.label!r} -> 201",
    )
    return _version_detail(version)


@atlas_router.get("/versions/{version_id}", response=AtlasVersionDetailOut)
def version_detail(request, version_id: int):
    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    return _version_detail(version)


@atlas_router.post("/versions/{version_id}/clone", response={201: AtlasVersionDetailOut})
def clone_version_route(request, version_id: int, payload: AtlasVersionCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    if not AtlasVersion.objects.filter(pk=version_id).exists():
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    version = clone_version(version_id, payload.label)
    _atlas_audit(
        request,
        action="atlas.version.clone",
        version=version,
        status=201,
        detail=f"POST /versions/{version_id}/clone label={payload.label!r} -> 201",
    )
    return _version_detail(version)


@atlas_router.post("/versions/{version_id}/archive", response={200: AtlasVersionDetailOut})
def archive_version(request, version_id: int):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    if version.status == VERSION_STATUSES.DRAFT:
        raise AdminError(
            409,
            IMMUTABLE_ACTIVE,
            "A draft Atlas version cannot be archived. Delete it instead.",
        )
    version.status = VERSION_STATUSES.ARCHIVED
    version.save(update_fields=["status"])
    _atlas_audit(
        request,
        action="atlas.version.archive",
        version=version,
        status=200,
        detail=f"POST /versions/{version_id}/archive -> 200",
    )
    return _version_detail(version)


# ---------------------------------------------------------------------------
# Task-3 routes: nodes + canonical picker (plan Task 3). Business rules stay in
# the model layer: writes go through full_clean() so ``clean()`` errors surface
# as 400 VALIDATION with ``fields``; overrides upsert AtlasNodeTranslation rows
# and group membership is replaced transactionally; the picker reads the
# canonical allow-list (canonical.CANONICAL_SOURCES) — never invents a record.
# ---------------------------------------------------------------------------
def _require_canonical_publishable(canonical_source: str, translation_key) -> None:
    """The picker rule as a write gate: the record must be publishable per locale.

    A canonical reference that resolves in neither locale is refused (400);
    one that resolves in only ONE locale is allowed — the parity publish gate
    will block publication until the other locale is covered (spec §5.4).
    """
    from apps.atlas.canonical import AmbiguousCanonicalRef, resolve_canonical_pair

    try:
        resolutions = resolve_canonical_pair(canonical_source, translation_key)
    except AmbiguousCanonicalRef as exc:
        raise AdminError(
            400,
            VALIDATION,
            "Ambiguous canonical reference.",
            fields={"canonicalTranslationKey": [str(exc)]},
        ) from exc
    if not any(resolved is not None for resolved in resolutions.values()):
        raise AdminError(
            400,
            VALIDATION,
            "Canonical record is not publishable in any locale.",
            fields={"canonicalTranslationKey": ["no published row for en or fa."]},
        )


def _as_uuid(text: str, field: str):
    try:
        return uuid.UUID(text)
    except ValueError as exc:
        raise AdminError(400, VALIDATION, "Invalid identifier.",
                         fields={field: [f"{text!r} is not a UUID."]}) from exc


def _validation_from_django(exc, *, prefix: str = "", remap: dict | None = None) -> AdminError:
    """Surface a Django ValidationError as 400 VALIDATION with ``fields``.

    ``remap`` renames one model-side field key to the wire key it belongs to
    (the pin's model columns are ``pin_x``/``pin_y``/``pin_z``; the wire is
    one ``pin`` object carrying them).
    """
    field_errors: dict = {}
    if hasattr(exc, "error_dict"):
        items = exc.error_dict.items()
    else:
        items = [("", exc.messages)]
    remap = remap or {}
    for field, errors in items:
        if hasattr(errors, "__iter__"):
            messages = [str(e.message) for e in errors]
        else:
            messages = [str(errors)]
        key = remap.get(field, field)
        key = f"{prefix}.{key}" if prefix else key
        field_errors[key] = field_errors.get(key, []) + messages
    return AdminError(400, VALIDATION, "Validation failed.", fields=field_errors)


def _apply_node_write(node: AtlasNode, payload: AtlasNodePatchIn) -> AtlasNode:
    """Field assignment + nested writes under the model's own rules.

    Identity fields never arrive (the schema omits ``publicKey``), the pin is
    written as a unit (the model's ``clean()`` enforces the paired rule),
    overrides upsert per-locale translation rows in place, and group
    membership is replaced transactionally against same-version groups.
    """
    from apps.atlas.models import (
        MOBILE_OVERVIEW_PRIORITIES,
        AtlasGroupMembership,
        AtlasNodeTranslation,
    )

    if payload.canonicalTranslationKey is not None:
        node.canonical_translation_key = _as_uuid(
            payload.canonicalTranslationKey, "canonicalTranslationKey"
        )
    if payload.importance is not None:
        node.importance = payload.importance
    if payload.visible is not None:
        node.visible = payload.visible
    if payload.mobileOverviewPriority is not None:
        if payload.mobileOverviewPriority not in MOBILE_OVERVIEW_PRIORITIES.values:
            raise AdminError(
                400,
                VALIDATION,
                "Unknown mobileOverviewPriority.",
                fields={"mobileOverviewPriority": ["must be one of auto/featured/hidden."]},
            )
        node.mobile_overview_priority = payload.mobileOverviewPriority
    if payload.pin is not None:
        node.pin_x = payload.pin.x
        node.pin_y = payload.pin.y
        node.pin_z = payload.pin.z

    try:
        node.full_clean()
        node.save()
    except DjangoValidationError as exc:
            raise _validation_from_django(
            exc, remap={"pin_x": "pin", "pin_y": "pin", "pin_z": "pin"}
        ) from exc

    overrides = payload.overrides or {}
    for locale, override in overrides.items():
        if locale not in ("en", "fa"):
            raise AdminError(
                400,
                VALIDATION,
                "Unknown override locale.",
                fields={"overrides": [f"unsupported locale {locale!r}."]},
            )
        row, _created = AtlasNodeTranslation.objects.get_or_create(node=node, locale=locale)
        touched: list[str] = []
        for attr, wire in (
            ("label_override", "label"),
            ("summary_override", "summary"),
            ("accessible_label_override", "accessibleLabel"),
            ("aliases", "aliases"),
        ):
            value = getattr(override, wire, None)
            if value is not None:
                setattr(row, attr, value)
                touched.append(attr)
        try:
            row.full_clean()
            row.save()
        except DjangoValidationError as exc:
            raise _validation_from_django(exc, prefix=f"overrides.{locale}") from exc

    if payload.groupKeys is not None:
        keys = list(dict.fromkeys(payload.groupKeys))
        groups = list(AtlasGroup.objects.filter(version=node.version, public_key__in=keys))
        known = {g.public_key for g in groups}
        if set(keys) != known:
            unknown = [k for k in keys if k not in known]
            raise AdminError(
                400,
                VALIDATION,
                "Unknown groupKeys.",
                fields={"groupKeys": [f"no group {k!r} in this version." for k in unknown]},
            )
        with transaction.atomic():
            AtlasGroupMembership.objects.filter(node=node).exclude(
                group__public_key__in=keys
            ).delete()
            existing = set(
                AtlasGroupMembership.objects.filter(node=node).values_list(
                    "group__public_key", flat=True
                )
            )
            for sort_order, group_key in enumerate(keys):
                group = next(g for g in groups if g.public_key == group_key)
                if group_key not in existing:
                    AtlasGroupMembership.objects.create(
                        group=group, node=node, sort_order=sort_order
                    )
    return node


def _node_row(node: AtlasNode) -> AtlasNodeRowOut:
    """One node row — group keys and the per-locale publish gates included."""
    from apps.atlas.canonical import resolve_canonical_pair

    groups = list(
        AtlasGroupMembership.objects.filter(node=node)
        .select_related("group")
        .order_by("sort_order")
        .values_list("group__public_key", flat=True)
    )
    pin = None
    if node.pin_x is not None and node.pin_y is not None:
        pin = {"x": node.pin_x, "y": node.pin_y, "z": node.pin_z}
    resolutions = resolve_canonical_pair(
        node.canonical_model, node.canonical_translation_key
    )
    locale_status = {
        locale: resolved is not None for locale, resolved in resolutions.items()
    }
    return AtlasNodeRowOut(
        publicKey=node.public_key,
        nodeTypeKey=node.node_type.key,
        canonicalSource=node.canonical_model,
        canonicalTranslationKey=(
            str(node.canonical_translation_key) if node.canonical_translation_key else None
        ),
        importance=node.importance,
        visible=node.visible,
        mobileOverviewPriority=node.mobile_overview_priority,
        groupKeys=list(groups),
        pin=pin,
        localeStatus=locale_status,
    )


@atlas_router.get("/versions/{version_id}/nodes", response=list[AtlasNodeRowOut])
def list_nodes(request, version_id: int):
    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    return [
        _node_row(node)
        for node in version.nodes.select_related("node_type").order_by("public_key")
    ]


@atlas_router.post("/versions/{version_id}/nodes", response={201: AtlasNodeRowOut})
def create_node(request, version_id: int, payload: AtlasNodeWriteIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)

    node_type = AtlasNodeType.objects.filter(key=payload.nodeTypeKey).first()
    if node_type is None:
        raise AdminError(
            400,
            VALIDATION,
            "Unknown nodeTypeKey.",
            fields={"nodeTypeKey": [f"no node type {payload.nodeTypeKey!r}."]},
        )
    if payload.canonicalSource and payload.canonicalSource != node_type.canonical_source:
        raise AdminError(
            400,
            VALIDATION,
            "canonicalSource must equal the node type's canonical_source.",
            fields={"canonicalSource": [f"expected {node_type.canonical_source!r}."]},
        )
    translation_key = (
        _as_uuid(payload.canonicalTranslationKey, "canonicalTranslationKey")
        if payload.canonicalTranslationKey
        else None
    )
    # A canonical node must resolve in BOTH locales (the picker can never
    # publish a half-resolvable record: spec §5.4, card rule).
    if node_type.canonical_source != "none":
        if translation_key is None:
            raise AdminError(
                400,
                VALIDATION,
                "A canonical node requires canonicalTranslationKey.",
                fields={"canonicalTranslationKey": ["required for this node type."]},
            )
        _require_canonical_publishable(node_type.canonical_source, translation_key)

    node = AtlasNode(
        version=version,
        node_type=node_type,
        public_key=keys.new_node_key(node_type.key),
        canonical_model=node_type.canonical_source,
        canonical_translation_key=translation_key,
    )
    patch = AtlasNodePatchIn(
        importance=payload.importance,
        visible=payload.visible,
        mobileOverviewPriority=payload.mobileOverviewPriority,
        groupKeys=payload.groupKeys,
        pin=payload.pin,
        overrides=payload.overrides,
    )
    node = _apply_node_write(node, patch)
    _atlas_audit(
        request,
        action="atlas.node.create",
        version=version,
        status=201,
        detail=f"POST /versions/{version.pk}/nodes {node.public_key} -> 201",
    )
    return _node_row(node)


@atlas_router.get("/versions/{version_id}/nodes/{node_key}", response=AtlasNodeRowOut)
def get_node(request, version_id: int, node_key: str):
    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    node = version.nodes.filter(public_key=node_key).first()
    if node is None:
        raise AdminError(404, "NOT_FOUND", "Atlas node not found.")
    return _node_row(node)


@atlas_router.delete("/versions/{version_id}/nodes/{node_key}", response={204: None})
def delete_node(request, version_id: int, node_key: str):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    node = version.nodes.filter(public_key=node_key).first()
    if node is None:
        raise AdminError(404, "NOT_FOUND", "Atlas node not found.")
    node.delete()
    _atlas_audit(
        request,
        action="atlas.node.delete",
        version=version,
        status=204,
        detail=f"DELETE /versions/{version.pk}/nodes/{node_key} -> 204",
    )
    return 204, None


@atlas_router.get("/canonical-candidates", response=list[AtlasCanonicalCandidateOut])
def canonical_candidates(request, source: str = "", q: str = ""):
    """The admin picker rows for one canonical family (plan Task 3).

    Reads the canonical allow-list registry; a family outside it is a 404, a
    never-resolving query would invent a record — the picker only ever offers
    rows that exist in the CMS, gated per locale by ``objects.public()``.
    """
    _require_admin_otp(request)
    from django.db.models import Q

    from apps.atlas.canonical import CANONICAL_SOURCES as REGISTRY

    source_key = (source or "").strip()
    if source_key not in REGISTRY:
        raise AdminError(
            404,
            "NOT_FOUND",
            f"Unknown canonical source {source_key!r}; known: {', '.join(REGISTRY)}.",
        )
    model = REGISTRY[source_key]
    queryset = model.objects.public()
    if source_key in ("method", "technology"):
        canonical_field = "short_description"
    else:
        canonical_field = "title"
    if q:
        queryset = queryset.filter(
            Q(title__icontains=q) | Q(**{f"{canonical_field}__icontains": q})
        )
    rows: dict[str, dict] = {}
    for row in queryset.distinct():
        key_text = str(row.translation_key)
        bucket = rows.setdefault(
            key_text,
            {
                "translationKey": key_text,
                "title": str(row.title or ""),
                "localeStatus": {"en": False, "fa": False},
                "publishable": {"en": False, "fa": False},
            },
        )
        locale = row.locale or "en"
        is_public = row in model.objects.public().filter(
            translation_key=row.translation_key, locale=locale
        )
        bucket["localeStatus"][locale] = True
        bucket["publishable"][locale] = is_public
    return [rows[k] for k in sorted(rows)]


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task-4 routes: relations, group membership, taxonomy CRUD (plan Task 4).
# A write that would leave the graph invalid answers 409 VALIDATION_BLOCKED
# with Plan A's issue array (validate_version); ProtectedError and in-use
# taxonomy rows answer 409 TAXONOMY_IN_USE (Task 4 step 3).
# ---------------------------------------------------------------------------


class AtlasRelationWriteIn(Schema):
    """POST /versions/<id>/relations body (spec §5.3, camelCase on the wire)."""

    sourceKey: str
    relationTypeKey: str
    targetKey: str
    directed: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=100)
    visible: bool | None = None
    explanation: dict[str, str] | None = Field(default=None)


class AtlasRelationPatchIn(Schema):
    """PATCH body — structural keys change nothing; only mutable fields land."""

    directed: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=100)
    visible: bool | None = None
    explanation: dict[str, str | None] | None = Field(default=None)


class AtlasRelationRowOut(Schema):
    """One relation row — the key is the composed public key (spec §5.3)."""

    key: str
    sourceKey: str
    relationTypeKey: str
    targetKey: str
    directed: bool
    weight: int
    visible: bool


class AtlasGroupWriteIn(Schema):
    label: str = Field(min_length=1, max_length=200)


class AtlasGroupPatchIn(Schema):
    """PATCH groups/{key}: rename per-locale copy (identity key immutable)."""

    labels: dict[str, str] | None = Field(default=None)


class AtlasGroupRowOut(Schema):
    key: str
    label: str
    memberKeys: list[str]



class AtlasMembersIn(Schema):
    nodeKeys: list[str]



def _resolve_relation_endpoints(version: AtlasVersion, payload: AtlasRelationWriteIn) -> dict:
    """404-check both endpoint keys and reload the relation type."""
    source = version.nodes.filter(public_key=payload.sourceKey).first()
    if source is None:
        raise AdminError(400, VALIDATION, "Unknown sourceKey.",
                         fields={"sourceKey": [f"no node {payload.sourceKey!r} in this version."]})
    target = version.nodes.filter(public_key=payload.targetKey).first()
    if target is None:
        raise AdminError(400, VALIDATION, "Unknown targetKey.",
                         fields={"targetKey": [f"no node {payload.targetKey!r} in this version."]})
    relation_type = AtlasRelationType.objects.filter(key=payload.relationTypeKey).first()
    if relation_type is None:
        raise AdminError(400, VALIDATION, "Unknown relationTypeKey.",
        fields={"relationTypeKey": [f"no relation type {payload.relationTypeKey!r}."]},
    )
    if not relation_type.active:
        raise AdminError(400, VALIDATION, "Inactive relation type.",
                         fields={"relationTypeKey": [f"{payload.relationTypeKey!r} is retired."]})
    # The taxonomy active gate spans the TYPES of the endpoint nodes (plan Task 4:
    # "inactive taxonomy cannot be referenced" — a retired node TYPE cannot
    # participate in a NEW relation).
    for endpoint, node in (("sourceKey", source), ("targetKey", target)):
        if not node.node_type.active:
            raise AdminError(400, VALIDATION, "Inactive node type.",
                             fields={endpoint: [f"{node.node_type.key!r} is retired."]})
    return {"source": source, "target": target, "relation_type": relation_type}



def _issue_template(relation_key: str) -> dict:
    """One RELATION_TYPE_NOT_ALLOWED issue in the wire spelling (spec §20)."""
    return {
        "code": "RELATION_TYPE_NOT_ALLOWED",
        "relationKey": relation_key,
        "messageToken": "atlas.relationTypeNotAllowed",
    }


def _build_relation(
    version: AtlasVersion, payload: AtlasRelationWriteIn, checks: dict
) -> AtlasRelation:
    """Author a relation under the model's own rules + Plan A's pair battery.

    Pair rule / self-loop / stale-taxonomy answers live here because they are
    the write-time guards Plan A's validation mirrors; the full-graph verdict
    is the aggregate report (blocked write → 409 with the issue list).
    """
    from apps.atlas.models import (
        AtlasRelation,
        AtlasRelationTranslation,
    )
    from apps.atlas.validation import allowed_types_by_relation_type, validate_version

    relation_type = checks["relation_type"]
    allowed = allowed_types_by_relation_type([relation_type.pk])[relation_type.pk]
    source, target = checks["source"], checks["target"]
    composed = keys.relation_public_key(
        payload.sourceKey, payload.relationTypeKey, payload.targetKey,
        directed=(payload.directed if payload.directed is not None
                  else relation_type.directed_default),
    )
    blocked_issues = []
    if allowed.sources and source.node_type_id not in allowed.sources:
        blocked_issues.append(composed)
    if allowed.targets and target.node_type_id not in allowed.targets:
        blocked_issues.append(composed)
    if blocked_issues:
        raise AdminError(
            409,
            VALIDATION_BLOCKED,
            "Relation type is not allowed for this endpoint pair.",
            issues=[_issue_template(i) for i in blocked_issues],
        )
    directed = payload.directed
    if directed is None:
        directed = relation_type.directed_default
    if directed != relation_type.directed_default and not relation_type.overridable_direction:
        raise AdminError(
            400,
            VALIDATION,
            "This relation type does not allow overriding direction.",
            fields={"directed": [f"{relation_type.key!r} fixes direction."]},
        )
    relation = AtlasRelation(
        version=version,
        source=source,
        target=target,
        relation_type=relation_type,
        directed=directed,
        weight=payload.weight if payload.weight is not None else relation_type.default_weight,
        visible=True if payload.visible is None else payload.visible,
    )
    try:
        relation.full_clean()
    except DjangoValidationError as exc:
        raise _validation_from_django(exc) from exc
    relation.save()
    for locale, explanation in (payload.explanation or {}).items():
        if locale not in ("en", "fa"):
            raise AdminError(400, VALIDATION, "Unknown explanation locale.",
                             fields={"explanation": [f"unsupported locale {locale!r}."]})
        AtlasRelationTranslation.objects.create(
            relation=relation, locale=locale, explanation=str(explanation or ""),
        )
    validate_version(version)  # the graph-level battery — surfaced by validate endpoint (Task 5)
    return relation


@atlas_router.get("/versions/{version_id}/relations", response=list[AtlasRelationRowOut])
def list_relations(request, version_id: int):
    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    rows = []
    for relation in version.relations.select_related(
        "relation_type"
    ).order_by("sort_order", "id"):
        source = version.nodes.get(pk=relation.source_id)
        target = version.nodes.get(pk=relation.target_id)
        rows.append(AtlasRelationRowOut(
            key=relation.public_key,
            sourceKey=source.public_key,
            relationTypeKey=relation.relation_type.key,
            targetKey=target.public_key,
            directed=relation.directed,
            weight=relation.weight,
            visible=relation.visible,
        ))
    return rows


@atlas_router.post("/versions/{version_id}/relations", response={201: AtlasRelationRowOut})
def create_relation(request, version_id: int, payload: AtlasRelationWriteIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    checks = _resolve_relation_endpoints(version, payload)
    relation = _build_relation(version, payload, checks)
    _atlas_audit(
        request,
        action="atlas.relation.create",
        version=version,
        status=201,
        detail=f"POST /versions/{version.pk}/relations {relation.public_key} -> 201",
    )
    return _relation_row(relation)


def _relation_row(relation) -> AtlasRelationRowOut:
    return AtlasRelationRowOut(
        key=relation.public_key,
        sourceKey=relation.source.public_key,
        relationTypeKey=relation.relation_type.key,
        targetKey=relation.target.public_key,
        directed=relation.directed,
        weight=relation.weight,
        visible=relation.visible,
    )




@atlas_router.put("/versions/{version_id}/groups/{group_key}/members", response=AtlasGroupRowOut)
def replace_group_members(request, version_id: int, group_key: str, payload: AtlasMembersIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    group = version.groups.filter(public_key=group_key).first()
    if group is None:
        raise AdminError(404, "NOT_FOUND", "Atlas group not found.")
    node_keys = list(dict.fromkeys(payload.nodeKeys))
    nodes = list(version.nodes.filter(public_key__in=node_keys))
    found = {node.public_key for node in nodes}
    if set(node_keys) != found:
        unknown = [node_key for node_key in node_keys if node_key not in found]
        raise AdminError(
            400,
            VALIDATION,
            "Unknown nodeKeys.",
            fields={
                "nodeKeys": [f"no node {key!r} in this version." for key in unknown]
            },
        )
    with transaction.atomic():
        AtlasGroupMembership.objects.filter(group=group).exclude(
            node__public_key__in=node_keys
        ).delete()
        nodes_by_key = {node.public_key: node for node in nodes}
        for sort_order, node_key in enumerate(node_keys):
            node = nodes_by_key[node_key]
            AtlasGroupMembership.objects.update_or_create(
                group=group, node=node, defaults={"sort_order": sort_order}
            )
    _atlas_audit(
        request,
        action="atlas.group.members.replace",
        version=version,
        status=200,
        detail=f"PUT /versions/{version.pk}/groups/{group_key}/members -> 200",
    )
    return _group_row(group)


def _group_row(group) -> AtlasGroupRowOut:
    from apps.atlas.models import AtlasGroupTranslation

    label_row = AtlasGroupTranslation.objects.filter(group=group, locale="en").first()
    return AtlasGroupRowOut(
        key=group.public_key,
        label=label_row.label if label_row else "",
        memberKeys=list(
            AtlasGroupMembership.objects.filter(group=group)
            .order_by("sort_order")
            .values_list("node__public_key", flat=True)
        ),
    )


@atlas_router.delete("/node-types/{type_key}", response={204: None})
def delete_node_type(request, type_key: str):
    _require_admin_otp(request)
    _check_csrf(request)
    node_type = AtlasNodeType.objects.filter(key=type_key).first()
    if node_type is None:
        raise AdminError(404, "NOT_FOUND", "Node type not found.")
    in_use = node_type._referencing_rows_exist()
    if in_use:
        raise AdminError(409, TAXONOMY_IN_USE, "A node type in use cannot be deleted.",
                         fields={"key": [f"{type_key!r} has nodes referencing it."]})
    try:
        node_type.delete()
    except ProtectedError as exc:
        raise AdminError(
            409,
            TAXONOMY_IN_USE,
            "A node type in use cannot be deleted.",
            fields={"key": [f"{type_key!r} has rows referencing it."]},
        ) from exc

    _atlas_audit_generic(
        request,
        action="atlas.nodeType.delete",
        object_id=type_key,
        detail=f"DELETE /node-types/{type_key} -> 204",
    )
    return 204, None


def _atlas_audit_generic(request, *, action: str, object_id: str, detail: str) -> None:
    _audit_log(
        request,
        action=action,
        model_name="atlas",
        object_id=str(object_id),
        detail=detail,
    )


@atlas_router.patch(
    "/versions/{version_id}/relations/{relation_key}", response={200: AtlasRelationRowOut}
)
def patch_relation(request, version_id: int, relation_key: str, payload: AtlasRelationPatchIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    relation = _resolve_relation_by_key(version, relation_key)
    if payload.directed is not None and payload.directed != relation.directed:
        if not relation.relation_type.overridable_direction:
            raise AdminError(400, VALIDATION, "This relation type fixes direction.",
                             fields={"directed": ["not overridable."]})
        relation.directed = payload.directed
    if payload.weight is not None:
        relation.weight = payload.weight
    if payload.visible is not None:
        relation.visible = payload.visible
    try:
        relation.full_clean()
        relation.save(update_fields=["directed", "weight", "visible"])
    except DjangoValidationError as exc:
        raise _validation_from_django(exc) from exc
    _atlas_audit(
        request, action="atlas.relation.update", version=version, status=200,
        detail=f"PATCH relations/{relation_key} -> 200",
    )
    return _relation_row(relation)


def _resolve_relation_by_key(version: AtlasVersion, relation_key: str):
    """Find one relation by its composed public key (nothing is stored)."""
    parts = relation_key.split("~")
    if len(parts) != 3:
        raise AdminError(404, "NOT_FOUND", "Atlas relation not found.")
    first, relation_type_key, second = parts
    for ordered in ((first, second), (second, first)):
        candidates = version.relations.filter(
            relation_type__key=relation_type_key,
            source__public_key=ordered[0],
            target__public_key=ordered[1],
        )
        match = candidates.select_related("relation_type").first()
        if match is not None:
            return match
    raise AdminError(404, "NOT_FOUND", "Atlas relation not found.")


@atlas_router.delete("/versions/{version_id}/relations/{relation_key}", response={204: None})
def delete_relation(request, version_id: int, relation_key: str):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    relation = _resolve_relation_by_key(version, relation_key)
    relation.delete()
    _atlas_audit(
        request, action="atlas.relation.delete", version=version, status=204,
        detail=f"DELETE relations/{relation_key} -> 204",
    )
    return 204, None


@atlas_router.get("/versions/{version_id}/groups", response=list[AtlasGroupRowOut])
def list_groups(request, version_id: int):
    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    return [_group_row(group) for group in version.groups.order_by("sort_order", "id")]


@atlas_router.post("/versions/{version_id}/groups", response={201: AtlasGroupRowOut})
def create_group(request, version_id: int, payload: AtlasGroupWriteIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    from apps.atlas.models import AtlasGroupTranslation

    group = AtlasGroup(version=version, public_key=keys.new_group_key())
    try:
        group.full_clean()
        group.save()
    except DjangoValidationError as exc:
        raise _validation_from_django(exc) from exc
    AtlasGroupTranslation.objects.create(group=group, locale="en", label=payload.label)
    _atlas_audit(
        request, action="atlas.group.create", version=version, status=201,
        detail=f"POST /versions/{version.pk}/groups {group.public_key} -> 201",
    )
    return _group_row(group)


@atlas_router.delete("/versions/{version_id}/groups/{group_key}", response={204: None})
def delete_group(request, version_id: int, group_key: str):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    group = version.groups.filter(public_key=group_key).first()
    if group is None:
        raise AdminError(404, "NOT_FOUND", "Atlas group not found.")
    group.delete()
    _atlas_audit(
        request, action="atlas.group.delete", version=version, status=204,
        detail=f"DELETE groups/{group_key} -> 204",
    )
    return 204, None


# ---------------------------------------------------------------------------
# Task-4 fix round: the taxonomy CRUD the plan pins in full (node-types and
# relation-types, each GET|POST|PATCH|DELETE), plus the group PATCH that makes
# "every authorable semantic has an admin path" true for group copy.
# ---------------------------------------------------------------------------


class AtlasNodeTypeRowOut(Schema):
    key: str
    label_en: str
    label_fa: str
    active: bool
    sort_order: int
    defaultImportance: int = 50
    canonicalSource: str = "none"


class AtlasNodeTypeWriteIn(Schema):
    key: str = Field(min_length=2, max_length=64)
    label_en: str = Field(min_length=1, max_length=120)
    label_fa: str = Field(min_length=1, max_length=120)
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


class AtlasNodeTypePatchIn(Schema):
    label_en: str | None = Field(default=None, max_length=120)
    label_fa: str | None = Field(default=None, max_length=120)
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


class AtlasRelationTypeRowOut(AtlasNodeTypeRowOut):
    key: str
    label_en: str
    label_fa: str
    active: bool
    sort_order: int
    directedDefault: bool = True
    overridableDirection: bool = False
    # Task-12 frontend filters (allowed-pair + hierarchy) read these straight
    # off the row: an empty allowed list means "any active type" (spec §6.2),
    # mirroring validation.AllowedTypes.
    hierarchyRole: bool = False
    allowedSourceTypes: list[str] = []
    allowedTargetTypes: list[str] = []


class AtlasRelationTypeWriteIn(Schema):
    key: str = Field(min_length=2, max_length=64)
    label_en: str = Field(min_length=1, max_length=120)
    label_fa: str = Field(min_length=1, max_length=120)
    directedDefault: bool | None = None
    overridableDirection: bool | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    allowedSourceTypes: list[str] | None = Field(default=None)
    allowedTargetTypes: list[str] | None = Field(default=None)


def _taxonomy_row(row) -> AtlasNodeTypeRowOut | AtlasRelationTypeRowOut:
    """One taxonomy row in the admin surface (wire camelCase where §20.3 needs)."""
    return AtlasNodeTypeRowOut(
        key=row.key,
        label_en=row.label_en,
        label_fa=row.label_fa,
        active=row.active,
        sort_order=row.sort_order,
        defaultImportance=getattr(row, "default_importance", 50),
        canonicalSource=getattr(row, "canonical_source", "none"),
    )


def _relation_taxonomy_row(row) -> AtlasRelationTypeRowOut:
    return AtlasRelationTypeRowOut(
        key=row.key,
        label_en=row.label_en,
        label_fa=row.label_fa,
        active=row.active,
        sort_order=row.sort_order,
        directedDefault=row.directed_default,
        overridableDirection=row.overridable_direction,
        hierarchyRole=row.hierarchy_role,
        allowedSourceTypes=sorted(
            t.key for t in row.allowed_source_types.all()
        ),
        allowedTargetTypes=sorted(
            t.key for t in row.allowed_target_types.all()
        ),
    )


def _taxonomy_delete(request, model, type_key: str, *, audit_action: str):
    row = model.objects.filter(key=type_key).first()
    if row is None:
        raise AdminError(404, "NOT_FOUND", "Taxonomy row not found.")
    if row._referencing_rows_exist():
        raise AdminError(
            409,
            TAXONOMY_IN_USE,
            "A taxonomy row in use cannot be deleted.",
            fields={"key": [f"{type_key!r} has rows referencing it."]},
        )
    try:
        row.delete()
    except ProtectedError as exc:
        raise AdminError(
            409,
            TAXONOMY_IN_USE,
            "A taxonomy row in use cannot be deleted.",
            fields={"key": [f"{type_key!r} has rows referencing it."]},
        ) from exc
    _atlas_audit_generic(
        request, action=audit_action, object_id=type_key,
        detail=f"DELETE {audit_action.split('.')[-1]} {type_key} -> 204",
    )
    return 204, None


@atlas_router.get("/node-types", response=list[AtlasNodeTypeRowOut])
def list_node_types(request):
    _require_admin_otp(request)
    return [_taxonomy_row(row) for row in AtlasNodeType.objects.order_by("sort_order", "key")]


@atlas_router.post("/node-types", response={201: AtlasNodeTypeRowOut})
def create_node_type(request, payload: AtlasNodeTypeWriteIn):
    _require_admin_otp(request)
    _check_csrf(request)
    row = AtlasNodeType(
        key=payload.key,
        label_en=payload.label_en,
        label_fa=payload.label_fa,
        semantic_role="record",
        visual_role="record",
        active=payload.active if payload.active is not None else True,
        sort_order=payload.sort_order if payload.sort_order is not None else 0,
    )
    try:
        row.full_clean()
        row.save()
    except (DjangoValidationError, IntegrityError) as exc:
        if isinstance(exc, IntegrityError):
            raise AdminError(
                400,
                VALIDATION,
                "Duplicate taxonomy key.",
                fields={"key": [f"{payload.key!r} already exists."]},
            ) from exc
        raise _validation_from_django(exc) from exc
    _atlas_audit_generic(request, action="atlas.nodeType.create",
                         object_id=row.key, detail=f"POST /node-types {row.key} -> 201")
    return _taxonomy_row(row)


@atlas_router.patch("/node-types/{type_key}", response=AtlasNodeTypeRowOut)
def patch_node_type(request, type_key: str, payload: AtlasNodeTypePatchIn):
    _require_admin_otp(request)
    _check_csrf(request)
    node_type = AtlasNodeType.objects.filter(key=type_key).first()
    if node_type is None:
        raise AdminError(404, "NOT_FOUND", "Node type not found.")
    if payload.label_en is not None:
        node_type.label_en = payload.label_en
    if payload.label_fa is not None:
        node_type.label_fa = payload.label_fa
    if payload.active is not None:
        if node_type._referencing_rows_exist() and not payload.active:
            raise AdminError(409, TAXONOMY_IN_USE, "A node type in use cannot be retired.",
                             fields={"active": [f"{type_key!r} still has nodes."]})
        node_type.active = payload.active
    if payload.sort_order is not None:
        node_type.sort_order = payload.sort_order
    try:
        node_type.full_clean()
        node_type.save()
    except DjangoValidationError as exc:
        raise _validation_from_django(exc) from exc
    _atlas_audit_generic(request, action="atlas.nodeType.update", object_id=type_key,
                         detail=f"PATCH /node-types/{type_key} -> 200")
    return _taxonomy_row(node_type)


@atlas_router.delete("/relation-types/{type_key}", response={204: None})
def delete_relation_type(request, type_key: str):
    _require_admin_otp(request)
    _check_csrf(request)
    return _taxonomy_delete(request, AtlasRelationType, type_key,
                            audit_action="atlas.relationType.delete")


@atlas_router.get("/relation-types", response=list[AtlasRelationTypeRowOut])
def list_relation_types(request):
    _require_admin_otp(request)
    rows = AtlasRelationType.objects.prefetch_related(
        "allowed_source_types", "allowed_target_types"
    ).order_by("sort_order", "key")
    return [_relation_taxonomy_row(row) for row in rows]


@atlas_router.post("/relation-types", response={201: AtlasRelationTypeRowOut})
def create_relation_type(request, payload: AtlasRelationTypeWriteIn):
    _require_admin_otp(request)
    _check_csrf(request)
    row = AtlasRelationType(
        key=payload.key,
        label_en=payload.label_en,
        label_fa=payload.label_fa,
        inverse_label_en=f"Inverse of {payload.label_en}",
        inverse_label_fa=f"معکوسِ {payload.label_fa}",
        semantic_role="utility",
        directed_default=payload.directedDefault if payload.directedDefault is not None else True,
        overridable_direction=payload.overridableDirection or False,
        active=payload.active if payload.active is not None else True,
        sort_order=payload.sort_order if payload.sort_order is not None else 0,
    )
    try:
        row.full_clean()
        row.save()
    except DjangoValidationError as exc:
        raise _validation_from_django(exc) from exc
    for source_key in (payload.allowedSourceTypes or []):
        node_type = AtlasNodeType.objects.filter(key=source_key).first()
        if node_type is None:
            raise AdminError(400, VALIDATION, "Unknown allowedSourceTypes entry.",
                             fields={"allowedSourceTypes": [f"no node type {source_key!r}."]})
        row.allowed_source_types.add(node_type)
    for target_key in (payload.allowedTargetTypes or []):
        node_type = AtlasNodeType.objects.filter(key=target_key).first()
        if node_type is None:
            raise AdminError(400, VALIDATION, "Unknown allowedTargetTypes entry.",
                             fields={"allowedTargetTypes": [f"no node type {target_key!r}."]})
        row.allowed_target_types.add(node_type)
    _atlas_audit_generic(request, action="atlas.relationType.create", object_id=row.key,
                         detail=f"POST /relation-types {row.key} -> 201")
    return _relation_taxonomy_row(row)


@atlas_router.patch("/relation-types/{type_key}", response=AtlasRelationTypeRowOut)
def patch_relation_type(request, type_key: str, payload: AtlasNodeTypePatchIn):
    _require_admin_otp(request)
    _check_csrf(request)
    row = AtlasRelationType.objects.filter(key=type_key).first()
    if row is None:
        raise AdminError(404, "NOT_FOUND", "Relation type not found.")
    if payload.label_en is not None:
        row.label_en = payload.label_en
    if payload.label_fa is not None:
        row.label_fa = payload.label_fa
    if payload.active is not None:
        if row._referencing_rows_exist() and not payload.active:
            raise AdminError(409, TAXONOMY_IN_USE, "A relation type in use cannot be retired.",
                             fields={"active": [f"{type_key!r} still has relations using it."]})
        row.active = payload.active
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    try:
        row.full_clean()
        row.save()
    except DjangoValidationError as exc:
        raise _validation_from_django(exc) from exc
    _atlas_audit_generic(request, action="atlas.relationType.update", object_id=row.key,
                         detail=f"PATCH /relation-types/{type_key} -> 200")
    return _relation_taxonomy_row(row)


@atlas_router.patch("/versions/{version_id}/groups/{group_key}", response=AtlasGroupRowOut)
def patch_group(request, version_id: int, group_key: str, payload: AtlasGroupPatchIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    group = version.groups.filter(public_key=group_key).first()
    if group is None:
        raise AdminError(404, "NOT_FOUND", "Atlas group not found.")
    from apps.atlas.models import AtlasGroupTranslation

    for locale, label in (payload.labels or {}).items():
        if locale not in ("en", "fa"):
            raise AdminError(400, VALIDATION, "Unknown label locale.",
                             fields={"labels": [f"unsupported locale {locale!r}."]})
        translation, _created = AtlasGroupTranslation.objects.get_or_create(
            group=group, locale=locale
        )
        translation.label = label
        translation.full_clean()
        translation.save()
    _atlas_audit(
        request, action="atlas.group.update", version=version, status=200,
        detail=f"PATCH groups/{group_key} -> 200",
    )
    return _group_row(group)


# ---------------------------------------------------------------------------
# Task-5 routes: layout recompute, bulk graph PUT, validate, activate, status.
# Delegation only: recompute_layout, validate_version(...).to_dict(),
# activate_version + enqueue_publication_job; the service exceptions map 1:1
# to wire codes (ValidationFailed → 409 VALIDATION_BLOCKED, AlreadyActive →
# 409 ALREADY_ACTIVE, PreconditionFailed handled locally by the composition).
# ---------------------------------------------------------------------------




@atlas_router.post("/versions/{version_id}/layout", response={200: dict})
def recompute_version_layout(request, version_id: int):
    """Deterministic layout recompute (plan Task 5): no client-supplied body."""
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    revision = recompute_layout(version)
    version.refresh_from_db()
    _atlas_audit(
        request,
        action="atlas.layout.recompute",
        version=version,
        status=200,
        detail=f"POST /versions/{version.pk}/layout -> revision {revision}",
    )
    return {"layoutRevision": revision, "coordinates": version.layout}


@atlas_router.get("/versions/{version_id}/validate", response={200: dict})
def validate_version_endpoint(request, version_id: int):
    from apps.atlas.validation import validate_version

    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    return validate_version(version).to_dict()


@atlas_router.get("/versions/{version_id}/status", response={200: dict})
def version_status(request, version_id: int):
    from apps.rebuild.models import PublicationJob

    _require_admin_otp(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    job = PublicationJob.objects.filter(
        affected_paths__icontains="/atlas/"
    ).order_by("-id").first()
    return {
        "id": version.pk,
        "status": version.status,
        "publishedAt": _format_revision(version.published_at) if version.published_at else None,
        "jobId": job.pk if job else None,
    }


@atlas_router.post("/versions/{version_id}/activate", response={200: dict})
def activate_version_endpoint(request, version_id: int):
    from apps.rebuild.services import enqueue_publication_job

    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    expected_revision = request.headers.get("If-Match", "").strip().strip('"')
    try:
        activated = activate_version(version, expected_revision=expected_revision)
    except AlreadyActive as exc:
        raise AdminError(409, "ALREADY_ACTIVE", "This version is already the active one.") from exc
    except ValidationFailed as exc:
        raise _serviceBlocked(exc) from exc
    except PreconditionFailed as exc:
        raise AdminError(
            409, STALE_REVISION, "The Atlas version was modified by someone else."
        ) from exc
    job = enqueue_publication_job(
        affected_paths=[
            "/en/atlas/", "/fa/atlas/", "/en/about/", "/fa/about/"
        ],
        requested_revision=str(activated.updated_at),
    )
    _atlas_audit(
        request,
        action="atlas.version.activate",
        version=activated,
        status=200,
        detail=f"POST /versions/{version.pk}/activate -> job {job.pk}",
    )
    activated.refresh_from_db()
    return {
        "id": activated.pk,
        "status": activated.status,
        "publishedAt": _format_revision(activated.published_at),
        "enqueuedPublicationJob": job.pk,
    }


def _serviceBlocked(exc: ValidationFailed) -> AdminError:
    """The publish battery refused — the full Plan A issue array, not codes."""
    from apps.atlas.validation import _issue_dict

    return AdminError(
        409,
        VALIDATION_BLOCKED,
        "The publish battery refused.",
        issues=[_issue_dict(issue) for issue in exc.issues],
    )


class AtlasBulkNodeIn(Schema):
    """One node of a bulk-graph PUT body (identity + mutable fields)."""

    publicKey: str | None = None
    nodeTypeKey: str
    canonicalTranslationKey: str | None = Field(default=None)
    importance: int | None = Field(default=None, ge=0, le=100)
    visible: bool | None = None
    mobileOverviewPriority: str | None = Field(default=None, max_length=16)
    overrides: dict[str, AtlasNodeOverridesIn] | None = Field(default=None)


class AtlasBulkRelationIn(Schema):
    sourceKey: str
    relationTypeKey: str
    targetKey: str
    directed: bool | None = None


class AtlasBulkGroupIn(Schema):
    key: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    nodeKeys: list[str] = Field(default_factory=list)


class AtlasBulkGraphIn(Schema):
    nodes: list[AtlasBulkNodeIn] = Field(default_factory=list)
    relations: list[AtlasBulkRelationIn] = Field(default_factory=list)
    groups: list[AtlasBulkGroupIn] = Field(default_factory=list)


@atlas_router.put("/versions/{version_id}/graph", response={200: dict})
def bulk_replace_graph(request, version_id: int, payload: AtlasBulkGraphIn):
    """Transactional bulk replace (plan Task 5): delete-then-create only for
    the rows the body owns; rows matching by public key keep their identity.
    On any refusal nothing is written (one atomic block + the validator)."""
    from apps.atlas.validation import validate_version

    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    try:
        with transaction.atomic():
            type_keys = {item.nodeTypeKey for item in payload.nodes}
            type_rows = AtlasNodeType.objects.filter(key__in=type_keys)
            types_by_key = {row.key: row for row in type_rows}
            if set(types_by_key) != type_keys:
                raise AdminError(400, VALIDATION, "Unknown nodeTypeKey.",
                                 fields={"nodes": [
                                         f"no node type {key!r}."
                                        for key in type_keys - set(types_by_key)
                                    ]},
        )
            version.nodes.all().delete()
            nodes_by_key: dict[str, AtlasNode] = {}
            for item in payload.nodes:
                node_type = types_by_key[item.nodeTypeKey]
                node = AtlasNode(
                    version=version,
                    node_type=node_type,
                    public_key=item.publicKey or keys.new_node_key(node_type.key),
                    canonical_model=node_type.canonical_source,
                    canonical_translation_key=(
                        uuid.UUID(item.canonicalTranslationKey)
                        if item.canonicalTranslationKey else None
                    ),
                    importance=item.importance if item.importance is not None else 50,
                    visible=True if item.visible is None else item.visible,
                    mobile_overview_priority=item.mobileOverviewPriority or "auto",
                )
                try:
                    node.full_clean()
                    node.save()
                except DjangoValidationError as exc:
                    raise _validation_from_django(exc) from exc
                nodes_by_key[node.public_key] = node
                overrides = item.overrides or {}
                for locale, override in overrides.items():
                    if locale not in ("en", "fa"):
                        raise AdminError(400, VALIDATION, "Unknown override locale.",
                                         fields={"overrides": [f"unsupported locale {locale!r}."]})
                    row, _created = AtlasNodeTranslation.objects.get_or_create(
                        node=node, locale=locale
                    )
                    for attr, wire in (
                        ("label_override", "label"),
                        ("summary_override", "summary"),
                        ("accessible_label_override", "accessibleLabel"),
                        ("aliases", "aliases"),
                    ):
                        value = getattr(override, wire, None)
                        if value is not None:
                            setattr(row, attr, value)
                    try:
                        row.full_clean()
                        row.save()
                    except DjangoValidationError as exc:
                        raise _validation_from_django(exc) from exc
            version.relations.all().delete()
            for item in payload.relations:
                source = nodes_by_key.get(item.sourceKey)
                target = nodes_by_key.get(item.targetKey)
                if source is None or target is None:
                    offending = item.sourceKey if source is None else item.targetKey
                    raise AdminError(
                        400,
                        VALIDATION,
                        "Dangling bulk relation.",
                        issues=[
                            {
                                "code": "DANGLING_RELATION_ENDPOINT",
                                "relationKey": keys.relation_public_key(
                                    item.sourceKey,
                                    item.relationTypeKey,
                                    item.targetKey,
                                    directed=True,
                                ),
                                "nodeKey": offending,
                                "messageToken": "atlas.danglingRelationEndpoint",
                            }
                        ],
                    )
                relation_type = AtlasRelationType.objects.filter(key=item.relationTypeKey).first()
                if relation_type is None:
                    raise AdminError(400, VALIDATION, "Unknown bulk relationTypeKey.",
                                     fields={"relations": [f"no type {item.relationTypeKey!r}."]})
                relation = AtlasRelation(
                    version=version, source=source, target=target,
                    relation_type=relation_type,
                    directed=(item.directed if item.directed is not None
                              else relation_type.directed_default),
                )
                try:
                    relation.full_clean()
                    relation.save()
                except DjangoValidationError as exc:
                    raise _validation_from_django(exc) from exc
            version.groups.all().delete()
            for item in payload.groups:
                from apps.atlas.models import AtlasGroup, AtlasGroupTranslation

                group = AtlasGroup(version=version, public_key=item.key or keys.new_group_key())
                try:
                    group.full_clean()
                    group.save()
                except DjangoValidationError as exc:
                    raise _validation_from_django(exc) from exc
                for locale, label in item.labels.items():
                    if locale not in ("en", "fa"):
                        raise AdminError(400, VALIDATION, "Unknown bulk group label locale.",
                                         fields={"groups": [f"unsupported locale {locale!r}."]})
                    AtlasGroupTranslation.objects.create(group=group, locale=locale, label=label)
                for sort_order, node_key in enumerate(item.nodeKeys):
                    node = nodes_by_key.get(node_key)
                    if node is None:
                        raise AdminError(400, VALIDATION, "Unknown bulk group member.",
                                         fields={"groups": [f"no node {node_key!r}."]})
                    AtlasGroupMembership.objects.create(
                        group=group, node=node, sort_order=sort_order
                    )
            report = validate_version(version)
            # MISSING_LAYOUT is not a bulk-write blocker: the layout is
            # (re)computed AFTER the graph lands (POST …/layout, Task 5's own
            # route) — a freshly replaced graph is layoutless by construction.
            graph_blockers = [
                issue for issue in report.blocking if issue.code != "MISSING_LAYOUT"
            ]
            if graph_blockers:
                # Refuse INSIDE the atomic block: the old graph rolls back and
                # nothing from the invalid body survives (the plan's pin).
                raise AdminError(
                    409,
                    VALIDATION_BLOCKED,
                    "The bulk body would leave the graph invalid.",
                    issues=[
                        _atlas_issue_dict(issue) for issue in graph_blockers
                    ],
                )
    except AdminError:
        raise
    version.refresh_from_db()
    return {"nodeCount": version.nodes.count(), "relationCount": version.relations.count()}


# ---------------------------------------------------------------------------
# Task-6 route: mint (never verify, never serve draft content) — Plan A owns
# the capability validation at the public read boundary; Plan C renders.
# ---------------------------------------------------------------------------


class AtlasPreviewMintIn(Schema):
    locale: str = Field(min_length=2, max_length=2)


@atlas_router.post("/versions/{version_id}/preview-token", response={200: dict})
def mint_preview_token(request, version_id: int, payload: AtlasPreviewMintIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_draft(version)  # an ACTIVE row refuses with IMMUTABLE_ACTIVE
    if payload.locale not in ("en", "fa"):
        raise AdminError(400, VALIDATION, "Unknown locale.",
                         fields={"locale": ["must be 'en' or 'fa'."]})
    from apps.atlas.admin_preview import mint_preview_capability

    body = mint_preview_capability(version.pk, payload.locale)
    _atlas_audit(
        request,
        action="atlas.version.preview-mint",
        version=version,
        status=200,
        detail=f"POST /versions/{version.pk}/preview-token {payload.locale} -> 200",
    )
    return body
