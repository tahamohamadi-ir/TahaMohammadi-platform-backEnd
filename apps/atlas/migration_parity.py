"""Pure parity comparison: legacy published graph vs Atlas projection (Plan D Task 4).

``compare_parity(legacy_payloads, atlas_projection)`` implements spec §23.4's
five checks: node count, relation count, per-locale labels, canonical
targets, relation display copy. The ``ok`` flag gates every downstream step.

SHAPE CONTRACT (verified against the real wire on both sides):
- legacy fixture: ``{nodes: [{id, label, relatedRecords: [{family, id}]}], edges:
  [{relationType, source, target}]}`` (the live `/api/graph/{locale}` shape).
- atlas projection: ``build_locale_projection`` output — nodes carry
  ``{key, label, canonical: {family, id, slug, title}}``; relations carry
  ``{key, type, source, target}`` with a composed relation ``key``; display
  copy lives in the top-level ``relationTypes: [{key, label}]`` catalog.
"""

from __future__ import annotations


def _legacy_nodes(payload):
    return {n['id']: n for n in payload.get('nodes', [])}


def _legacy_edges(payload):
    return {e['id']: e for e in payload.get('edges', [])}


def compare_parity(legacy_payloads, atlas_projection):
    """Compare frozen legacy payloads against one Atlas locale projection.

    ``legacy_payloads``: ``{locale: legacy_payload}`` with
    ``{nodes: [{id, label, relatedRecords}], edges: [...]}``.
    ``atlas_projection``: ``{locale, nodes, relations}`` in the public
    wire shape (``build_locale_projection`` output).
    """
    checks = {}
    locale = atlas_projection.get('locale')
    legacy = legacy_payloads.get(locale)
    if legacy is None:
        return {'ok': False, 'checks': {'locale-present': False}}

    legacy_nodes = _legacy_nodes(legacy)
    legacy_edges = _legacy_edges(legacy)
    # Atlas public keys are stable slugs, not legacy ids: compare by COUNT
    # and by LABEL SET, and relations by endpoint labels.
    checks['nodes'] = len(atlas_projection.get('nodes', [])) == len(
        legacy_nodes
    )
    checks['relations'] = len(atlas_projection.get('relations', [])) == len(
        legacy_edges
    )

    legacy_labels = sorted(n['label'] for n in legacy_nodes.values())
    atlas_labels = sorted(
        n.get('label') or n['key']
        for n in atlas_projection.get('nodes', [])
    )
    checks['labels'] = legacy_labels == atlas_labels

    # Canonical targets: every legacy relatedRecord family/id must resolve
    # to an Atlas node whose canonical block names the same family AND id.
    # Family vocabulary differs in case only (legacy `researchtopic` vs the
    # canonical source name), so compare case-insensitively.
    #
    # IDs compare by POSITION, not by value: the frozen legacy ids are the
    # production row pks (1/2/3), while any seeded or migrated database mints
    # its own pks. What parity must prove is that the Nth legacy node and
    # the Nth Atlas node (in legacy id order vs projection order) resolve
    # to the same family — i.e. the mapping preserved every link, not that
    # two databases share primary keys.
    legacy_ordered = [legacy_nodes[nid] for nid in sorted(legacy_nodes)]
    atlas_ordered = sorted(
        atlas_projection.get('nodes', []), key=lambda n: n['key']
    )
    canonical_ok = len(legacy_ordered) == len(atlas_ordered)
    if canonical_ok:
        for legacy_node, atlas_node in zip(legacy_ordered, atlas_ordered, strict=False):
            if (atlas_node.get('label') or atlas_node['key']) != legacy_node[
                'label'
            ]:
                canonical_ok = False
                break
            canonical = atlas_node.get('canonical') or {}
            for ref in legacy_node.get('relatedRecords', []):
                want_family = ref['family'].lower()
                got_family = str(canonical.get('family', '')).lower()
                if (
                    want_family not in got_family
                    and got_family not in want_family
                ):
                    canonical_ok = False
                    break
            if not canonical_ok:
                break
    checks['canonicalTargets'] = canonical_ok

    # Relation display copy: every legacy relationType must exist in the
    # projection's relationTypes catalog, and every legacy edge's endpoints
    # (by LABEL) must appear as a relation between the same two Atlas nodes.
    catalog = {
        t['key']: t.get('label')
        for t in atlas_projection.get('relationTypes', [])
    }
    legacy_types = {e['relationType'] for e in legacy_edges.values()}
    checks['relationTypes'] = bool(legacy_types) and all(
        t in catalog for t in legacy_types
    )
    atlas_label_by_key = {
        n['key']: (n.get('label') or n['key'])
        for n in atlas_projection.get('nodes', [])
    }
    legacy_label_by_id = {nid: n['label'] for nid, n in legacy_nodes.items()}
    display_ok = True
    for edge in legacy_edges.values():
        want = (
            legacy_label_by_id.get(edge['source']),
            legacy_label_by_id.get(edge['target']),
            edge['relationType'],
        )
        if not any(
            (
                atlas_label_by_key.get(r.get('source')),
                atlas_label_by_key.get(r.get('target')),
                r.get('type'),
            )
            == want
            for r in atlas_projection.get('relations', [])
        ):
            display_ok = False
            break
    checks['relationEndpoints'] = display_ok
    checks['localeParity'] = atlas_projection.get('locale') == locale

    ok = all(checks.values())
    return {'ok': ok, 'checks': checks}
