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

from datetime import datetime

from ninja import Router, Schema

from apps.api.admin_common import (
    IMMUTABLE_ACTIVE,
    PRECONDITION_REQUIRED,
    STALE_REVISION,
    AdminError,
    _audit_log,
    _check_csrf,
    _require_admin_otp,
)
from apps.atlas.models import AtlasVersion
from apps.atlas.services import version_revision

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


class AtlasLayoutIn(Schema):
    """The stored layout dict shape: node public key -> [x, y, z]."""

    layout: dict[str, list[float]]


class AtlasNodeUpdateIn(Schema):
    """PATCH /versions/<id>/nodes/<key> body — Task 1 accepts importance only."""

    importance: int


class AtlasNodeOut(Schema):
    """The patch response for a single node — minimal Task-1 surface."""

    key: str
    importance: int


def _version_counts(version: AtlasVersion) -> tuple[int, int]:
    return (version.nodes.count(), version.relations.count())


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


@atlas_router.post("/versions/{version_id}/layout", response={200: dict})
def replace_layout(request, version_id: int, payload: AtlasLayoutIn):
    _require_admin_otp(request)
    _check_csrf(request)
    version = AtlasVersion.objects.filter(pk=version_id).first()
    if version is None:
        raise AdminError(404, "NOT_FOUND", "Atlas version not found.")
    _require_current_revision(request, version)
    _require_draft(version)
    version.layout = dict(payload.layout)
    version.save(update_fields=["layout"])
    _atlas_audit(
        request,
        action="atlas.layout.replace",
        version=version,
        status=200,
        detail=f"POST /versions/{version.pk}/layout -> 200",
    )
    return {"layout": version.layout, "layoutRevision": version.layout_revision}


@atlas_router.patch("/versions/{version_id}/nodes/{node_key}", response={200: AtlasNodeOut})
def patch_node(request, version_id: int, node_key: str, payload: AtlasNodeUpdateIn):
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
    node.importance = payload.importance
    node.save(update_fields=["importance"])
    _atlas_audit(
        request,
        action="atlas.node.update",
        version=version,
        status=200,
        detail=f"PATCH /versions/{version.pk}/nodes/{node_key} -> 200",
    )
    return AtlasNodeOut(key=node.public_key, importance=node.importance)
