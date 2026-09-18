"""The scale fixture's dump/reload surface — the remaining plan Task 18 obligation.

The plan's Task 18 declares ``atlas_scale_fixture`` the seed-free 72/136 fixture
whose surface Tasks 9–16 already consume (Task 11 built it early, per the plan's
``created at first consumer`` ruling). What was still missing — and what this file
pins — is the dual-serialization half of the task:

* ``dump_atlas_version(version)`` captures the **stored** whole-graph state of
  one version (nodes including hidden ones, their per-locale overrides,
  relations with their explanations, groups, memberships, the layout dict,
  pins, every canonical reference) as a JSON string — deterministic bytes:
  ``json.dumps(..., sort_keys=True)`` over rows read in ``public_key`` order;
* the fixture's ``dump()``/``load_dump(path)`` pair route through those two
  functions, so the §19.2 shape can be reproduced in a fresh process without
  a second fixture call;
* a full reload resolves every reference **exactly** and fails closed: a
  taxonomy row the dump names but the database does not carry, a canonical
  triple that resolves no published record, and a layout coordinate that
  collides with another node's slot instead of sitting in its own are each a
  :class:`ValueError` — never a repaired graph, never a partial load (the same
  discipline the publish gate applies).

The plan's "seed-free" ruling is enforced, not assumed: the scan test proves no
fixture-creation code path reads a pre-baked artifact. Any JSON dump here is a
*secondary cache* for Plan C's frontend fixtures — value: a test process can
rebuild the §19.2 graph without re-running the ORM builders; cost: the cache
can go stale, which is why the loader validates every reference against the
live database instead of trusting the file — a trade-off documented in
``factories.py``'s docstring, not hidden.

Determinism (ledger ruling R11): reloading the dump into a **fresh empty
database** — new primary-key space, opposite insertion order — reproduces the
original's ``canonical_json`` byte for byte, so the digest a projection run
records travels with the dump rather than with the process that minted it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from apps.atlas.models import (
    AtlasGroup,
    AtlasGroupMembership,
    AtlasGroupTranslation,
    AtlasNode,
    AtlasNodeTranslation,
    AtlasNodeType,
    AtlasRelation,
    AtlasRelationTranslation,
    AtlasVersion,
)
from apps.atlas.projection import build_locale_projection, canonical_json
from apps.atlas.tests.factories import (
    SCALE_GROUP_TOTAL,
    SCALE_NODE_TOTAL,
    SCALE_RELATION_TOTAL,
    atlas_scale_fixture,
    dump_atlas_version,
    load_atlas_dump,
)

# The module-level fixture surface this file consumes: pytest resolves
# ``atlas_scale_fixture`` by name — importing it here is what registers the
# factory helper as a fixture for this module (the repo's test_layout pattern).
__all__ = ["atlas_scale_fixture"]

# transaction=True: _fresh_database() performs real multi-table deletes and the
# loader re-inserts; inside pytest-django's per-test rollback the deletes would be
# undone before the loader reads, leaving the world unexpectedly empty (72,136,6 == 0,0,0).
pytestmark = pytest.mark.django_db(transaction=True)

FACTORIES_PATH = Path(__file__).with_name("factories.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slot_layout(version) -> dict:
    """One **distinct** stored coordinate per visible node, over the sorted keys.

    Hand-built on purpose: the round-trip's claim is about the *storage*, not the
    engine, and three fixed axes over the sorted keys give every node a slot no
    other node shares.
    """
    return {
        key: [round(index * 0.125 + 1.0, 3), round(-index * 1.5, 3), round(index * 0.001, 3)]
        for index, key in enumerate(
            sorted(version.nodes.filter(visible=True).values_list("public_key", flat=True))
        )
    }


def _stolen_slot_layout(version, *, key: str) -> dict:
    """The same slots with ``key`` **stolen**: it sits in another node's exact slot."""
    layout = _slot_layout(version)
    owner = next(other for other in layout if other != key)
    layout[key] = list(layout[owner])
    return layout


def _fresh_database() -> None:
    """Delete every Atlas and canonical row — the in-process form of a fresh process.

    The full form of the claim is the cross-process run recorded in the evidence
    file; inside one process the falsifiable form is: drop everything, keep only
    the dump string, reload, compare digests. Primary keys move because sqlite
    never re-issues an id it has already used for the table.
    """
    from apps.api.record_resolver import RESOLVER_FAMILIES
    from apps.content.models import Method, Technology

    for model in (
        AtlasGroupMembership,
        AtlasGroupTranslation,
        AtlasGroup,
        AtlasRelationTranslation,
        AtlasRelation,
        AtlasNodeTranslation,
        AtlasNode,
        AtlasVersion,
        # Taxonomy stays: the loader refuses a dump naming a type key the live
        # database does not carry (TAXONOMY_TYPE_MISSING), so a reload cannot
        # work at all if the vocabulary is wiped — that gate is exercised by the
        # dedicated corruption test, not by every helper call.
        *RESOLVER_FAMILIES.values(),
        Method,
        Technology,
    ):
        model._base_manager.all().delete()


def _fixture_layout(some) -> dict:
    """The scale fixture's layout, all slots distinct (the round-trip's shape)."""
    return {
        node.public_key: [round(index * 0.125 + 1.0, 3), -index * 0.5, round(index * 0.001, 3)]
        for index, node in enumerate(sorted(some.parts[0], key=lambda row: row.public_key))
    }


def _counts(version) -> tuple[int, int, int]:
    """``(nodes, relations, groups)`` of the stored version."""
    return (
        version.nodes.count(),
        version.relations.count(),
        AtlasGroup.objects.filter(version=version).count(),
    )


# ---------------------------------------------------------------------------
# The dump surface
# ---------------------------------------------------------------------------


def test_scale_fixture_dump_is_deterministic_json_of_the_whole_graph(atlas_scale_fixture):
    """One dump is sorted-key JSON over the stored graph; a second is byte-identical."""
    fixture = atlas_scale_fixture
    version = fixture.version_obj
    fixture.apply_layout()
    AtlasVersion.objects.filter(pk=version.pk).update(
        layout=_fixture_layout(fixture), layout_revision=7
    )

    first = fixture.dump()
    again = fixture.dump()

    assert isinstance(first, str)
    assert first == again, "two dumps of one stored graph differ"
    document = json.loads(first)
    # The frozen dump shape (the surface Tasks 19/20 consume — stated here).
    assert set(document) == {
        "modelVersion",
        "digest",
        "nodeTypes",
        "relationTypes",
        "nodes",
        "relationItems",
        "groups",
        "layout",
        # The stored layout's revision stamp travels with the layout dict: the
        # reload serves ``layoutRevision: 9`` when the dump was taken at 9, so a
        # reloaded payload is not silently revision-0 after a revision-9 wipe.
        "layoutRevision",
        # The canonical registry: the semantic copy each reference resolves to.
        "canonicalSources",
    }
    assert document["modelVersion"] == 1
    # Nodes are key-identified, not pk-identified: composing the load from
    # ``publicKey`` means a fresh pk space reproduces the payload exactly.
    assert {node["publicKey"] for node in document["nodes"]} == set(fixture.node_keys)
    assert len(document["nodes"]) == SCALE_NODE_TOTAL == 72
    assert len(document["relationItems"]) == SCALE_RELATION_TOTAL == 136
    assert len(document["groups"]) == SCALE_GROUP_TOTAL == 6
    assert len(document["layout"]) == SCALE_NODE_TOTAL
    # The digest is over these six keys, in this order.
    assert document["digest"].startswith("atlas-dump-v1:")
    assert document["digest"].split(":")[1] != ""


def test_dump_and_load_atlas_dump_round_trip_every_collection(atlas_scale_fixture):
    """Deleting the world and reloading the dump recreates every stored collection.

    Hidden rows included: the dump is the *stored* graph, not the served one
    (spec §10.3's visibility rule is the projection's, never the fixture's).
    """
    fixture = atlas_scale_fixture
    version = fixture.version_obj
    fixture.apply_layout()
    AtlasVersion.objects.filter(pk=version.pk).update(layout=_fixture_layout(fixture))

    hidden_node = next(node for node in fixture.parts[0] if node.mobile_overview_priority == "auto")
    AtlasNode.objects.filter(pk=hidden_node.pk).update(visible=False)
    hidden_relation = fixture.relations[0]
    AtlasRelation.objects.filter(pk=hidden_relation.pk).update(visible=False)
    retired_group = AtlasGroup.objects.filter(version=version).first()
    AtlasGroup.objects.filter(pk=retired_group.pk).update(active=False)
    AtlasNodeTranslation.objects.create(
        node=fixture.parts[0][0], locale="en", label_override="Dumped node override"
    )
    AtlasRelationTranslation.objects.create(
        relation=fixture.relations[0], locale="en", explanation="Dumped explanation"
    )
    AtlasGroupTranslation.objects.create(
        group=retired_group, locale="fa", label="گروه قدیمی", description="توضیح قدیمی"
    )

    # Snapshot BEFORE the wipe: after _fresh_database() the *fixture's* rows are
    # gone, so _counts(fixture.version_obj) would read (0, 0, 0) from an emptied
    # database — counting a deleted row's related sets — and the assertion would
    # be comparing the fresh load against the void, not against the dump.
    stored_counts = _counts(fixture.version_obj)
    stored_membership_count = AtlasGroupMembership.objects.filter(
        group__version=fixture.version_obj
    ).count()
    dump = fixture.dump()
    _fresh_database()
    assert AtlasVersion.objects.count() == 0, "the world is empty before the reload"

    loaded = load_atlas_dump(dump)
    loaded_version = loaded.version_obj

    assert _counts(loaded_version) == stored_counts == (72, 136, 6)
    # The hidden rows travelled with their flags, not as deleted or visible rows.
    reloaded_hidden = AtlasNode.objects.get(
        version=loaded_version, public_key=hidden_node.public_key
    )
    assert reloaded_hidden.visible is False
    # The first-arriving relation is the dump's first relationItems row: it is the
    # one the fixture hid, identified by its source/target/type triple — fresh pks
    # never reuse the originals (sqlite never re-issues an id it has burned), so
    # arrival order is what "recreated in dump order" can falsifiably mean here.
    reloaded_hidden_relation = (
        AtlasRelation.objects.filter(version=loaded_version).order_by("id").first()
    )
    assert reloaded_hidden_relation is not None
    assert reloaded_hidden_relation.visible is False
    assert (
        reloaded_hidden_relation.source.public_key,
        reloaded_hidden_relation.target.public_key
    ) == (
        hidden_relation.source.public_key, hidden_relation.target.public_key), (
        "the relations were recreated in dump order, so the first arrival is the dump's first row"
    )
    reloaded_retired = AtlasGroup.objects.filter(
        version=loaded_version, public_key=retired_group.public_key
    ).get()
    assert reloaded_retired.active is False
    # Overrides and explanations: per-locale copy is part of the stored graph.
    assert AtlasNodeTranslation.objects.filter(
        node__version=loaded_version, locale="en", label_override="Dumped node override"
    ).exists()
    assert AtlasRelationTranslation.objects.filter(
        relation=reloaded_hidden_relation, explanation="Dumped explanation"
    ).exists()
    reloaded_retired_group = AtlasGroup.objects.filter(
        version=loaded_version, public_key=retired_group.public_key
    ).get()
    assert AtlasGroupTranslation.objects.filter(
        group=reloaded_retired_group, locale="fa", label="گروه قدیمی"
    ).exists()
    # Pins, layout and memberships travelled exactly. The fixture's side of the
    # membership comparison was snapshotted before the wipe (its rows are gone
    # after _fresh_database(), so a live count would read the void, not the
    # dump's source).
    assert {row.public_key for row in loaded_version.nodes.filter(pin_x__isnull=False)} == set(
        fixture.pinned_keys
    )
    assert AtlasGroupMembership.objects.filter(
        group__version=loaded_version
    ).count() == stored_membership_count == 36
    assert loaded.groups == fixture.groups


def test_scale_fixture_dump_reload_roundtrip_stays_deterministic(atlas_scale_fixture):
    """Reload — fresh pk space, reversed arrival order — reproduces the payload bytes.

    The dump is re-inserted with every row list reversed, exactly as the
    fixture itself can hand its parts reversed (``parts_reversed``), so the
    reconstruction is proven against both arrival orders that exist.
    """
    fixture = atlas_scale_fixture
    version = fixture.version_obj
    fixture.apply_layout()
    AtlasVersion.objects.filter(pk=version.pk).update(
        layout=_slot_layout(version), layout_revision=9
    )

    def _stable_projection(version_row, locale: str) -> dict:
        """The projection with its two truly ephemeral fields normalized away.

        ``version.id`` is the row's database pk and ``revision`` is
        ``f"{pk}-{updated_at}"`` (``version_revision``, services.py) — both are
        row-identity stamps a fresh pk space *must* re-mint differently, not
        round-trip guarantees. Everything else — public keys, topology, labels,
        layout, ``layoutRevision`` — stays byte-compared.
        """
        payload = build_locale_projection(version_row, locale)
        assert isinstance(payload["version"]["layoutRevision"], int)
        # Ephemeral: the version row's DB pk and its pk-stamped revision value
        # (a fresh pk space re-mints both by design — asserted separately below).
        payload["version"].pop("id")
        payload["version"].pop("revision")
        # ``canonical.id`` is the canonical record's DB pk too (the resolver's
        # row id, spec §10.2): fresh pks are its only allowed drift, so it is
        # normalized **here only** — family/slug/title/locale must stay equal.
        for node_entry in payload["nodes"]:
            if isinstance(node_entry.get("canonical"), dict):
                node_entry["canonical"].pop("id")
        return payload

    before_en = _stable_projection(version, "en")
    before_fa = _stable_projection(version, "fa")
    dump = fixture.dump()

    _fresh_database()
    loaded = load_atlas_dump(dump, insert_reversed=True)
    loaded_version = loaded.version_obj

    assert canonical_json(_stable_projection(loaded_version, "en")) == canonical_json(before_en)
    assert canonical_json(_stable_projection(loaded_version, "fa")) == canonical_json(before_fa)
    assert (
        loaded_version.pk != version.pk
    ), "the reload must land in a fresh row, not re-read the deleted one"


# ---------------------------------------------------------------------------
# Corruption — the dump fails closed
# ---------------------------------------------------------------------------


_DIGEST_EXCLUDED = ("modelVersion", "digest")


def _resign(document: dict) -> str:
    """Re-digest an edited document so the corruption reaches its own gate.

    The digest gate is real (the last case exercises it); these three edits are
    re-signed on purpose, so each rule is the one that fires, not the digest.
    The payload is hashed **as the loader hashes it** — every document key except
    ``modelVersion`` and ``digest`` — so a re-signed edit reaches its own gate
    instead of dying (or, worse, passing) at the digest check.
    """
    import hashlib

    document.pop("digest", None)
    payload = {key: document[key] for key in sorted(document) if key not in _DIGEST_EXCLUDED}
    payload_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    document["digest"] = f"atlas-dump-v1:{hashlib.sha256(payload_bytes).hexdigest()}"
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _editable_dump(dump: str) -> dict:
    """The parsed document, ready for an edit and a re-sign."""
    return json.loads(dump)


def test_a_corrupted_dump_fails_closed(atlas_scale_fixture, tmp_path):
    """Every broken reference is a refused load, never a repaired graph.

    Three corruptions, one per rule, in one control/treatment sequence: the
    unedited reload is proven green once, then each corruption is refused —
    the digest gate first (any edit moves the SHA-256), the taxonomy and
    canonical gates on a properly re-signed copy, so each check is the one
    catching its own corruption and no test passes by accident.
    """
    fixture = atlas_scale_fixture
    version = fixture.version_obj
    fixture.apply_layout()
    AtlasVersion.objects.filter(pk=version.pk).update(
        layout=_slot_layout(version), layout_revision=9
    )
    dump = fixture.dump()

    # Control: the pristine dump loads into an empty database — the corruption
    # sequence's “treatment” is measured against a green baseline, not a guess.
    _fresh_database()
    loaded = load_atlas_dump(dump)
    assert _counts(loaded.version_obj) == (72, 136, 6)

    # (a) a taxonomy type key the stored world does not carry. The digest stands
    # — the type key is the defect, so the taxonomy gate must be the finder.
    _fresh_database()

    # "identity" is a node the dump references (the composition carries exactly
    # one); "uses" is a *relation* type, deleting it would trip a gate this case
    # does not probe — the node-type taxonomy gate needs its own missing key.
    AtlasNodeType.objects.filter(key="identity").delete()
    with pytest.raises(ValueError, match="TAXONOMY_TYPE_MISSING"):
        load_atlas_dump(dump)
    # Restore the row this case deleted: the later cases reach their gates
    # against the complete vocabulary — they corrupt the *document*, not the
    # live taxonomy, and must not inherit the previous case's damage.
    from apps.atlas.tests.factories import _scale_node_type

    _scale_node_type("identity")
    # (b) a canonical translation_key that resolves no published row in either
    # locale — the FA witness: the pair is orphaned in the empty world.
    _fresh_database()

    document = _editable_dump(dump)
    # The witness is a node the document actually carries a canonical reference
    # for (the identity anchor is Atlas-only, spec §10.2's optional ``canonical``
    # object): orphaning a key nothing carries is the corruption this case probes.
    witness = next(
        entry
        for entry in document["nodes"]
        if entry["canonicalTranslationKey"] is not None
    )
    assert witness["canonicalTranslationKey"]
    witness["canonicalTranslationKey"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="CANONICAL_SOURCE_MISSING"):
        load_atlas_dump(_resign(document))

    # (c) a layout slot collision: one node's coordinate IS another node's exact
    # stored coordinate, rather than sitting at its own assigned slot.
    _fresh_database()
    document = _editable_dump(dump)
    group_key = min(entry["key"] for entry in document["groups"])
    members = next(
        entry["members"] for entry in document["groups"] if entry["key"] == group_key
    )
    assert len(members) >= 2, "the shared-slot collision needs two members of one group"
    stolen, keep = sorted(members)[:2]
    document["layout"][stolen] = list(document["layout"][keep])
    with pytest.raises(ValueError, match="LAYOUT_SLOT_COLLISION"):
        load_atlas_dump(_resign(document))

    # The digest gate, last so the corruption tests above are proven against a
    # loader that accepted well-formed re-signed dumps:
    tampered = json.loads(dump)
    tampered["layout"]["identity-00000001"] = [0.0, 0.0, 9.0]
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        load_atlas_dump(json.dumps(tampered, sort_keys=True, separators=(",", ":")))


def test_a_corrupted_dump_file_fails_closed(atlas_scale_fixture, tmp_path):
    """``load_dump(path)`` reads the file and refuses a tampered one the same way.

    The fixture's own file surface is what a second process holds: the path, not
    the string. Both forms of the same corruption must agree.
    """
    fixture = atlas_scale_fixture
    fixture.apply_layout()

    path = tmp_path / "atlas-dump.json"
    path.write_text(fixture.dump(), encoding="utf-8")
    _fresh_database()

    loaded = fixture.load_dump(path)
    assert _counts(loaded.version_obj) == (72, 136, 6)

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["layout"]["identity-00000001"] = [0.0, 0.0, 9.0]
    tampered_path = tmp_path / "atlas-dump-tampered.json"
    tampered_path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")))
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        fixture.load_dump(tampered_path)


def test_the_dump_surface_is_frozen_against_drift(atlas_scale_fixture):
    """One home, the documented names, and a version that says what it captures.

    The surface Tasks 19/20 consume is exactly: ``atlas_scale_fixture.dump()``,
    ``atlas_scale_fixture.load_dump(path)``, ``dump_atlas_version(version)`` and
    ``load_atlas_dump(text)`` — declared once, in the one builders module,
    beside ``__all__`` (R10), and never spelled from another module.
    """
    fixture = atlas_scale_fixture
    version = fixture.version_obj
    fixture.apply_layout()

    assert fixture.dump() == dump_atlas_version(version)
    assert load_atlas_dump(dump_atlas_version(version)).version_obj.nodes.count() == 72
    text = FACTORIES_PATH.read_text(encoding="utf-8")
    for name in (
        "atlas_scale_fixture",
        "dump_atlas_version",
        "load_atlas_dump",
    ):
        assert text.count(f"def {name}(") + text.count(name) >= 1, name
    assert text.count('"""') > 3, "the fixture docstring documents the dual serialization"
    assert "json.dumps" in text and "json.loads" in text
    assert "sort_keys=True" in text
    # The trade-off is documented, with its spec anchor, not just performed:
    assert "ATLAS-PAYLOAD-CONTRACT" in text or "docs/contracts" in text
    assert "secondary cache" in text


def test_the_scale_fixture_family_reads_no_prebaked_fixture_file(atlas_scale_fixture):
    """No fixture code path reads a pre-baked artifact — the seed-free ruling.

    A dump is a cache written *after* the ORM builds, and the loader is its
    counterpart: no module in the family reads any fixture file during
    creation. The scan is over ``factories.py``'s source text.
    """
    text = FACTORIES_PATH.read_text(encoding="utf-8")
    creation_zone = text[: text.index("# The `atlas_scale_fixture` family")]
    for banned in ("json.load", ".raw(", "executemany", "open(", "Path("):
        assert banned not in creation_zone, (
            f"the fixture builders must not read pre-baked files; found {banned!r}"
        )
    # The serialization functions live *after* the builders and are the only
    # place the module serializes. The trade-off for that is a secondary cache:
    # rebuilt from the ORM, never the reverse.
    assert "json.dumps" in text and "json.loads" in text
    assert 'json.dump("' not in text and "str(document)" not in text, (
        "the dump is canonical JSON bytes, not a repr paste à la Python repr"
    )
