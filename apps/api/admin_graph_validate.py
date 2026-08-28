"""Pure graph payload validator (Track AB-06).

Validates one graph version payload (``{"nodes": [...], "edges": [...]}`` in
the AGENT-COORDINATION.md section 4 ``GraphNodePublic``/``GraphEdgePublic``
camelCase shapes) and returns a list of structured issues. Groups are ignored
in v1 (authoring-side structure only; no public contract yet).

This module is PURE: no HTTP, no Django imports at module top. The
related-record existence check is injected as ``related_resolver(family, id)
-> bool`` by the HTTP layer (``apps/api/admin_graph.py`` wires the
published-existence resolver from ``apps/api/admin_common.py``, whose
``GRAPH_RELATED_FAMILIES`` table owns the family wire strings). Issues are
``{"code": str, "nodeId"?: str, "edgeId"?: str, "messageToken": str}``.

Stable issue codes (``GRAPH_ISSUE_CODES``, exact strings - do not rename):

- ``DUPLICATE_NODE_ID``          node ``id`` appears more than once
- ``SELF_EDGE``                  edge with ``source == target``
- ``DUPLICATE_EDGE``             same ``source``+``target``+``relationType``
                                 twice; also a swapped pair when BOTH edges
                                 are ``directed=False`` (mirrors GraphEdge.clean)
- ``BROKEN_RELATED``             ``relatedResolver(family, id)`` returned
                                 False (or the entry is malformed)
- ``MISSING_ACCESSIBLE_LABEL``   node ``accessibleLabel`` missing/blank
- ``BAD_WEIGHT``                 weight not a number in 0..1 (see below)
- ``MISSING_POSITION``           node ``position.x``/``y`` absent or
                                 non-numeric (``z`` optional but numeric)

Weight note: the public contract says weight is a 0..1 number, but the BK-04
storage columns (``GraphNode.weight``/``GraphEdge.weight``) are
``PositiveSmallIntegerField``; a fractional weight would be truncated by the
ORM and break PUT->GET roundtrip equality. The validator therefore also
rejects non-integral values in 0..1 (only 0 and 1 pass) until a storage
migration widens the column - flagged to the integrator via report.

``messageToken`` is the stable localization key AF renders per code (one
token per code, ``graph.*`` namespace). ``edgeId`` on issues uses the edge's
supplied ``id`` when present, else the composed public id from
:func:`edge_public_id`.
"""

from __future__ import annotations

GRAPH_ISSUE_CODES: list[str] = [
    "DUPLICATE_NODE_ID",
    "SELF_EDGE",
    "DUPLICATE_EDGE",
    "BROKEN_RELATED",
    "MISSING_ACCESSIBLE_LABEL",
    "BAD_WEIGHT",
    "MISSING_POSITION",
]

_MESSAGE_TOKENS: dict[str, str] = {
    "DUPLICATE_NODE_ID": "graph.duplicateNodeId",
    "SELF_EDGE": "graph.selfEdge",
    "DUPLICATE_EDGE": "graph.duplicateEdge",
    "BROKEN_RELATED": "graph.brokenRelated",
    "MISSING_ACCESSIBLE_LABEL": "graph.missingAccessibleLabel",
    "BAD_WEIGHT": "graph.badWeight",
    "MISSING_POSITION": "graph.missingPosition",
}


# SYNC-GUARD: BK-05 (public graph read) has not shipped yet, so the stable
# public edge id composition is duplicated here. When BK-05 exports its
# composer, import that instead and delete this copy (keep strings identical).
# Related-record note (AB-07): the family -> model table lives in
# apps/api/admin_common.py (GRAPH_RELATED_FAMILIES); this module stays pure
# and receives family existence via the injected ``related_resolver``.
def edge_public_id(source: str, target: str, relation_type: str) -> str:
    """Stable public edge id (BK-04/05 contract: composed, never stored)."""
    return f"{source}->{target}:{relation_type}"


def _issue(code: str, *, node_id: str | None = None, edge_id: str | None = None) -> dict:
    issue: dict = {"code": code, "messageToken": _MESSAGE_TOKENS[code]}
    if node_id is not None:
        issue["nodeId"] = node_id
    if edge_id is not None:
        issue["edgeId"] = edge_id
    return issue


def _is_number(value: object) -> bool:
    """JSON numbers only - bool is an int subclass in Python and is rejected."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bad_weight(value: object) -> bool:
    """0..1 numeric, integral only (see module docstring weight note)."""
    if not _is_number(value):
        return True
    if not 0 <= value <= 1:  # type: ignore[operator]
        return True
    return float(value) != int(value)


def validate_graph_payload(payload: dict, *, related_resolver) -> list[dict]:
    """Validate one graph payload; return every issue (empty list = clean).

    ``related_resolver(family: str, id: str) -> bool`` must report whether the
    related record exists in a publicly readable state for its family.
    """
    issues: list[dict] = []
    if not isinstance(payload, dict):
        return issues
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []

    seen_node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw_id = node.get("id")
        node_id = "" if raw_id is None else str(raw_id)
        if node_id in seen_node_ids:
            issues.append(_issue("DUPLICATE_NODE_ID", node_id=node_id))
        seen_node_ids.add(node_id)

        accessible = node.get("accessibleLabel")
        if not isinstance(accessible, str) or not accessible.strip():
            issues.append(_issue("MISSING_ACCESSIBLE_LABEL", node_id=node_id))

        if _bad_weight(node.get("weight")):
            issues.append(_issue("BAD_WEIGHT", node_id=node_id))

        position = node.get("position")
        pos = position if isinstance(position, dict) else None
        if pos is None or not _is_number(pos.get("x")) or not _is_number(pos.get("y")):
            issues.append(_issue("MISSING_POSITION", node_id=node_id))
        elif "z" in pos and pos["z"] is not None and not _is_number(pos["z"]):
            issues.append(_issue("MISSING_POSITION", node_id=node_id))

        related = node.get("relatedRecords")
        if not isinstance(related, list):
            related = []
        for entry in related:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("family"), str)
                or not isinstance(entry.get("id"), str)
                or not related_resolver(entry["family"], entry["id"])
            ):
                issues.append(_issue("BROKEN_RELATED", node_id=node_id))

    seen_edges: list[dict] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = "" if edge.get("source") is None else str(edge.get("source"))
        target = "" if edge.get("target") is None else str(edge.get("target"))
        relation = (
            "" if edge.get("relationType") is None else str(edge.get("relationType"))
        )
        directed = edge.get("directed", True)
        raw_id = edge.get("id")
        edge_id = (
            raw_id
            if isinstance(raw_id, str) and raw_id
            else edge_public_id(source, target, relation)
        )
        if source == target:
            issues.append(_issue("SELF_EDGE", edge_id=edge_id))
        if _bad_weight(edge.get("weight")):
            issues.append(_issue("BAD_WEIGHT", edge_id=edge_id))
        for prior in seen_edges:
            exact = (
                prior["source"] == source
                and prior["target"] == target
                and prior["relation"] == relation
            )
            swapped_undirected = (
                prior["directed"] is False
                and directed is False
                and prior["relation"] == relation
                and prior["source"] == target
                and prior["target"] == source
            )
            if exact or swapped_undirected:
                issues.append(_issue("DUPLICATE_EDGE", edge_id=edge_id))
                break
        seen_edges.append(
            {"source": source, "target": target, "relation": relation, "directed": directed}
        )
    return issues
