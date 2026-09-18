"""Locale projection and payload assembly — the wire payload (plan Task 14).

The projection is the contract the public endpoint serves: spec §10.2's shape,
materialised per locale from the *stored* topology, with exact-locale resolution
(spec §7.8: "A missing FA projection never falls back to the EN copy, and vice
versa") and no invented values. The tests here prove, in this order:

* the served set and the documented ordering (spec §10.3 "Ordering");
* identity is locale-independent while text is not (spec §7.1/§7.3);
* the wire names are the spec's own — read out of
  ``KNOWLEDGE-ATLAS-V1-DESIGN-SPEC.md`` §10.2, not from memory (spec §10.3
  "Naming": ``mobile_overview_priority`` in storage, ``mobileOverviewPriority``
  on the wire, "those are the only two spellings that exist anywhere");
* the ETag input (spec §10.5) and the frozen contract literal (plan File Map:
  ``apps/atlas/contract.py``);
* §5.4's resolution rule with its per-locale override precedence and **no**
  cross-locale fallback;
* coordinates come from the stored ``version.layout`` (spec §12.1: computed once
  per revision and stored, then served) and a missing one is a ``MISSING_LAYOUT``
  condition at the payload gate, never a fabricated zero;
* determinism as ruling **R11** demands it: the same logical graph with every
  collection inserted in the opposite order (and therefore different primary
  keys) produces byte-identical ``canonical_json``.

The module reads the spec from disk when it can find it and skips the one test
that needs it otherwise, so the assertion is against the document rather than
against a copy of it — ``ATLAS_SPEC_PATH`` points the test at the file when the
layout is unusual.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from apps.api.record_resolver import RESOLVER_FAMILIES, ROUTE_FAMILY_MAP
from apps.atlas.contract import ATLAS_CONTRACT_VERSION
from apps.atlas.models import (
    AtlasGroup,
    AtlasNode,
    AtlasNodeTranslation,
    AtlasRelation,
    AtlasVersion,
)
from apps.atlas.projection import (
    build_locale_projection,
    canonical_json,
    projection_etag,
)
from apps.atlas.services import version_revision
from apps.atlas.tests.factories import (
    _default_node_type,
    _group,
    _group_translation,
    _identity_node_type,
    _membership,
    _node,
    _node_type,
    _relation,
    _relation_translation,
    _relation_type,
    _research_focus_type,
    _seed_pair,
    _version,
    atlas_active_version,
    atlas_scale_fixture,
    atlas_v1,
    seed_pairs,
)
from apps.atlas.validation import validate_payload_contract
from apps.content.models import Profile, ResearchTopic

pytestmark = pytest.mark.django_db

#: The fixture family this module consumes. Listed explicitly (the lesson of
#: ledger row ``8-tool``: a bare fixture import trips ruff's F401/F811).
__all__ = [
    "atlas_active_version",
    "atlas_scale_fixture",
    "atlas_v1",
    "seed_pairs",
]

#: The published timestamp fixture rows carry (``factories.PUBLISHED_AT``), used
#: where a test needs ``publishedAt`` on the wire without importing the constant.
PUBLISHED_AT = datetime(2026, 1, 1, tzinfo=UTC)

#: The graph the order and permutation tests rebuild: two research areas behind
#: the identity anchor, one relation each and one group with two members. Fixed
#: keys, so a test asserts a known order rather than eight random hex characters.
_GRAPH_IDENTITY_KEY = "identity-00000001"
_GRAPH_AREA_KEYS = ("research-area-00000001", "research-area-00000002")
_GRAPH_GROUP_KEY = "group-00000001"


# ---------------------------------------------------------------------------
# The spec's own §10.2 example, read from the document
# ---------------------------------------------------------------------------

#: Path of the design spec, relative to the platform root and to a checkout root.
_SPEC_RELATIVE = Path("Docs/05-delivery/knowledge-atlas/KNOWLEDGE-ATLAS-V1-DESIGN-SPEC.md")


def _spec_text() -> str:
    """The design spec's text, located from this file or ``ATLAS_SPEC_PATH``.

    The spec lives in the platform's ``Docs/`` tree, which is not part of the
    ``Back-End`` repository, so the lookup walks the ancestors of this file and
    also tries each ancestor's sibling ``tahamohammadi-platform`` checkout. When
    nothing matches, the caller skips: an assertion against a *copy* of the
    example would be exactly the "memory" this test exists to avoid.
    """
    candidates: list[Path] = []
    override = os.environ.get("ATLAS_SPEC_PATH")
    if override:
        candidates.append(Path(override))
    for ancestor in Path(__file__).resolve().parents[:6]:
        candidates.append(ancestor / _SPEC_RELATIVE)
        candidates.append(ancestor / "tahamohammadi-platform" / _SPEC_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    pytest.skip(
        "the Knowledge Atlas design spec is not reachable from "
        f"{Path(__file__).resolve()}; set ATLAS_SPEC_PATH to its path"
    )


def _documented_example() -> dict:
    """Spec §10.2's response example, parsed out of the document."""
    text = _spec_text()
    sections = text.split("### 10.2 Response shape", 1)
    assert len(sections) == 2, "spec §10.2 'Response shape' heading not found"
    block = re.search(r"```json\s*\n(.*?)\n```", sections[1], re.DOTALL)
    assert block is not None, "spec §10.2 has no json code block"
    return json.loads(block.group(1))


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _entry(entries: list[dict], key: str) -> dict:
    """The one entry carrying ``key`` — a duplicate would be a contract defect."""
    matches = [entry for entry in entries if entry["key"] == key]
    assert len(matches) == 1, f"{key!r} appears {len(matches)} times"
    return matches[0]


def _leaves(value, path: str = ""):
    """Every ``(path, scalar)`` pair of a payload, for the omission invariant."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def _ordered(items: list, *, reverse: bool) -> list:
    """``items`` as-is, or in the opposite order — the R11 permutation."""
    return list(reversed(items)) if reverse else list(items)


def _spend_node_pks(count: int, prefix: str) -> list[int]:
    """Create throwaway nodes so the *next* rows get later primary keys.

    sqlite hands out ``max(pk) + 1``: a graph deleted and rebuilt in the same
    database reuses the first build's pks unless the watermark is raised in
    between. The permutation test asserts the two builds' pks really differ, so
    this is the control that keeps the test honest (the lesson of Task 11's F4:
    two fresh sqlite databases assign the *same* pks).
    """
    version = _version(status="draft", label=f"{prefix}-version")
    node_type = _default_node_type()
    return sorted(
        _node(version=version, node_type=node_type, public_key=f"{prefix}-{index:08d}").pk
        for index in range(count)
    )


def _graph_pairs() -> dict:
    """The canonical records behind the permutation graph, minted **once**.

    A second call mints different rows (fresh ``translation_key``, fresh slug), so
    the order-permutation test mints the pairs once and rebuilds only the Atlas
    rows between its two snapshots — otherwise the two payloads would describe two
    different sets of CMS records and the byte comparison would be meaningless.
    """
    pairs = {
        key: _seed_pair(
            ResearchTopic,
            uuid4(),
            en={"summary": f"{key} EN summary"},
            fa={"summary": f"خلاصهٔ {key}"},
        )
        for key in _GRAPH_AREA_KEYS
    }
    pairs[_GRAPH_IDENTITY_KEY] = _seed_pair(
        Profile, uuid4(), en={"short_bio": "Identity EN bio"}, fa={"short_bio": "بیوگرافی"}
    )
    return pairs


def _build_graph(version, *, reverse: bool = False, pairs: dict | None = None) -> dict:
    """Create the permutation graph of ``version`` in the requested order.

    Every collection — nodes, relations, memberships — is inserted in the
    opposite order when ``reverse`` is set, and the canonical records behind the
    nodes resolve in both locales, so the projection has text to order and group.
    """
    area_type = _default_node_type()
    identity_type = _identity_node_type()
    focus_type = _research_focus_type()
    pairs = _graph_pairs() if pairs is None else pairs

    nodes: dict[str, AtlasNode] = {}
    node_specs = [
        (_GRAPH_AREA_KEYS[0], area_type),
        (_GRAPH_AREA_KEYS[1], area_type),
        (_GRAPH_IDENTITY_KEY, identity_type),
    ]
    for index, (key, node_type) in enumerate(_ordered(node_specs, reverse=reverse)):
        nodes[key] = _node(
            version=version,
            node_type=node_type,
            public_key=key,
            canonical_translation_key=pairs[key][0].translation_key,
            sort_order=index,
        )

    relations = []
    relation_specs = [
        (_GRAPH_IDENTITY_KEY, _GRAPH_AREA_KEYS[0]),
        (_GRAPH_IDENTITY_KEY, _GRAPH_AREA_KEYS[1]),
    ]
    for index, (source, target) in enumerate(_ordered(relation_specs, reverse=reverse)):
        relations.append(
            _relation(
                source=nodes[source],
                target=nodes[target],
                relation_type=focus_type,
                version=version,
                sort_order=index,
            )
        )

    group = _group(version=version, public_key=_GRAPH_GROUP_KEY, sort_order=0)
    _group_translation(group, locale="en", label="EN group", description="EN description")
    _group_translation(group, locale="fa", label="گروه", description="توضیح")
    for index, key in enumerate(_ordered(list(_GRAPH_AREA_KEYS), reverse=reverse)):
        _membership(group, nodes[key], sort_order=index)

    return {"nodes": nodes, "relations": relations, "group": group, "pairs": pairs}


def _stored_layout(version, *, missing: str | None = None) -> dict:
    """Store one distinct coordinate per visible node and return the dict.

    Written through ``QuerySet.update`` on purpose: a ``save()`` would move
    ``updated_at`` and therefore the payload's ``revision``, and the
    byte-identity test needs the version row's stamps to stand still between the
    two builds.
    """
    keys = sorted(version.nodes.filter(visible=True).values_list("public_key", flat=True))
    layout = {
        key: [index + 0.5, index - 1.25, index + 6.0]
        for index, key in enumerate(keys)
        if key != missing
    }
    AtlasVersion.objects.filter(pk=version.pk).update(layout=layout)
    return layout


def _hide(row) -> None:
    """Hide one node or relation row (spec §10.3: only visible rows are served)."""
    row.visible = False
    row.save(update_fields=["visible"])


# ---------------------------------------------------------------------------
# The served set, the ordering and the catalogs (spec §10.2/§10.3)
# ---------------------------------------------------------------------------


def test_projection_serves_only_visible_entities_and_the_documented_order(atlas_scale_fixture):
    """The plan's first snippet: the served set, the order, the envelope.

    The plan's body reads ``atlas_scale_fixture.visible_count``, which no fixture
    exposes — the count is ``len(fixture.node_keys)``. The order is asserted
    against the rows themselves rather than against an importance list alone:
    §10.3 orders by ``(-importance, public_key)``, so an implementation that
    sorted by importance with an unstable tiebreak would pass the plan's
    ``sorted(..., reverse=True)`` line and fail here.
    """
    fixture = atlas_scale_fixture
    version = fixture.version_obj
    payload = build_locale_projection(version, "en")

    assert payload["contractVersion"] == ATLAS_CONTRACT_VERSION
    assert payload["locale"] == "en"
    assert len(payload["nodes"]) == len(fixture.node_keys)

    visible = list(version.nodes.filter(visible=True))
    expected_nodes = [
        node.public_key
        for node in sorted(visible, key=lambda row: (-row.importance, row.public_key))
    ]
    assert [entry["key"] for entry in payload["nodes"]] == expected_nodes
    assert [entry["importance"] for entry in payload["nodes"]] == sorted(
        (entry["importance"] for entry in payload["nodes"]), reverse=True
    )

    priorities = {
        relation.public_key: relation.relation_type.visual_priority
        for relation in fixture.relations
    }
    assert [entry["key"] for entry in payload["relations"]] == [
        entry["key"]
        for entry in sorted(
            ({"key": relation.public_key} for relation in fixture.relations),
            key=lambda entry: (-priorities[entry["key"]], entry["key"]),
        )
    ]

    node_types = {node.node_type for node in visible}
    assert [entry["key"] for entry in payload["nodeTypes"]] == [
        node_type.key for node_type in sorted(node_types, key=lambda row: (row.sort_order, row.key))
    ]
    relation_types = {relation.relation_type for relation in fixture.relations}
    assert [entry["key"] for entry in payload["relationTypes"]] == [
        relation_type.key
        for relation_type in sorted(relation_types, key=lambda row: (row.sort_order, row.key))
    ]

    assert [entry["key"] for entry in payload["groups"]] == [
        group.public_key
        for group in AtlasGroup.objects.filter(version=version).order_by("sort_order", "public_key")
    ]

    assert payload["version"]["id"] == version.pk
    assert payload["version"]["nodeCount"] == len(payload["nodes"])
    assert payload["version"]["relationCount"] == len(payload["relations"])
    assert payload["version"]["layoutRevision"] == version.layout_revision


def test_ids_are_identical_across_locales_while_text_differs(atlas_v1):
    """The plan's second snippet, on the fixture that can carry text.

    The plan runs it on ``atlas_scale_fixture``, whose nodes have neither
    canonical rows nor overrides (its own module docstring says Task 18 adds the
    projection data), so ``[n["label"] for n in en["nodes"]] != [n["label"] …]``
    compares two lists of missing keys. The bilingual R5 fixture is where the
    plan's own Task 10 parity tests live, and there the identity rule
    (spec §7.1: identical entities and counts in EN and FA) and the texture rule
    (§7.3) are both non-vacuous.
    """
    version = atlas_v1.version
    en = build_locale_projection(version, "en")
    fa = build_locale_projection(version, "fa")

    assert [entry["key"] for entry in en["nodes"]] == [entry["key"] for entry in fa["nodes"]]
    assert [entry["key"] for entry in en["relations"]] == [
        entry["key"] for entry in fa["relations"]
    ]
    assert [entry.get("label") for entry in en["nodes"]] != [
        entry.get("label") for entry in fa["nodes"]
    ]

    assert all(
        entry["canonical"]["href"].startswith("/en/")
        for entry in en["nodes"]
        if entry.get("canonical")
    )
    assert all(
        entry["canonical"]["href"].startswith("/fa/")
        for entry in fa["nodes"]
        if entry.get("canonical")
    )

    identity = _entry(en["nodes"], atlas_v1.identity.public_key)["canonical"]
    assert identity["family"] == "profile"
    assert identity["family"] in RESOLVER_FAMILIES
    assert identity["routeFamily"] == ROUTE_FAMILY_MAP["profile"] == "about"
    assert identity["href"] == f"/en/about/{identity['slug']}/"
    fa_identity = _entry(fa["nodes"], atlas_v1.identity.public_key)["canonical"]
    assert identity["href"] != fa_identity["href"]
    assert fa_identity["href"] == f"/fa/about/{fa_identity['slug']}/"

    area = _entry(en["nodes"], atlas_v1.areas[0].public_key)["canonical"]
    assert area["family"] == "researchtopic"
    assert area["routeFamily"] == ROUTE_FAMILY_MAP["researchtopic"] == "research"
    assert area["href"] == f"/en/research/{area['slug']}/"


def test_canonical_href_is_derived_from_the_family_and_never_invented(seed_pairs):
    """§10.3 "Canonical": the four routed families get a route, two get none.

    ``method`` and ``technology`` are published entities with **no** public route
    in v1 (spec §5.2; §25) — a fabricated ``/en/method/…`` is exactly what this
    test forbids. Their record still resolves, so ``canonical`` stays present and
    simply carries no ``routeFamily``/``href``.
    """
    version = _version(status="draft", label="atlas-projection-families")
    families = (
        ("identity-00000001", "profile", _identity_node_type()),
        ("research-area-00000001", "research_topic", _default_node_type()),
        (
            "project-00000001",
            "project",
            _node_type("project", canonical_source="project", semantic_role="record"),
        ),
        (
            "publication-00000001",
            "publication",
            _node_type("publication", canonical_source="publication", semantic_role="record"),
        ),
        ("method-00000001", "method", _node_type("method", canonical_source="method")),
        (
            "technology-00000001",
            "technology",
            _node_type("technology", canonical_source="technology"),
        ),
    )
    for key, source, node_type in families:
        _node(
            version=version,
            node_type=node_type,
            public_key=key,
            canonical_translation_key=seed_pairs.as_dict()[source][0].translation_key,
        )
    _stored_layout(version)

    rows = {source: pair[0] for source, pair in seed_pairs.as_dict().items()}
    assert {row._meta.model_name for row in rows.values()} - set(RESOLVER_FAMILIES) == {
        "method",
        "technology",
    }

    for locale in ("en", "fa"):
        payload = build_locale_projection(version, locale)
        assert validate_payload_contract(payload) == []
        for key, source, _node_type_row in families:
            row = seed_pairs.as_dict()[source][0 if locale == "en" else 1]
            family = row._meta.model_name
            expected = {
                "family": family,
                "id": str(row.pk),
                "slug": row.slug,
                "title": row.title,
            }
            if family in ROUTE_FAMILY_MAP:
                expected["routeFamily"] = ROUTE_FAMILY_MAP[family]
                expected["href"] = f"/{locale}/{ROUTE_FAMILY_MAP[family]}/{row.slug}/"
            assert _entry(payload["nodes"], key)["canonical"] == expected
        for _key, source, _node_type_row in families:
            if source in {"method", "technology"}:
                assert f"/{locale}/{source}/" not in canonical_json(payload).decode("utf-8")


def test_a_record_with_a_blank_slug_is_served_without_a_route(seed_pairs):
    """§10.3: the href is *derived* from the family and the slug — no slug, no path.

    A published row with an empty slug is a content defect the publish gate does
    not name, so the honest projection of it is a ``canonical`` block that stops
    after the record's identity: ``/en/research//`` would be a fabricated URL, and
    an empty string is never served (§10.3's omission rule). The record still
    resolved, so the block stays — the two facts are separate.
    """
    version = _version(status="draft", label="atlas-projection-blank-slug")
    node_type = _default_node_type()
    pair = seed_pairs.research_topic
    ResearchTopic.objects.filter(pk=pair[0].pk).update(slug="")
    node = _node(
        version=version,
        node_type=node_type,
        public_key="research-area-00000001",
        canonical_translation_key=pair[0].translation_key,
    )
    _stored_layout(version)

    payload = build_locale_projection(version, "en")
    assert _entry(payload["nodes"], node.public_key)["canonical"] == {
        "family": "researchtopic",
        "id": str(pair[0].pk),
        "title": pair[0].title,
    }
    assert validate_payload_contract(payload) == []


def test_the_spec_lookup_skips_instead_of_failing_when_the_document_is_absent(monkeypatch):
    """A checkout without the platform's ``Docs/`` must skip, not go red.

    The spec lives outside this repository, so the one test that asserts the wire
    names against the document is the only test in the suite that reads a file
    outside the checkout: it has to degrade to a **skip** wherever the file cannot
    be found, and ``ATLAS_SPEC_PATH`` is the way to point it at the file when the
    layout is unusual. This pins the mechanism, not the outcome.
    """
    from apps.atlas.tests import test_projection as module

    monkeypatch.delenv("ATLAS_SPEC_PATH", raising=False)
    monkeypatch.setattr(
        module, "_SPEC_RELATIVE", Path("Docs/05-delivery/knowledge-atlas/ABSENT.md")
    )
    with pytest.raises(pytest.skip.Exception, match="ATLAS_SPEC_PATH"):
        module._spec_text()


def test_every_documented_field_name_is_emitted_and_nothing_is_invented(atlas_active_version):
    """The wire names are asserted against the spec's own §10.2 example.

    A fully populated payload — every optional field non-empty — so the key sets
    can be compared for *equality* in both directions: a name the spec does not
    document fails here, and a documented name that stops being served fails too.
    """
    example = _documented_example()
    version = atlas_active_version.version
    group = _group(version=version, public_key=_GRAPH_GROUP_KEY, sort_order=0)
    _group_translation(group, locale="en", label="Vision & language", description="EN description")
    _group_translation(group, locale="fa", label="زبان و بینش", description="توضیح")
    for member in (atlas_active_version.identity, atlas_active_version.areas[0]):
        _membership(group, member)
    relation = atlas_active_version.relations[0]
    _relation_translation(relation, locale="en", explanation="Why this edge exists")
    _relation_translation(relation, locale="fa", explanation="چرا این یال وجود دارد")
    atlas_active_version.override(
        atlas_active_version.identity, locale="en", aliases=["Persian text-to-SQL"]
    )
    AtlasVersion.objects.filter(pk=version.pk).update(published_at=PUBLISHED_AT)

    payload = build_locale_projection(version, "en")

    assert set(payload) == set(example)
    assert payload["contractVersion"] == example["contractVersion"]
    assert set(payload["version"]) == set(example["version"])
    assert set(payload["nodeTypes"][0]) == set(example["nodeTypes"][0])
    assert set(payload["relationTypes"][0]) == set(example["relationTypes"][0])

    node = _entry(payload["nodes"], atlas_active_version.identity.public_key)
    assert set(node) == set(example["nodes"][0])
    assert set(node["canonical"]) == set(example["nodes"][0]["canonical"])
    assert set(node["position"]) == set(example["nodes"][0]["position"])

    relation_entry = _entry(payload["relations"], relation.public_key)
    assert set(relation_entry) == set(example["relations"][0])

    group_entry = _entry(payload["groups"], group.public_key)
    assert set(group_entry) == set(example["groups"][0])

    mobile = {key for key in node if "mobile" in key.lower()}
    assert mobile == {"mobileOverviewPriority"}, (
        "spec §10.3 Naming: mobileOverviewPriority is the only compact-overview "
        f"spelling on the wire; found {sorted(mobile)}"
    )


def test_no_field_is_ever_served_as_null_or_an_empty_string(atlas_v1):
    """§10.3 "Omission": empty optional fields are omitted, never sent empty.

    The spec's own example spells ``"description": ""`` and ``"explanation":
    null``; the field-rule table is the rule ("never sent as empty strings") and
    the example is an illustration, so the invariant is asserted globally over
    every scalar of every served value — including the two nodes of the fixture
    that resolve in only one locale.
    """
    for locale in ("en", "fa"):
        payload = build_locale_projection(atlas_v1.version, locale)
        for path, value in _leaves(payload, locale):
            assert value is not None, f"{path} is null"
            assert value != "", f"{path} is an empty string"
        for node in payload["nodes"]:
            assert "canonical" not in node or node["canonical"]["title"], node["key"]


def test_aliases_are_served_for_their_locale_and_blank_ones_dropped(atlas_v1):
    """§5.3/§7.3: aliases are per-locale override data; blank entries are copy."""
    node = atlas_v1.areas[0]
    atlas_v1.override(node, locale="en", aliases=["Persian text-to-SQL", "  ", ""])
    en = _entry(build_locale_projection(atlas_v1.version, "en")["nodes"], node.public_key)
    fa = _entry(build_locale_projection(atlas_v1.version, "fa")["nodes"], node.public_key)
    assert en["aliases"] == ["Persian text-to-SQL"]
    assert "aliases" not in fa


# ---------------------------------------------------------------------------
# The contract literal and the ETag input (spec §10.5; plan File Map)
# ---------------------------------------------------------------------------


def test_the_contract_version_is_the_frozen_literal_in_exactly_one_place():
    """``atlas01-1.0.0`` exists once under ``apps/atlas/`` — and is the served one.

    Task 15 serves it and Task 19 pins it in the OpenAPI snapshot, so a second
    literal anywhere in the app would be a second source of truth: the scan is
    over the app's top-level modules (the test files legitimately spell the value
    out in expectations).
    """
    assert ATLAS_CONTRACT_VERSION == "atlas01-1.0.0"
    app_root = Path(__file__).resolve().parents[1]
    owners = sorted(
        path.name
        for path in app_root.glob("*.py")
        if "atlas01-1.0.0" in path.read_text(encoding="utf-8")
    )
    assert owners == ["contract.py"]


def test_etag_is_stable_for_an_unchanged_version_and_changes_on_projection_change(
    atlas_scale_fixture,
):
    """The plan's third snippet: stable while nothing moves, different when it does.

    ``rename_one_node()`` does not exist on the fixture, so the projection change
    is made the way a rename really happens for a locale: a written override
    (spec §5.4.3). The derivation is pinned too — §10.5's ``"<version.id>-<16 hex
    of a SHA-256 over the canonical JSON of this locale projection>"``.
    """
    version = atlas_scale_fixture.version_obj
    body = canonical_json(build_locale_projection(version, "en"))
    first = projection_etag(build_locale_projection(version, "en"))
    second = projection_etag(build_locale_projection(version, "en"))
    assert first == second
    assert first == f"{version.pk}-{hashlib.sha256(body).hexdigest()[:16]}"
    assert re.fullmatch(r"[0-9]+-[0-9a-f]{16}", first)

    node = atlas_scale_fixture.parts[0][0]
    AtlasNodeTranslation.objects.create(
        node=node, locale="en", label_override="Renamed by the ETag test"
    )
    assert projection_etag(build_locale_projection(version, "en")) != first


def test_canonical_json_key_order_does_not_depend_on_insertion_order(atlas_scale_fixture):
    """REVIEW-14 fix: ``sort_keys=True`` is the ETag input's contract (§10.5).

    ``_redirect`` only checked this because the projection always *builds* the
    payload in the same order. Two callers assembling identical content in
    different insertion orders (the preview path, an OpenAPI snapshot) must get
    the same digest, so the key-sorted contract has to be falsifiable rather
    than assumed; the projection fixtures make that assertion real.
    """
    payload = build_locale_projection(atlas_scale_fixture.version_obj, "en")
    reordered = dict(reversed(list(payload.items())))
    assert canonical_json(payload) == canonical_json(reordered)

    for node in payload["nodes"][:2]:
        assert canonical_json(node) == canonical_json(
            dict(reversed(list(node.items())))
        )


def test_payload_size_stays_inside_the_budget(atlas_scale_fixture):
    """The plan's fourth snippet, on the 72-node graph (spec §10.4's ceiling).

    Honest scope: the scale fixture carries no projection text until Task 18 adds
    it, so this measures the *structural* payload — roughly 30 KB of the 120 KB
    raw budget — and Task 18's data will make the measurement meaningful.
    """
    body = canonical_json(build_locale_projection(atlas_scale_fixture.version_obj, "en"))
    assert len(body) < 120_000, "80-node raw payload budget (spec §10.4)"
    assert isinstance(body, bytes)
    assert json.loads(body.decode("utf-8"))["locale"] == "en"


# ---------------------------------------------------------------------------
# Exact-locale resolution and override precedence (spec §5.4, §7.8)
# ---------------------------------------------------------------------------


def test_exact_locale_resolution_never_falls_back_to_the_other_locale(atlas_v1):
    """Spec §7.8: the honest outcomes are the override, the row, or an issue.

    ``en_missing_node`` resolves FA only and ``fa_missing_node`` EN only, so each
    payload has one node with no label. The FA copy of the FA-only node must be
    absent from the EN payload *entirely* — the assertion is made against the
    serialised payload, not only against the entry, so a fallback smuggled into
    any other field is caught too.
    """
    version = atlas_v1.version
    en = build_locale_projection(version, "en")
    fa = build_locale_projection(version, "fa")

    en_only = _entry(en["nodes"], atlas_v1.fa_missing_node.public_key)
    fa_only = _entry(fa["nodes"], atlas_v1.fa_missing_node.public_key)
    assert en_only["label"] == "researchtopic en"
    assert "label" not in fa_only
    assert "canonical" not in fa_only

    fa_only_node = _entry(fa["nodes"], atlas_v1.en_missing_node.public_key)
    en_only_node = _entry(en["nodes"], atlas_v1.en_missing_node.public_key)
    assert fa_only_node["label"] == "researchtopic fa"
    assert "label" not in en_only_node
    assert "canonical" not in en_only_node

    # No other locale's copy anywhere in the payload, both directions.
    en_text = canonical_json(en).decode("utf-8")
    fa_text = canonical_json(fa).decode("utf-8")
    assert "researchtopic fa" not in en_text
    assert "researchtopic en" not in fa_text
    assert "خلاصهٔ دانه" not in en_text
    assert "EN summary" not in fa_text


def test_a_non_blank_override_wins_over_the_row_and_a_blank_one_loses(atlas_v1):
    """Spec §5.4's precedence: the per-locale override, then the exact-locale row.

    A whitespace-only override is not an override — the same rule the parity
    gate's ``label_override.strip()`` applies, and the difference between
    "covered" and "unresolved" would otherwise be invisible here.
    """
    node = atlas_v1.areas[0]
    atlas_v1.override(node, locale="en", label="   \t ")
    blank = _entry(build_locale_projection(atlas_v1.version, "en")["nodes"], node.public_key)
    assert blank["label"] == "researchtopic en"

    atlas_v1.override(node, locale="en", label="Custom EN label", summary="Custom EN summary")
    filled = _entry(build_locale_projection(atlas_v1.version, "en")["nodes"], node.public_key)
    assert filled["label"] == "Custom EN label"
    assert filled["summary"] == "Custom EN summary"
    assert filled["canonical"]["title"] == "researchtopic en"  # the record is still named


def test_an_override_for_the_other_locale_never_reaches_this_payload(atlas_v1):
    """The override is per locale, exactly like the resolution (spec §5.4)."""
    atlas_v1.override(atlas_v1.fa_missing_node, locale="fa", label="فقط فارسی")
    en = _entry(
        build_locale_projection(atlas_v1.version, "en")["nodes"],
        atlas_v1.fa_missing_node.public_key,
    )
    fa = _entry(
        build_locale_projection(atlas_v1.version, "fa")["nodes"],
        atlas_v1.fa_missing_node.public_key,
    )
    assert en["label"] == "researchtopic en"
    assert fa["label"] == "فقط فارسی"
    assert "فقط فارسی" not in canonical_json(
        build_locale_projection(atlas_v1.version, "en")
    ).decode("utf-8")


def test_the_accessibility_label_falls_back_to_the_served_label(atlas_v1):
    """Spec §5.3: a blank override means "the resolved label"; §10.2 names it."""
    node = atlas_v1.areas[1]
    plain = _entry(build_locale_projection(atlas_v1.version, "en")["nodes"], node.public_key)
    assert plain["accessibleLabel"] == plain["label"] == "researchtopic en"

    atlas_v1.override(node, locale="en", label="Visible", aliases=None)
    AtlasNodeTranslation.objects.filter(node=node, locale="en").update(
        accessible_label_override="Screen-reader copy"
    )
    filled = _entry(build_locale_projection(atlas_v1.version, "en")["nodes"], node.public_key)
    assert filled["label"] == "Visible"
    assert filled["accessibleLabel"] == "Screen-reader copy"


# ---------------------------------------------------------------------------
# Stored layout, z and the fail-closed conditions
# ---------------------------------------------------------------------------


def test_position_comes_from_the_stored_layout_and_is_never_zero_filled(atlas_v1):
    """The stored layout is the served one (spec §12.1/§12.8; ruling R13-g).

    Two claims: per-node fidelity of all three axes (including ``z``, the one
    coordinate no stage but the depth rule authors) and §10.3's "rounded to 3
    decimals" at the wire boundary. Then the fail-closed half: a visible node
    whose stored layout has no coordinate is served **without** a position, and
    the payload gate reports ``MISSING_LAYOUT`` — never a zero coordinate.
    """
    version = atlas_v1.version
    stored = _stored_layout(version)
    payload = build_locale_projection(version, "en")
    assert validate_payload_contract(payload) == []

    for entry in payload["nodes"]:
        x, y, z = stored[entry["key"]]
        assert entry["position"] == {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}

    # A value authored outside the engine (four decimals) is served rounded to
    # the documented precision rather than recomputed or truncated away.
    AtlasVersion.objects.filter(pk=version.pk).update(
        layout={**stored, atlas_v1.areas[0].public_key: [1.2345, 2.0, 3.0]}
    )
    rounded = _entry(
        build_locale_projection(version, "en")["nodes"], atlas_v1.areas[0].public_key
    )
    assert rounded["position"] == {"x": 1.234, "y": 2.0, "z": 3.0}

    missing = atlas_v1.areas[1].public_key
    layout = _stored_layout(version, missing=missing)
    AtlasVersion.objects.filter(pk=version.pk).update(layout=layout)
    broken = build_locale_projection(version, "en")
    entry = _entry(broken["nodes"], missing)
    assert "position" not in entry
    assert entry.get("position") != {"x": 0.0, "y": 0.0, "z": 0.0}
    issues = validate_payload_contract(broken)
    assert [issue.code for issue in issues] == ["MISSING_LAYOUT"]
    assert issues[0].node_key == missing


def test_the_served_payload_passes_its_own_contract_gate(atlas_active_version):
    """The gate Task 15 calls is satisfied by a version that should be servable."""
    version = atlas_active_version.version
    for locale in ("en", "fa"):
        assert validate_payload_contract(build_locale_projection(version, locale)) == []


# ---------------------------------------------------------------------------
# The served set: visibility, catalogs, groups, drafts
# ---------------------------------------------------------------------------


def test_hidden_nodes_and_relations_are_not_served(atlas_v1):
    """§10.3 "Visibility": only ``visible`` nodes and relations reach the payload."""
    version = atlas_v1.version
    hidden_node = atlas_v1.fa_missing_node
    hidden_relations = [
        relation
        for relation in AtlasRelation.objects.filter(version=version).select_related("target")
        if relation.target_id == hidden_node.pk
    ]
    _hide(hidden_node)
    for relation in hidden_relations:
        _hide(relation)

    for locale in ("en", "fa"):
        payload = build_locale_projection(version, locale)
        keys = {entry["key"] for entry in payload["nodes"]}
        relation_keys = {entry["key"] for entry in payload["relations"]}
        assert hidden_node.public_key not in keys
        for relation in hidden_relations:
            assert relation.public_key not in relation_keys
        assert "fa_missing" not in "".join(sorted(keys))
        assert validate_payload_contract(payload) == []
        assert all(
            entry["source"] in keys and entry["target"] in keys for entry in payload["relations"]
        )


def test_catalogs_are_limited_to_active_types_actually_used(atlas_v1):
    """The plan's Step 3: "catalogs limited to active types actually used".

    An unused type, a type only an invisible node uses, and a retired type are
    all absent from the catalogs. The retired type is the interesting one: the
    visible node that still uses it then references a type the payload does not
    carry, which the payload gate reports — Task 15's fail-closed path, not a
    silently unfilterable node.
    """
    version = atlas_v1.version
    _stored_layout(version)
    unused = _node_type("unused-type")
    hidden_type = _node_type("hidden-type")
    hidden_node = _node(
        version=version,
        node_type=hidden_type,
        public_key="hidden-type-00000001",
        visible=False,
    )
    assert hidden_node.pk

    payload = build_locale_projection(version, "en")
    catalog = {entry["key"] for entry in payload["nodeTypes"]}
    assert catalog == {"identity", "research-area"}
    assert {entry["key"] for entry in payload["relationTypes"]} == {"research-focus", "related-to"}
    assert unused.key not in catalog and hidden_type.key not in catalog
    assert validate_payload_contract(payload) == []

    area_type = _default_node_type()
    area_type.active = False
    area_type.save(update_fields=["active"])
    retired = build_locale_projection(version, "en")
    assert "research-area" not in {entry["key"] for entry in retired["nodeTypes"]}
    issues = validate_payload_contract(retired)
    assert [issue.code for issue in issues] == ["PAYLOAD_CONTRACT_INVALID"] * len(issues)
    assert {issue.node_key for issue in issues} == {
        entry["key"] for entry in retired["nodes"] if entry["type"] == "research-area"
    }

    # REVIEW-14 fix: the relation-type half of the same rule. The original test
    # only retired a node type, so deleting the ``active`` filter in
    # ``_relation_type_catalog`` (projection.py:466) survived every test while a
    # §10.3-forbidden catalog entry stayed served and the payload gate went
    # silent — the inverse of the fail-closed contract Tasks 15/16 rely on.
    relation_type = _relation_type("co-occurs")
    _relation(atlas_v1.identity, atlas_v1.areas[0], relation_type=relation_type)
    relation_type.active = False
    relation_type.save(update_fields=["active"])
    retired_types = build_locale_projection(version, "en")
    assert relation_type.key not in {entry["key"] for entry in retired_types["relationTypes"]}
    relation_issues = validate_payload_contract(retired_types)
    assert any(
        issue.code == "PAYLOAD_CONTRACT_INVALID" and issue.relation_key is not None
        for issue in relation_issues
    )


def test_groups_serve_the_locale_copy_and_only_visible_members(atlas_v1):
    """§5.5/§10.2: groups carry localized copy and the node keys of their members."""
    version = atlas_v1.version
    first = _group(version=version, public_key="group-00000002", sort_order=2)
    second = _group(version=version, public_key="group-00000001", sort_order=1)
    for group, label in ((first, "Second"), (second, "First")):
        _group_translation(group, locale="en", label=label)
        _group_translation(group, locale="fa", label=f"گروه {label}")
    for member in (atlas_v1.identity, atlas_v1.areas[0], atlas_v1.fa_missing_node):
        _membership(second, member)
    _stored_layout(version)
    _hide(atlas_v1.fa_missing_node)
    for relation in AtlasRelation.objects.filter(version=version, target=atlas_v1.fa_missing_node):
        _hide(relation)

    en = build_locale_projection(version, "en")
    fa = build_locale_projection(version, "fa")
    assert [entry["key"] for entry in en["groups"]] == ["group-00000001", "group-00000002"]
    assert [entry["key"] for entry in en["groups"]] == [entry["key"] for entry in fa["groups"]]

    served = _entry(en["groups"], "group-00000001")
    assert served["label"] == "First"
    assert "description" not in served
    assert served["nodeKeys"] == sorted(
        [atlas_v1.identity.public_key, atlas_v1.areas[0].public_key]
    )
    assert atlas_v1.fa_missing_node.public_key not in served["nodeKeys"]
    assert _entry(fa["groups"], "group-00000001")["label"] == "گروه First"
    assert validate_payload_contract(en) == []


def test_a_visible_relation_to_a_hidden_node_fails_closed_at_the_gate(atlas_v1):
    """The visible relation is served; the payload then fails its own contract.

    Spec §10.3 serves visible relations and visible nodes, so this state has no
    consistent payload: the honest answer is the served row *plus* the gate's
    finding, which Task 15 turns into its 500 with no partial payload (§10.6) —
    dropping the relation silently would serve a topology the author did not
    write.
    """
    version = atlas_v1.version
    _stored_layout(version)
    _hide(atlas_v1.en_missing_node)
    payload = build_locale_projection(version, "en")
    relation_keys = {entry["key"] for entry in payload["relations"]}
    dangling = [
        relation.public_key
        for relation in AtlasRelation.objects.filter(version=version).select_related("target")
        if relation.target_id == atlas_v1.en_missing_node.pk
    ]
    assert set(dangling) <= relation_keys
    issues = validate_payload_contract(payload)
    assert [issue.code for issue in issues] == ["PAYLOAD_CONTRACT_INVALID"] * len(issues)
    assert {issue.relation_key for issue in issues} == set(dangling)


def test_another_versions_draft_node_is_never_served(atlas_active_version):
    """A visible node of a *draft* version is unreachable from this payload."""
    version = atlas_active_version.version
    draft_key = atlas_active_version.add_draft_only_node()
    for locale in ("en", "fa"):
        payload = build_locale_projection(version, locale)
        keys = {entry["key"] for entry in payload["nodes"]}
        assert draft_key not in keys
        assert keys == set(
            version.nodes.filter(visible=True).values_list("public_key", flat=True)
        )
        assert validate_payload_contract(payload) == []


def test_the_version_block_carries_the_stored_stamps_and_the_served_counts(
    atlas_active_version, atlas_v1
):
    """§10.2's version block, field by field, for an active and a draft row."""
    active = atlas_active_version.version
    payload = build_locale_projection(active, "en")
    block = payload["version"]
    assert block["id"] == active.pk
    assert block["revision"] == version_revision(active)
    assert block["nodeCount"] == len(payload["nodes"])
    assert block["relationCount"] == len(payload["relations"])
    assert block["layoutRevision"] == active.layout_revision

    AtlasVersion.objects.filter(pk=active.pk).update(published_at=PUBLISHED_AT)
    stamped = build_locale_projection(active, "en")["version"]
    assert stamped["publishedAt"] == PUBLISHED_AT.isoformat()

    draft = build_locale_projection(atlas_v1.version, "en")["version"]
    assert "publishedAt" not in draft


def test_an_unknown_locale_is_refused_rather_than_projected(atlas_v1):
    """§10.1's locale set is closed: no other locale has a projection."""
    with pytest.raises(ValueError, match="locale"):
        build_locale_projection(atlas_v1.version, "de")
    assert build_locale_projection(atlas_v1.version, "fa")["locale"] == "fa"


# ---------------------------------------------------------------------------
# Determinism (ruling R11): same graph, opposite arrival order, same bytes
# ---------------------------------------------------------------------------


def test_permuting_every_row_order_keeps_the_payload_bytes_identical():
    """R11's falsification form: permute the insertion order, assert identity.

    One version row, built twice — ascending, then descending — with the pk
    watermark raised in between so the second build's rows carry different
    primary keys. The layout is rewritten through ``update()`` so the version's
    ``updated_at`` (and therefore ``revision``) stands still: the *whole* payload
    must come out byte-identical, envelope included.
    """
    version = _version(status="draft", label="atlas-projection-order")
    _spend_node_pks(4, "junk-a")
    pairs = _graph_pairs()
    first = _build_graph(version, reverse=False, pairs=pairs)
    layout = _stored_layout(version)
    before = canonical_json(build_locale_projection(version, "en"))
    before_fa = canonical_json(build_locale_projection(version, "fa"))
    first_pks = sorted(node.pk for node in first["nodes"].values())

    AtlasRelation.objects.filter(version=version).delete()
    AtlasGroup.objects.filter(version=version).delete()
    AtlasNode.objects.filter(version=version).delete()
    _spend_node_pks(4, "junk-b")
    second = _build_graph(version, reverse=True, pairs=pairs)
    second_pks = sorted(node.pk for node in second["nodes"].values())
    assert second_pks != first_pks, "the permutation did not move the primary keys"
    assert AtlasVersion.objects.filter(pk=version.pk).update(layout=layout) == 1

    assert canonical_json(build_locale_projection(version, "en")) == before
    assert canonical_json(build_locale_projection(version, "fa")) == before_fa
    assert len(json.loads(before.decode("utf-8"))["nodes"]) == len(first["nodes"])
    assert sorted(second["nodes"]) == sorted(first["nodes"])  # same graph, new pks
