"""Clone and publish services (spec §8.1–§8.3, §12.1) — plan Task 13.

Plan A's domain layer is pure where it can be (``validation.py``, ``layout.py``,
``canonical.py``); this module is the small, deliberate exception. It is the
lifecycle's only writer, and each of its three services does all of its work
inside one ``transaction.atomic`` block, so a publication is either complete or
absent — spec §8.3's "There is no partially published state".

Four decisions are recorded here because they are rulings, not style:

* **Archive, then activate.** ``atlas_version_unique_active`` is a partial unique
  index on ``status = 'active'``, so at most one row may be active at any
  instant. Activating the draft before archiving its predecessor would need two
  active rows at once and dies with ``IntegrityError`` (reproduced against this
  very constraint by the Task 6 review); the reverse order — spec §8.3 step 4 —
  is the only sequence that can succeed, and the module keeps the two writes as
  two adjacent statements so the order stays visible.
* **Validate a row re-read inside the transaction** (ledger row ``13-note``).
  ``validate_layout`` and ``validate_pins`` read the *in-memory* instance, so a
  service that validated an instance the caller held — or one it had already
  written over with a bare ``.update()`` — would validate a layout the database
  no longer has. ``activate_version`` accepts a row or its primary key, uses
  **only the identity**, re-reads the row under ``select_for_update`` inside the
  transaction, and validates *that*.
* **A clone carries its source's keys** (ruling R9). Spec §8.1's ``Active vN
  ──clone──▶ Draft vN+1`` needs both versions to hold the same ``public_key``s at
  once, which is why node and group keys are unique **per version** and not
  globally (migration ``0004_version_scoped_public_keys``); re-minting one would
  silently break every deep link and the stored layout dict, which the card
  forbids ("stable public keys must not silently mutate").
* **A clone copies the stored layout** (spec §12.1: computed once per revision
  and stored). The layout is a pure function of the rows being copied — topology,
  groups and pins — so the copy *is* the layout of that revision; recomputing
  would spend work to reach the same coordinates (Task 11 proved the engine
  deterministic across pks and row orders) while bumping ``layout_revision`` for
  a graph that did not change. Copying keeps spec §8.1's flow (clone → edit →
  validate → publish) executable without a manual layout action for an unedited
  clone, while a clone whose topology *changed* is still stopped by
  ``MISSING_LAYOUT`` — the gate, not this service, decides.

``recompute_layout`` is the layout action of spec §8.2 (``POST
…/versions/{id}/layout``), and it is the one in-place mutation a service offers:
it refuses a non-draft version, because an active version is served as-is
("Editing an active version is refused", spec §8.1) and an archived version is
history.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from django.db import models, transaction
from django.utils import timezone

from apps.atlas.layout import apply_layout
from apps.atlas.models import (
    VERSION_STATUSES,
    AtlasGroup,
    AtlasGroupMembership,
    AtlasGroupTranslation,
    AtlasNode,
    AtlasNodeTranslation,
    AtlasRelation,
    AtlasRelationTranslation,
    AtlasVersion,
)
from apps.atlas.validation import Issue, ValidationReport, validate_version

__all__ = [
    "AlreadyActive",
    "ImmutableActive",
    "PreconditionFailed",
    "ValidationFailed",
    "activate_version",
    "clone_version",
    "recompute_layout",
    "version_revision",
]


class ValidationFailed(Exception):
    """The publish gate refused: ``blocking`` was not empty (spec §8.3 step 1).

    ``issues`` is the blocking list itself — the caller can read every ``code``
    that stopped the publication, and ``codes`` answers "which blockers?" in one
    call. ``report`` carries the whole report (blocking **and** warnings) when the
    raiser had one, which is how the service raises it.
    """

    def __init__(self, issues: Iterable[Issue], *, report: ValidationReport | None = None) -> None:
        self.issues: tuple[Issue, ...] = tuple(issues)
        self.report = report
        self.codes: tuple[str, ...] = tuple(sorted({issue.code for issue in self.issues}))
        super().__init__(", ".join(self.codes) or "validation blocked")


class PreconditionFailed(Exception):
    """An ``If-Match``/lifecycle precondition failed (Plan B: ``409``).

    Raised for a stale ``expected_revision`` (spec §8.2's ``STALE_REVISION``) and
    for a target that is neither a draft nor currently active — an archived
    version is history and is not resurrected as the served topology.
    """


class AlreadyActive(Exception):
    """The target is already the active version (spec §8.2's ``ALREADY_ACTIVE``)."""


class ImmutableActive(Exception):
    """An in-place edit of a non-draft version (spec §8.1's ``IMMUTABLE_ACTIVE``).

    An active version is served as-is, so changing it in place would change what
    the site answers without going through publication; an archived version is
    kept for its history. Cloning is *not* an edit: spec §8.1's flow starts by
    cloning the active version.
    """


def version_revision(version: AtlasVersion) -> str:
    """The ``If-Match`` value of one version row (plan Task 13; Plan B reuses it).

    ``<pk>-<updated_at ISO>``: the identity of the row plus the stamp every write
    refreshes, so any mutation of the row makes a previously read value stale.
    """
    return f"{version.pk}-{version.updated_at.isoformat()}"


# ---------------------------------------------------------------------------
# Row resolution: identity in, locked rows out (ledger 13-note).
# ---------------------------------------------------------------------------


def _row_id(version: AtlasVersion | int) -> int:
    """The identity of the row to act on — never the argument's field values.

    Ledger row ``13-note``: an ``AtlasVersion`` instance handed in by a caller may
    be a stale view of the database (a bare ``.update()`` writes the row without
    touching any Python object), so no service here reads one. A pk and a row are
    both accepted because Plan A's own call sites pass pks and a Django caller
    naturally has the instance in hand.
    """
    if isinstance(version, AtlasVersion):
        if version.pk is None:
            raise PreconditionFailed("An unsaved AtlasVersion has no row to act on.")
        return version.pk
    return int(version)


def _locked_rows(*, version_id: int) -> dict[int, AtlasVersion]:
    """``{pk: row}`` for the target row and the currently active row, locked.

    One statement, ordered by primary key: ``select_for_update`` asks the
    database for the row locks inside the caller's transaction, and a single
    consistently ordered statement also removes the lock-ordering hazard two
    concurrent publications would otherwise have on a backend that really locks.
    SQLite ignores the clause (``connection.features.has_select_for_update`` is
    ``False``), so on the development database this is a plain read; the call is
    still made — and pinned by a test — so the production semantics are not a
    per-backend accident.
    """
    return {
        row.pk: row
        for row in (
            AtlasVersion.objects.select_for_update()
            .filter(models.Q(pk=version_id) | models.Q(status=VERSION_STATUSES.ACTIVE))
            .order_by("pk")
        )
    }


def _target(rows: dict[int, AtlasVersion], version_id: int) -> AtlasVersion:
    """The locked target row, or the ORM's own ``DoesNotExist`` (Plan B: ``404``)."""
    try:
        return rows[version_id]
    except KeyError:
        raise AtlasVersion.DoesNotExist(
            f"AtlasVersion matching pk={version_id} does not exist."
        ) from None


def _require_draft(version: AtlasVersion) -> None:
    """Only a draft may be written in place (spec §8.1's lifecycle)."""
    if version.status != VERSION_STATUSES.DRAFT:
        raise ImmutableActive(
            f"Version {version.pk} is '{version.status}'; only a draft can be edited in place."
        )


# ---------------------------------------------------------------------------
# The services.
# ---------------------------------------------------------------------------


def clone_version(source_id: AtlasVersion | int, label: str) -> AtlasVersion:
    """Copy a version's whole graph into a new draft (spec §8.1/§8.2).

    ``source_id`` may be the row or its primary key; only its **identity** is
    used — the source's stored state is re-read inside the transaction, so a
    caller's stale instance can never seed the copy (ledger ``13-note``).

    Everything the source holds is copied: nodes and their per-locale overrides,
    relations and their explanations, groups and their copy, memberships, the
    stored ``layout`` with its ``layout_revision``, and the pins that travel with
    the node rows. ``public_key``s are **copied**, never re-minted (ruling R9), and
    invisible rows and retired groups are copied with their flags — they are part
    of the topology, and an unpublished edit must not vanish on the way to the
    next version.

    ``created_from`` records the source row (spec §5's "clone provenance"), the
    label is taken verbatim — the caller owns the naming — and the new row is a
    draft with no ``published_at``: only :func:`activate_version` publishes.

    Collections are copied with one write per collection, so the cost is bounded
    and independent of the node count; the writes are ordered by the keys that
    end up in the copy (nodes by ``public_key``, relations by their source key,
    and so on), so the statement sequence does not follow a caller's insertion
    order (ruling R11).
    """
    source_id = _row_id(source_id)
    source = _target(_locked_rows(version_id=source_id), source_id)

    clone = AtlasVersion.objects.create(
        status=VERSION_STATUSES.DRAFT,
        label=label,
        created_from=source,
        layout_revision=source.layout_revision,
        # JSON data: a round trip, so the two rows never share one dict object.
        layout=json.loads(json.dumps(source.layout)),
    )

    nodes = list(source.nodes.select_related("node_type").order_by("public_key"))
    AtlasNode.objects.bulk_create(
        AtlasNode(
            version=clone,
            public_key=node.public_key,
            node_type=node.node_type,
            canonical_model=node.canonical_model,
            canonical_translation_key=node.canonical_translation_key,
            importance=node.importance,
            visible=node.visible,
            mobile_overview_priority=node.mobile_overview_priority,
            pin_x=node.pin_x,
            pin_y=node.pin_y,
            pin_z=node.pin_z,
            sort_order=node.sort_order,
        )
        for node in nodes
    )
    new_nodes = {node.public_key: node for node in clone.nodes.all()}

    AtlasNodeTranslation.objects.bulk_create(
        AtlasNodeTranslation(
            node=new_nodes[translation.node.public_key],
            locale=translation.locale,
            label_override=translation.label_override,
            summary_override=translation.summary_override,
            accessible_label_override=translation.accessible_label_override,
            aliases=list(translation.aliases),
        )
        for translation in AtlasNodeTranslation.objects.filter(node__version=source)
        .select_related("node")
        .order_by("node__public_key", "locale")
    )

    source_relations = list(
        source.relations.select_related("source", "target", "relation_type").order_by(
            "source__public_key", "relation_type__key", "target__public_key"
        )
    )
    AtlasRelation.objects.bulk_create(
        AtlasRelation(
            version=clone,
            source=new_nodes[relation.source.public_key],
            target=new_nodes[relation.target.public_key],
            relation_type=relation.relation_type,
            directed=relation.directed,
            weight=relation.weight,
            visible=relation.visible,
            sort_order=relation.sort_order,
        )
        for relation in source_relations
    )
    new_relations = {
        _relation_identity(relation): relation
        for relation in clone.relations.select_related("source", "target")
    }
    # Read every explanation in one query and group it in Python: asking each
    # relation for its own translations would cost one round trip per relation
    # (measured at 136 extra statements on the 72-node scale graph before this).
    translations_by_relation: dict[int, list[AtlasRelationTranslation]] = {}
    for translation in AtlasRelationTranslation.objects.filter(
        relation__version=source
    ).order_by("relation_id", "locale"):
        translations_by_relation.setdefault(translation.relation_id, []).append(translation)
    AtlasRelationTranslation.objects.bulk_create(
        AtlasRelationTranslation(
            relation=new_relations[_relation_identity(relation)],
            locale=translation.locale,
            explanation=translation.explanation,
        )
        for relation in source_relations
        for translation in translations_by_relation.get(relation.pk, ())
    )

    AtlasGroup.objects.bulk_create(
        AtlasGroup(
            version=clone,
            public_key=group.public_key,
            sort_order=group.sort_order,
            active=group.active,
        )
        for group in source.groups.order_by("public_key")
    )
    new_groups = {group.public_key: group for group in clone.groups.all()}

    AtlasGroupTranslation.objects.bulk_create(
        AtlasGroupTranslation(
            group=new_groups[translation.group.public_key],
            locale=translation.locale,
            label=translation.label,
            description=translation.description,
        )
        for translation in AtlasGroupTranslation.objects.filter(group__version=source)
        .select_related("group")
        .order_by("group__public_key", "locale")
    )

    AtlasGroupMembership.objects.bulk_create(
        AtlasGroupMembership(
            group=new_groups[membership.group.public_key],
            node=new_nodes[membership.node.public_key],
            sort_order=membership.sort_order,
        )
        for membership in AtlasGroupMembership.objects.filter(group__version=source)
        .select_related("group", "node")
        .order_by("group__public_key", "node__public_key")
    )

    return clone


def _relation_identity(relation: AtlasRelation) -> tuple[str, int, str]:
    """``(source key, relation type pk, target key)`` — a relation's identity in a version.

    The stored ``(version, source, target, relation_type)`` unique constraint makes
    this tuple unique inside one version, which is all the copy needs to pair a
    source row with its clone.
    """
    return (relation.source.public_key, relation.relation_type_id, relation.target.public_key)


@transaction.atomic
def activate_version(version: AtlasVersion | int, *, expected_revision: str) -> AtlasVersion:
    """Publish a draft — spec §8.3's five steps in one transaction, or nothing.

    Order of operations, each one load-bearing:

    1. the row is re-read **inside** the transaction under ``select_for_update``,
       and only then is anything judged or written (ledger ``13-note``);
    2. ``AlreadyActive`` and the ``expected_revision`` precondition are answered
       from that fresh row, in that order, so a caller repeating a successful
       activation gets the lifecycle answer rather than a stale-revision one;
    3. the aggregate :func:`~apps.atlas.validation.validate_version` report is
       re-run against the fresh row. It is spec §8.3's steps 1–3 together: the
       report's locale gate re-resolves against ``objects.public()`` and its
       ``validate_layout`` half is the checklist's "coordinates exist for every
       visible node". Any blocking issue raises :class:`ValidationFailed` with the
       issues — nothing is written;
    4. the currently active row is archived, **then** the draft is activated and
       stamped with ``published_at`` (spec §8.3 step 4).

    The returned instance is refreshed from the row the transaction produced, and
    no other version's rows are written: a sibling draft keeps its status, layout,
    revision, stamps and graph.
    """
    version_id = _row_id(version)
    rows = _locked_rows(version_id=version_id)
    target = _target(rows, version_id)

    if target.status == VERSION_STATUSES.ACTIVE:
        raise AlreadyActive(f"Version {version_id} is already active.")
    if target.status != VERSION_STATUSES.DRAFT:
        raise PreconditionFailed(
            f"Only a draft can be activated; version {version_id} is '{target.status}'."
        )

    stored_revision = version_revision(target)
    if expected_revision != stored_revision:
        raise PreconditionFailed(
            f"Version {version_id} is at revision '{stored_revision}', "
            f"the caller expected '{expected_revision}'."
        )

    report = validate_version(target)
    if report.blocking:
        raise ValidationFailed(report.blocking, report=report)

    for row in rows.values():
        if row.pk != version_id and row.status == VERSION_STATUSES.ACTIVE:
            row.status = VERSION_STATUSES.ARCHIVED
            row.save(update_fields=["status", "updated_at"])

    target.status = VERSION_STATUSES.ACTIVE
    target.published_at = timezone.now()
    target.save(update_fields=["status", "published_at", "updated_at"])

    target.refresh_from_db()
    return target


@transaction.atomic
def recompute_layout(version: AtlasVersion | int) -> int:
    """Recompute and store a draft's layout; returns the new ``layout_revision``.

    Spec §8.2's ``POST …/versions/{id}/layout``: pins are preserved (the engine
    reads them), every coordinate is re-derived from the stored topology, and the
    revision moves by one. The row is re-read under ``select_for_update`` before
    the counter is touched, so the revision it counts up from is the stored one —
    a caller's stale instance cannot make it go backwards (ledger ``13-note``).

    Only a draft may be recomputed: an active version is served as-is (spec §8.1)
    and an archived one is history, so both raise :class:`ImmutableActive`.
    """
    version_id = _row_id(version)
    target = _target(_locked_rows(version_id=version_id), version_id)
    _require_draft(target)
    apply_layout(target)
    return target.layout_revision
