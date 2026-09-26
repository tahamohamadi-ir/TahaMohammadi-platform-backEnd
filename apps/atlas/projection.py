"""Locale projection — spec §10.2's wire payload, materialised for one locale.

The projection is the only place the stored topology becomes the served payload:
:func:`build_locale_projection` reads the version's rows, resolves every piece of
copy **in exactly the requested locale** and returns the §10.2 shape.
:func:`canonical_json` and :func:`projection_etag` are the ETag pair (§10.5).

What the rules are, and where each comes from:

* **Exact locale, no fallback (§7.8).** A node's label/summary comes from that
  locale's non-blank override, or from that locale's own canonical row via
  :func:`apps.atlas.canonical.resolve_canonical` (§5.4's rule, "the single
  resolution rule used by validation, the public API and the admin preview"). A
  node that resolves in the *other* locale only is served **without** a label —
  never with the other locale's copy. That state is exactly what the parity gate
  reports as ``MISSING_LOCALE_PROJECTION``; the projection's job is to be honest
  about it, not to paper over it.
* **Override precedence (§5.4.3).** A non-blank per-locale override wins over the
  canonical row; a blank or whitespace-only override is not an override.
* **The payload serves what is *served*, not what is repaired.** Only ``visible``
  nodes and relations are projected (§10.3 "Visibility"), and catalogs are
  limited to the types that are **active and actually used** by those rows. No
  value is ever invented: a resolution that did not happen yields no ``canonical``
  block, an unresolvable locale yields no label, and a *published record without
  a public route* (``method``/``technology`` in v1 — spec §5.2; §25) yields a
  ``canonical`` block with no ``routeFamily``/``href`` rather than a fabricated
  path. ``href`` is derived from the existing route-family map
  (:data:`apps.api.record_resolver.ROUTE_FAMILY_MAP`), the same table the record
  resolver serves, and is the exact-locale path ``/{locale}/{route}/{slug}/``.
* **Coordinates are the stored ones (§12.1).** ``position`` comes from
  ``version.layout`` — the dict keyed by node ``public_key`` that
  :func:`apps.atlas.layout.apply_layout` writes — rounded to the three decimals
  §10.3 documents. This module never calls the layout engine: §12.1 fixes "one
  layout authority", and a projection that re-simulated coordinates would serve
  values that are not the published ones. The ``z`` axis travels as stored:
  §12.8/§14.3 fold depth into radius and draw order in the *presentation*, which
  is a pure function of this payload plus a viewport box — a projection that
  pre-folded it would destroy the input the 3D scene reads.
* **Fail closed, never silently partial.** A visible node whose stored layout has
  no usable coordinate is served without ``position``, which
  :func:`apps.atlas.validation.validate_payload_contract` reports as
  ``MISSING_LAYOUT`` — a zero coordinate would hide the defect behind a valid
  payload. An ambiguous canonical reference (two published rows for one
  ``(model, translation_key, locale)``) propagates
  :class:`apps.atlas.canonical.AmbiguousCanonicalRef` instead of picking a row.
* **Determinism (ruling R11).** The result is a pure function of the stored rows
  and the locale: every collection is ordered in Python — nodes by
  ``(-importance, public_key)``, relations by ``(visual_priority DESC,
  composed key)``, groups and catalogs by ``(sort_order, key)``, group members by
  ``public_key`` (all spec §10.3) — so no queryset, dict or set iteration order
  can reach the wire. ``canonical_json`` sorts its keys, so two projections of
  one row set are byte-identical.
* **The caller's instance is not the authority.** The row is re-read by primary
  key before anything is projected (the ``13-note`` discipline, applied to the
  read path): a stale in-memory version cannot serve a layout the database no
  longer holds.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from apps.atlas.canonical import (
    CANONICAL_SOURCES,
    DEFAULT_LOCALES,
    CanonicalResolution,
    resolve_canonical,
)
from apps.atlas.contract import ATLAS_CONTRACT_VERSION
from apps.atlas.models import (
    AtlasGroup,
    AtlasGroupMembership,
    AtlasNode,
    AtlasNodeTranslation,
    AtlasRelation,
    AtlasVersion,
)
from apps.atlas.services import version_revision

__all__ = ["build_locale_projection", "canonical_json", "projection_etag"]

#: The three coordinate axes, in wire order (spec §10.2's ``position`` object).
_POSITION_AXES: tuple[str, ...] = ("x", "y", "z")

#: The precision §10.3 documents for ``position``. The engine already rounds at
#: storage (§12.2 step 9), so this is idempotent on engine output and only
#: normalises a value written outside the pipeline.
_COORDINATE_DECIMALS = 3


def build_locale_projection(version: AtlasVersion | int, locale: str) -> dict:
    """The spec §10.2 payload for ``locale`` — the served topology, never a repair.

    ``version`` may be a row or its primary key; only the identity is read from
    the argument, because the projection must describe the *stored* row. The
    locale set is closed to :data:`apps.atlas.canonical.DEFAULT_LOCALES`
    (spec §10.1): an unknown locale raises ``ValueError`` rather than producing a
    payload with no resolution in it, which would be a cross-locale fallback by
    another name.
    """
    if locale not in DEFAULT_LOCALES:
        raise ValueError(
            f"unsupported Atlas locale {locale!r}: the topology is projected into "
            f"{' and '.join(DEFAULT_LOCALES)} only, with no fallback locale (spec §10.1/§7.8)."
        )
    row = _fresh(version)

    layout = row.layout if isinstance(row.layout, Mapping) else {}
    nodes = sorted(
        row.nodes.filter(visible=True).select_related("node_type").prefetch_related("translations"),
        key=_node_order,
    )
    relations = sorted(
        row.relations.filter(visible=True)
        .select_related("relation_type", "source", "target")
        .prefetch_related("translations"),
        key=_relation_order,
    )
    groups = sorted(
        row.groups.filter(active=True).prefetch_related("translations"), key=_group_order
    )
    members = _group_members(row)

    node_entries = [_node_entry(node, locale, layout) for node in nodes]
    relation_entries = [_relation_entry(relation, locale) for relation in relations]
    group_entries = [_group_entry(group, locale, members) for group in groups]

    return {
        "contractVersion": ATLAS_CONTRACT_VERSION,
        "locale": locale,
        "version": _version_block(row, len(node_entries), len(relation_entries)),
        "nodeTypes": _node_type_catalog(nodes, locale),
        "relationTypes": _relation_type_catalog(relations, locale),
        "groups": group_entries,
        "nodes": node_entries,
        "relations": relation_entries,
    }


def canonical_json(payload: Mapping) -> bytes:
    """The ETag input: ``payload`` as canonical JSON (spec §10.5).

    Sorted keys, compact separators, UTF-8 and no ASCII escaping — one function,
    so the endpoint and its tests hash the same bytes. This is the serialisation
    the digest is *defined* over, not a requirement on the response body.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def projection_etag(payload: Mapping) -> str:
    """``"<version.id>-<16 hex>"`` over the canonical JSON (spec §10.5).

    ``ETag: "<version.id>-<16 hex of a SHA-256 over the canonical JSON of this
    locale projection>"``: the identity of the row plus a digest of everything
    the client will receive, so any change in the projection — a rename, a new
    override, a republished canonical row, a moved coordinate — changes the tag.
    """
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()[:16]
    return f"{payload['version']['id']}-{digest}"


# ---------------------------------------------------------------------------
# The row itself
# ---------------------------------------------------------------------------


def _fresh(version: AtlasVersion | int) -> AtlasVersion:
    """The stored row behind ``version`` — the projection's real input.

    Ledger ``13-note``'s trap in its read-path form: an ``AtlasVersion`` handed
    in by a caller may be a stale view (a bare ``QuerySet.update()`` writes the
    database and touches no Python object), and the layout it carries is the one
    the payload would then serve. The argument supplies the identity only.
    """
    identity = version.pk if isinstance(version, AtlasVersion) else version
    if identity is None:
        raise ValueError("build_locale_projection needs a stored AtlasVersion (or its pk).")
    return AtlasVersion.objects.get(pk=identity)


def _node_order(node: AtlasNode) -> tuple[int, str]:
    """Spec §10.3: ``nodes`` ordered by ``(-importance, public_key)``."""
    return (-node.importance, node.public_key)


def _relation_order(relation: AtlasRelation) -> tuple[int, str]:
    """Spec §10.3: ``relations`` ordered by ``visual_priority DESC, key``."""
    return (-relation.relation_type.visual_priority, relation.public_key)


def _group_order(group: AtlasGroup) -> tuple[int, str]:
    """Spec §10.3: ``groups`` ordered by ``sort_order, key``."""
    return (group.sort_order, group.public_key)


def _version_block(version: AtlasVersion, node_count: int, relation_count: int) -> dict:
    """The §10.2 ``version`` block — stored stamps plus the served counts.

    ``publishedAt`` is omitted while the row has none (a draft read through the
    preview path), which is §10.3's omission rule and not a missing feature: an
    unpublished version has published nothing.
    """
    block: dict[str, Any] = {"id": version.pk, "revision": version_revision(version)}
    if version.published_at is not None:
        block["publishedAt"] = version.published_at.isoformat()
    block["nodeCount"] = node_count
    block["relationCount"] = relation_count
    block["layoutRevision"] = version.layout_revision
    return block


# ---------------------------------------------------------------------------
# Resolution and copy
# ---------------------------------------------------------------------------


def _translation(rows: Iterable, locale: str):
    """The per-locale row of a prefetched translation set, or ``None``.

    ``(node|relation|group, locale)`` is unique at the model level, so at most
    one row can match; the scan is over the prefetched list rather than a filter
    so a projection costs one query per collection instead of one per row.
    """
    for row in rows:
        if row.locale == locale:
            return row
    return None


def _text(value: object) -> str:
    """A stored copy field as it is served: stripped, or empty when it is not copy.

    §10.3's omission rule ("``null``/empty optional fields are omitted, never sent
    as empty strings") is only implementable if "is this empty?" is answered once,
    consistently, for every copy field.
    """
    return str(value or "").strip()


def _first(*candidates: str) -> str:
    """The first non-empty candidate — the precedence helper of §5.4/§5.4.3."""
    for candidate in candidates:
        if candidate:
            return candidate
    return ""


def _locale_text(row, field: str, locale: str) -> str:
    """A bilingual taxonomy column for one locale (``label_en``/``label_fa``)."""
    return _text(getattr(row, f"{field}_{locale}", ""))


def _resolution(node: AtlasNode, locale: str) -> CanonicalResolution | None:
    """The record that resolves for this node in **this** locale (spec §5.4).

    ``None`` means "nothing resolves here": no canonical reference at all (an
    Atlas-only structural node), or no published exact-locale row. The pair helper
    is deliberately not used — a per-locale answer is what makes "EN resolves, FA
    does not" visible instead of silently averaged away.
    """
    if node.canonical_model not in CANONICAL_SOURCES or node.canonical_translation_key is None:
        return None
    return resolve_canonical(node.canonical_model, node.canonical_translation_key, locale)


def _canonical_block(resolution: CanonicalResolution | None, locale: str) -> dict | None:
    """The §10.2 ``canonical`` object, or ``None`` when nothing resolved.

    ``routeFamily``/``href`` exist only where the record resolver has a public
    route for the family (spec §10.3: the path "derived from the existing
    route-family map"). ``method`` and ``technology`` have none in v1, so their
    blocks stop after the record's identity — a fabricated ``/{locale}/method/…``
    is precisely the invention this rule forbids. A blank slug or title is dropped
    like any other empty field, and with no slug there is no path to derive.
    """
    if resolution is None:
        return None
    slug = _text(resolution.slug)
    title = _text(resolution.title)
    block: dict[str, Any] = {"family": resolution.family, "id": resolution.id}
    if slug:
        block["slug"] = slug
    if title:
        block["title"] = title
    if slug and resolution.route_family is not None:
        block["routeFamily"] = resolution.route_family
        block["href"] = f"/{locale}/{resolution.route_family}/{slug}/"
    return block


def _position(layout: Mapping, key: str) -> dict[str, float] | None:
    """The stored coordinate of one node, or ``None`` when there is no usable one.

    The storage shape is the plan's ``{"<node public key>": [x, y, z]}`` (§12.2
    step 9 rounds at storage). Anything else — absent, wrong arity, non-numeric,
    non-finite — is reported as *missing* rather than repaired: the entry then
    carries no ``position`` and the payload gate raises ``MISSING_LAYOUT``
    (spec §20.1's own row), which is the fail-closed answer. A zero coordinate is
    never substituted, and no axis is dropped silently.
    """
    coordinate = layout.get(key)
    if isinstance(coordinate, (str, bytes)) or not isinstance(coordinate, Sequence):
        return None
    if len(coordinate) != len(_POSITION_AXES):
        return None
    values: list[float] = []
    for value in coordinate:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        values.append(round(number, _COORDINATE_DECIMALS))
    return dict(zip(_POSITION_AXES, values, strict=True))


def _aliases(translation: AtlasNodeTranslation | None) -> list[str]:
    """The locale's search aliases, blank entries dropped (§5.3's own rule)."""
    if translation is None or not isinstance(translation.aliases, list):
        return []
    return [text for alias in translation.aliases if (text := _text(alias))]


def _node_entry(node: AtlasNode, locale: str, layout: Mapping) -> dict:
    """One §10.2 node entry: identity, copy, resolution, stored position."""
    translation = _translation(node.translations.all(), locale)
    resolution = _resolution(node, locale)
    title = _text(resolution.title) if resolution is not None else ""
    label = _first(
        _text(getattr(translation, "label_override", "")), title
    )
    entry: dict[str, Any] = {"key": node.public_key, "type": node.node_type.key}
    if label:
        entry["label"] = label
    summary = _first(
        _text(getattr(translation, "summary_override", "")),
        _text(resolution.summary) if resolution is not None else "",
    )
    if summary:
        entry["summary"] = summary
    if label:
        # spec §5.3: a blank accessible-label override means "the resolved label".
        entry["accessibleLabel"] = _first(
            _text(getattr(translation, "accessible_label_override", "")), label
        )
    entry["importance"] = node.importance
    entry["mobileOverviewPriority"] = node.mobile_overview_priority
    aliases = _aliases(translation)
    if aliases:
        entry["aliases"] = aliases
    canonical = _canonical_block(resolution, locale)
    if canonical is not None:
        entry["canonical"] = canonical
    position = _position(layout, node.public_key)
    if position is not None:
        entry["position"] = position
    return entry


def _relation_entry(relation: AtlasRelation, locale: str) -> dict:
    """One §10.2 relation entry — composed key, per-type direction and hierarchy.

    ``key`` is the row's composed public key: the model's own ``public_key``
    property is :func:`apps.atlas.keys.relation_public_key` over the two endpoint
    keys and the type key, so the wire never re-derives it (plan Step 3).
    ``directed``/``weight`` are the authored row's values, ``hierarchy`` is read
    from the relation **type** (§10.3: derived, never authored per relation), and
    ``inverseLabel`` is that type's copy in this locale.
    """
    relation_type = relation.relation_type
    entry: dict[str, Any] = {
        "key": relation.public_key,
        "type": relation_type.key,
        "source": relation.source.public_key,
        "target": relation.target.public_key,
        "directed": relation.directed,
        "weight": relation.weight,
        "hierarchy": relation_type.hierarchy_role,
    }
    inverse_label = _locale_text(relation_type, "inverse_label", locale)
    if inverse_label:
        entry["inverseLabel"] = inverse_label
    translation = _translation(relation.translations.all(), locale)
    explanation = _text(getattr(translation, "explanation", ""))
    if explanation:
        entry["explanation"] = explanation
    return entry


def _group_entry(group: AtlasGroup, locale: str, members: Mapping) -> dict:
    """One §10.2 group entry: localized copy plus the visible member keys."""
    translation = _translation(group.translations.all(), locale)
    label = _text(getattr(translation, "label", ""))
    description = _text(getattr(translation, "description", ""))
    entry: dict[str, Any] = {"key": group.public_key}
    if label:
        entry["label"] = label
    if description:
        entry["description"] = description
    entry["nodeKeys"] = list(members.get(group.public_key, ()))
    return entry


def _group_members(version: AtlasVersion) -> dict[str, tuple[str, ...]]:
    """``{group key: visible member node keys}``, sorted — one query, no row order.

    A membership pointing at a hidden node is dropped rather than served: the
    payload's ``nodeKeys`` must resolve inside its own ``nodes`` array (the payload
    gate enforces exactly that) and a hidden node is not served at all. Rows are
    read once and sorted in Python, because a database collation is not part of
    the ordering rule (spec §12.3).
    """
    members: dict[str, list[str]] = {}
    rows = (
        AtlasGroupMembership.objects.filter(group__version=version, group__active=True)
        .select_related("group", "node")
        .order_by()
    )
    for row in rows:
        if row.node.visible:
            members.setdefault(row.group.public_key, []).append(row.node.public_key)
    return {key: tuple(sorted(keys)) for key, keys in members.items()}


def _node_type_catalog(nodes: Iterable[AtlasNode], locale: str) -> list[dict]:
    """The node-type vocabulary the served nodes need (spec §10.2).

    Limited to the types **active and used by a visible node** (plan Step 3): an
    unused taxonomy row is payload weight nobody asked for, and a retired type
    must not be advertised. A visible node whose type is retired then references a
    type the catalog does not carry — the payload gate reports it and the endpoint
    fails closed, which is the honest outcome rather than a silently unfilterable
    node.
    """
    used = {node.node_type for node in nodes if node.node_type.active}
    catalog: list[dict] = []
    for node_type in sorted(used, key=lambda row: (row.sort_order, row.key)):
        entry: dict[str, Any] = {"key": node_type.key}
        label = _locale_text(node_type, "label", locale)
        if label:
            entry["label"] = label
        entry["semanticRole"] = node_type.semantic_role
        entry["visualRole"] = node_type.visual_role
        entry["allowAsRoot"] = node_type.allow_as_root
        entry["allowChildren"] = node_type.allow_children
        entry["filterVisible"] = node_type.filter_visible
        catalog.append(entry)
    return catalog


def _relation_type_catalog(relations: Iterable[AtlasRelation], locale: str) -> list[dict]:
    """The relation-type vocabulary the served relations need (spec §10.2).

    Same rule as the node-type catalog: active types, used by a visible relation,
    ordered by ``(sort_order, key)`` (§10.3). ``directed`` is the type's
    ``directed_default`` — the value a relation is initialised from — and
    ``hierarchyRole`` is the field that decides whether the relation is hierarchy
    at all (§5.6).
    """
    used = {relation.relation_type for relation in relations if relation.relation_type.active}
    catalog: list[dict] = []
    for relation_type in sorted(used, key=lambda row: (row.sort_order, row.key)):
        entry: dict[str, Any] = {"key": relation_type.key}
        label = _locale_text(relation_type, "label", locale)
        if label:
            entry["label"] = label
        inverse_label = _locale_text(relation_type, "inverse_label", locale)
        if inverse_label:
            entry["inverseLabel"] = inverse_label
        entry["directed"] = relation_type.directed_default
        entry["hierarchyRole"] = relation_type.hierarchy_role
        entry["visualPriority"] = relation_type.visual_priority
        catalog.append(entry)
    return catalog
