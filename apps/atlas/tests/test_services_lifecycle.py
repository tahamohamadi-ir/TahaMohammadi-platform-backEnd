"""Clone and transactional publish services (spec §8.1–§8.3, §12.1) — plan Task 13.

The three services in ``apps/atlas/services.py`` are the lifecycle's only writers:

* :func:`clone_version` carries a version's whole graph into a new draft —
  nodes, their per-locale overrides, relations, relation explanations, groups,
  group copy and memberships — **keys included, and never re-minted**. That is
  what ruling **R9** (public-key uniqueness is *per version*) exists for: spec
  §8.1's flow ``Active vN ──clone──▶ Draft vN+1`` needs both versions to hold the
  same keys at once, and the clone regression test has existed since ``9271d0e``.
* :func:`activate_version` publishes a draft in one ``transaction.atomic`` block:
  it re-reads the row inside that transaction (ledger row ``13-note`` —
  ``validate_layout``/``validate_pins`` read the *in-memory* instance, so a stale
  instance validates a stale layout), re-runs the aggregate
  :func:`validate_version` gate against the fresh row, and only then archives the
  previous active row **before** it activates the draft. The order is
  load-bearing: ``atlas_version_unique_active`` is a partial unique index on
  ``status='active'``, and the opposite order dies with ``IntegrityError`` (the
  Task 6 review reproduced exactly that), so archive-then-activate is the only
  sequence that can succeed — and the statement order is asserted here rather
  than assumed.
* :func:`recompute_layout` is spec §8.2's ``POST …/layout`` action. It re-reads
  the row too, so the revision it counts up from is the *stored* one, and it
  refuses a non-draft version: an active version is served as-is (spec §8.1,
  "Editing an active version is refused").

The plan's Step-1 snippets are the first six tests below. Five of them run
verbatim; the sixth — ``test_active_version_cannot_be_edited_in_place`` — is
**unsatisfiable as written** (its body is the conditional expression
``clone_version(...) if False else None``, which evaluates to ``None`` and never
calls the service, so ``pytest.raises`` could only ever report "DID NOT RAISE";
the literal run is captured in ``.atlas-evidence/raw/``), and is corrected to the
plan's stated intent: no service edits an active version in place. The plan's
Step 4 also predicts ``5 passed`` for the six snippets it defines.

Fixtures are imported by name (``factories.py`` is not a conftest), so ``__all__``
marks them as used-by-design: pyflakes cannot see a test parameter as a use of a
module-level binding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext

from apps.atlas import services
from apps.atlas.models import (
    AtlasGroup,
    AtlasGroupMembership,
    AtlasNode,
    AtlasVersion,
)
from apps.atlas.services import (
    AlreadyActive,
    ImmutableActive,
    PreconditionFailed,
    ValidationFailed,
    activate_version,
    clone_version,
    recompute_layout,
    version_revision,
)
from apps.atlas.tests.factories import (
    DAG_EDGES,
    DAG_NODE_NAMES,
    SCALE_GROUP_TOTAL,
    SCALE_NODE_TOTAL,
    SCALE_PINNED_TOTAL,
    SCALE_RELATION_TOTAL,
    _dag_hierarchy_type,
    _dag_node_type,
    _dag_nodes,
    _group,
    _group_translation,
    _membership,
    _relation,
    _relation_translation,
    _version,
    atlas_active_version,
    atlas_dag,
    atlas_scale_fixture,
    atlas_two_versions,
    atlas_v1,
)
from apps.atlas.validation import validate_version

__all__ = [
    "atlas_active_version",
    "atlas_dag",
    "atlas_scale_fixture",
    "atlas_two_versions",
    "atlas_v1",
]

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Whole-graph description: the clone-faithfulness instrument.
# ---------------------------------------------------------------------------


def _relation_row(relation) -> tuple:
    """One relation as a pk-free tuple, transactions of the graph included."""
    return (
        relation.source.public_key,
        relation.relation_type.key,
        relation.target.public_key,
        relation.directed,
        relation.weight,
        relation.visible,
        relation.sort_order,
        tuple(relation.translations.order_by("locale").values_list("locale", "explanation")),
    )


def _group_row(group) -> tuple:
    """One group as a pk-free tuple, its per-locale copy included."""
    return (
        group.public_key,
        group.sort_order,
        group.active,
        tuple(group.translations.order_by("locale").values_list("locale", "label", "description")),
    )


def _graph_description(version: AtlasVersion) -> tuple:
    """The version's whole graph as a pk-free, order-free tuple.

    Primary keys are deliberately absent: a clone *must* differ from its source
    in every pk and in nothing else, so a description that named them could not
    state the property this file is about. Row order is absent for the same
    reason (ruling R11): the description is the set of rows, not their arrival
    order.
    """
    nodes = tuple(
        sorted(
            (
                node.public_key,
                node.node_type.key,
                node.canonical_model,
                str(node.canonical_translation_key),
                node.importance,
                node.visible,
                node.mobile_overview_priority,
                (node.pin_x, node.pin_y, node.pin_z),
                node.sort_order,
                tuple(
                    node.translations.order_by("locale").values_list(
                        "locale",
                        "label_override",
                        "summary_override",
                        "accessible_label_override",
                        "aliases",
                    )
                ),
            )
            for node in version.nodes.select_related("node_type")
        )
    )
    relations = tuple(
        sorted(
            _relation_row(relation)
            for relation in version.relations.select_related(
                "source", "target", "relation_type"
            )
        )
    )
    groups = tuple(
        sorted(_group_row(group) for group in version.groups.all())
    )
    memberships = tuple(
        sorted(
            AtlasGroupMembership.objects.filter(group__version=version)
            .select_related("group", "node")
            .values_list("group__public_key", "node__public_key", "sort_order")
        )
    )
    return (nodes, relations, groups, memberships)


def _memberships_by_group(version: AtlasVersion) -> dict[str, tuple[str, ...]]:
    """``{group key: member node keys}`` — the scale fixture's own shape, from the rows."""
    members: dict[str, list[str]] = {}
    rows = (
        AtlasGroupMembership.objects.filter(group__version=version)
        .select_related("group", "node")
        .order_by()
    )
    for row in rows:
        members.setdefault(row.group.public_key, []).append(row.node.public_key)
    return {key: tuple(sorted(value)) for key, value in members.items()}


@dataclass(frozen=True)
class _VersionSnapshot:
    """Everything about one version row and its graph that a service must not touch."""

    status: str
    layout: str
    layout_revision: int
    published_at: datetime | None
    updated_at: datetime
    graph: tuple


def _version_snapshot(version: AtlasVersion) -> _VersionSnapshot:
    """A comparable snapshot of a version row; ``layout`` as canonical JSON."""
    return _VersionSnapshot(
        status=version.status,
        layout=json.dumps(version.layout, sort_keys=True),
        layout_revision=version.layout_revision,
        published_at=version.published_at,
        updated_at=version.updated_at,
        graph=_graph_description(version),
    )


class _WriteRecorder:
    """Records ``(sql, params)`` for every version-table UPDATE a statement executes.

    ``CaptureQueriesContext`` keeps only the SQL text (Django's query log dropped
    its ``params`` key), so the parameter values — which row a write touched and
    what status it set — come from an ``execute_wrapper`` instead.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    def __call__(self, execute, sql, params, many, context):
        if isinstance(sql, str) and sql.startswith('UPDATE "atlas_version"'):
            self.calls.append((sql, list(params or ())))
        return execute(sql, params, many, context)


# ---------------------------------------------------------------------------
# The plan's Task 13 Step-1 snippets.
# ---------------------------------------------------------------------------


def test_clone_copies_topology_pins_and_keys(atlas_v1):
    clone = clone_version(atlas_v1.version.pk, "v2")
    assert clone.status == "draft"
    assert clone.pk != atlas_v1.version.pk
    assert {node.public_key for node in clone.nodes.all()} == {
        node.public_key for node in atlas_v1.version.nodes.all()
    }
    assert clone.nodes.get(public_key=atlas_v1.pinned_key).pin_x == atlas_v1.pinned_x


def test_activate_is_atomic_and_archives_the_previous_active(atlas_two_versions):
    draft = atlas_two_versions.draft
    activated = activate_version(draft.pk, expected_revision=version_revision(draft))
    assert activated.status == "active"
    assert AtlasVersion.objects.get(pk=atlas_two_versions.previous_active.pk).status == "archived"
    assert AtlasVersion.objects.filter(status="active").count() == 1
    assert activated.published_at is not None


def test_activation_rolls_back_completely_on_validation_failure(atlas_two_versions):
    atlas_two_versions.break_fa_projection()
    draft = atlas_two_versions.draft
    before = _version_snapshot(AtlasVersion.objects.get(pk=draft.pk))
    with pytest.raises(ValidationFailed) as exc:
        activate_version(draft.pk, expected_revision=version_revision(draft))
    assert "MISSING_LOCALE_PROJECTION" in {issue.code for issue in exc.value.issues}
    assert AtlasVersion.objects.get(pk=atlas_two_versions.previous_active.pk).status == "active"
    assert AtlasVersion.objects.get(pk=draft.pk).status == "draft"
    # Additive: a refused activation writes nothing at all — not even a timestamp.
    assert _version_snapshot(AtlasVersion.objects.get(pk=draft.pk)) == before


def test_activation_rejects_either_missing_locale_direction(atlas_two_versions):
    draft = atlas_two_versions.draft
    for break_it, restore in (
        (atlas_two_versions.break_fa_projection, atlas_two_versions.restore_fa_projection),
        (atlas_two_versions.break_en_projection, atlas_two_versions.restore_en_projection),
    ):
        break_it()
        with pytest.raises(ValidationFailed) as exc:
            activate_version(draft.pk, expected_revision=version_revision(draft))
        assert "MISSING_LOCALE_PROJECTION" in {issue.code for issue in exc.value.issues}
        assert AtlasVersion.objects.get(pk=draft.pk).status == "draft"
        restore()
    activate_version(draft.pk, expected_revision=version_revision(draft))


def test_stale_revision_and_already_active_are_rejected(atlas_two_versions):
    draft = atlas_two_versions.draft
    with pytest.raises(PreconditionFailed):
        activate_version(draft.pk, expected_revision="0-stale")
    activate_version(draft.pk, expected_revision=version_revision(draft))
    with pytest.raises(AlreadyActive):
        activate_version(draft.pk, expected_revision=version_revision(draft))


def test_active_version_cannot_be_edited_in_place(atlas_active_version):
    """The plan's snippet, corrected — its literal body could never raise.

    The plan writes ``clone_version(atlas_active_version.pk, "no-op") if False
    else None``: a conditional expression evaluates exactly one branch, so the
    service is never called and ``pytest.raises(ImmutableActive)`` fails with
    "DID NOT RAISE" (captured verbatim in ``.atlas-evidence/raw/``). Cloning an
    active version is *legal* — it is the first step of spec §8.1's publish flow,
    and the test below proves it — so the exception belongs to the in-place edit,
    which is what the test's name says: the one in-place mutation a service can
    perform is the layout action, and it refuses a non-draft row (spec §8.1:
    "Editing an active version is refused (409 IMMUTABLE_ACTIVE)"; an archived
    version is history and is refused for the same reason).
    """
    with pytest.raises(ImmutableActive):
        recompute_layout(atlas_active_version.version)
    archived = _version(status="archived", label="atlas-archived")
    with pytest.raises(ImmutableActive):
        recompute_layout(archived)
    assert not hasattr(services, "edit_active")  # editing an active version is not a service


# ---------------------------------------------------------------------------
# A clone carries the whole graph — keys included (ruling R9, spec §8.1).
# ---------------------------------------------------------------------------


def test_clone_carries_translations_groups_and_memberships(atlas_v1):
    """Every collection spec §8.1 names, including the rows a payload never shows.

    An invisible node, an invisible relation and a retired group are part of the
    topology: the clone must carry them with their flags, exactly as it carries
    the visible ones, or an edit waiting in a hidden node would silently vanish
    on the way to the next version.
    """
    version = atlas_v1.version
    hidden_node = atlas_v1.en_missing_node
    hidden_node.visible = False
    hidden_node.save(update_fields=["visible", "updated_at"])
    hidden_relation = atlas_v1.relations[1]
    hidden_relation.visible = False
    hidden_relation.save(update_fields=["visible"])

    group = _group(version=version, public_key="group-clone0001", sort_order=3)
    _group_translation(group, locale="en", label="Group EN", description="Description EN")
    _group_translation(group, locale="fa", label="گروه", description="توضیح")
    for index, node in enumerate((atlas_v1.identity, atlas_v1.areas[0], hidden_node)):
        _membership(group, node, sort_order=index)
    retired = _group(version=version, public_key="group-clone0002", sort_order=4, active=False)
    _group_translation(retired, locale="en", label="Retired EN")

    _relation_translation(atlas_v1.relations[0], locale="en", explanation="Why EN")
    _relation_translation(atlas_v1.relations[0], locale="fa", explanation="چرا")
    label = atlas_v1.override(atlas_v1.identity, locale="fa", label="این", aliases=["alias-fa"])
    label.accessible_label_override = "برچسب دسترسپذیر"
    label.save(update_fields=["accessible_label_override"])
    atlas_v1.override(atlas_v1.areas[2], locale="en", summary="Summary EN", aliases=["a", "b"])

    clone = clone_version(version.pk, "faithful")

    assert _graph_description(clone) == _graph_description(version)
    assert clone.nodes.count() == version.nodes.count()
    assert AtlasGroup.objects.filter(version=clone).count() == 2
    assert AtlasGroupMembership.objects.filter(group__version=clone).count() == 3
    assert clone.nodes.get(public_key=hidden_node.public_key).visible is False


def test_clone_of_the_scale_graph_copies_every_row(atlas_scale_fixture):
    """72 nodes / 136 relations / 6 groups / 36 memberships, counted on the clone."""
    source = atlas_scale_fixture.version_obj
    with CaptureQueriesContext(connection) as captured:
        clone = clone_version(source.pk, "scale-clone")

    assert clone.nodes.count() == SCALE_NODE_TOTAL
    assert clone.relations.count() == SCALE_RELATION_TOTAL
    assert AtlasGroup.objects.filter(version=clone).count() == SCALE_GROUP_TOTAL
    assert _memberships_by_group(clone) == atlas_scale_fixture.groups
    assert {node.public_key for node in clone.nodes.all()} == {
        node.public_key for node in source.nodes.all()
    }
    assert clone.nodes.filter(pin_x__isnull=False).count() == SCALE_PINNED_TOTAL
    assert clone.layout == source.layout
    # One write per collection rather than one per row: the copy of this graph
    # costs 18 statements, an N+1 variant of it 153 (measured — the bound is what
    # caught the per-relation translation queries this clone used to issue). The
    # number is deliberately loose: a shape assertion, not a frozen constant.
    assert len(captured.captured_queries) < 30, len(captured.captured_queries)


def test_clone_is_independent_of_the_source_row_insertion_order():
    """Ruling R11: the same logical graph, inserted in another order, clones alike.

    Two versions carry the same keys, the same four hierarchy edges and the same
    per-locale copy — inserted in opposite orders (nodes and edges alike). Their
    clones must describe the same graph; a clone that zipped two querysets or
    paired rows positionally would diverge here.
    """
    node_type = _dag_node_type()
    hierarchy = _dag_hierarchy_type()

    first = _version(status="draft", label="clone-order-a")
    first_nodes = _dag_nodes(first, node_type=node_type)
    for index, (source_name, target_name) in enumerate(DAG_EDGES):
        _relation(
            source=first_nodes[source_name],
            target=first_nodes[target_name],
            relation_type=hierarchy,
            version=first,
            sort_order=index,
        )

    second = _version(status="draft", label="clone-order-b")
    second_nodes = _dag_nodes(second, node_type=node_type, order=tuple(reversed(DAG_NODE_NAMES)))
    for source_name, target_name in reversed(DAG_EDGES):
        _relation(
            source=second_nodes[source_name],
            target=second_nodes[target_name],
            relation_type=hierarchy,
            version=second,
            sort_order=DAG_EDGES.index((source_name, target_name)),
        )

    assert _graph_description(clone_version(first.pk, "clone-a")) == _graph_description(
        clone_version(second.pk, "clone-b")
    )


def test_cloning_a_version_leaves_it_untouched(atlas_v1):
    """The source is read, never written: status, layout, revision and graph hold."""
    before = _version_snapshot(AtlasVersion.objects.get(pk=atlas_v1.version.pk))
    clone_version(atlas_v1.version.pk, "copy")
    assert _version_snapshot(AtlasVersion.objects.get(pk=atlas_v1.version.pk)) == before


def test_clone_copies_the_stored_row_state_and_records_its_source(atlas_two_versions):
    """Ledger ``13-note``'s sibling: the clone copies the *stored* row, not a view.

    A bare ``.update()`` writes the database without touching any Python object,
    so the caller's instance is exactly the stale view a clone must ignore. The
    premise is asserted first: the stored layout is the one the clone carries.
    """
    source = atlas_two_versions.draft
    stored_layout = {"stale-caller-view": [9.0, 9.0, 9.0]}
    AtlasVersion.objects.filter(pk=source.pk).update(layout=stored_layout, layout_revision=5)

    clone = clone_version(source, "v-next")

    assert clone.layout == stored_layout
    assert clone.layout != source.layout, "the clone must not read the caller's instance"
    assert clone.layout_revision == 5
    assert clone.created_from_id == source.pk
    assert clone.status == "draft"
    assert clone.published_at is None
    assert clone.label == "v-next"
    assert clone.pk != source.pk


def test_clone_of_the_active_version_is_publishable_without_a_recompute(atlas_active_version):
    """Spec §8.1's whole flow, end to end: Active vN ──clone──▶ Draft ──▶ Publish.

    The clone copies the stored layout, so an unedited clone is publishable
    without a manual layout action (the layout is a pure function of the graph,
    the groups and the pins — all copied verbatim), while a clone whose topology
    *changed* is still stopped by ``MISSING_LAYOUT``: the gate, not a recompute,
    decides.
    """
    clone = clone_version(atlas_active_version.version.pk, "v-next")
    assert validate_version(clone).blocking_codes() == []

    activated = activate_version(clone.pk, expected_revision=version_revision(clone))

    assert activated.status == "active"
    assert AtlasVersion.objects.get(pk=atlas_active_version.version.pk).status == "archived"
    assert AtlasVersion.objects.filter(status="active").count() == 1


# ---------------------------------------------------------------------------
# Publication: one transaction, a fresh row, the gate, archive then activate.
# ---------------------------------------------------------------------------


def test_activation_validates_the_stored_row_not_the_callers_view(atlas_two_versions):
    """Ledger ``13-note``: the gate reads a row re-read inside the transaction.

    ``validate_layout`` reads the *in-memory* instance's ``layout``, so a caller
    holding a pre-``.update()`` instance is holding a layout the database no
    longer has. The premise is asserted first — the caller's view still passes,
    the stored row does not — and the activation must follow the stored row.
    """
    draft = atlas_two_versions.draft
    AtlasVersion.objects.filter(pk=draft.pk).update(layout={})
    assert "MISSING_LAYOUT" not in validate_version(draft).blocking_codes()

    revision = version_revision(AtlasVersion.objects.get(pk=draft.pk))
    with pytest.raises(ValidationFailed) as exc:
        activate_version(draft, expected_revision=revision)

    assert "MISSING_LAYOUT" in exc.value.codes
    assert AtlasVersion.objects.get(pk=draft.pk).status == "draft"


def test_activation_locks_the_version_rows_before_it_writes(monkeypatch, atlas_two_versions):
    """``select_for_update`` on ``atlas_version``, once, before the first write.

    SQLite ignores the clause (``connection.features.has_select_for_update`` is
    ``False``), so what a test on this backend pins is the *request*: the service
    asks for the row lock, in the same transaction as the writes, and the locked
    read precedes them. On PostgreSQL the same call is the ``FOR UPDATE`` that
    makes two concurrent activations serialize — and the transaction is what
    makes it legal there at all.
    """
    lock_calls: list[str] = []
    original = QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        lock_calls.append(self.model.__name__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", spy)
    with CaptureQueriesContext(connection) as captured:
        activate_version(
            atlas_two_versions.draft.pk,
            expected_revision=version_revision(atlas_two_versions.draft),
        )

    assert lock_calls == ["AtlasVersion"]
    statements = [query["sql"] for query in captured.captured_queries]
    locked_read = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith("SELECT") and '"atlas_version"' in sql and '"status"' in sql
    )
    first_status_write = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith('UPDATE "atlas_version"') and '"status"' in sql
    )
    assert locked_read < first_status_write


def test_activation_archives_before_it_activates(atlas_two_versions):
    """The write order, observed: archive the active row, then activate the draft.

    The plan's ``test_activate_is_atomic_and_archives_the_previous_active``
    proves the outcome; this test pins the mechanism, so a future reordering
    cannot be rescued by an unrelated coincidence. The counterexample is exact:
    activating first needs two active rows at once, which the partial unique
    index refuses.
    """
    draft_pk = atlas_two_versions.draft.pk
    previous_pk = atlas_two_versions.previous_active.pk
    recorder = _WriteRecorder()
    with connection.execute_wrapper(recorder):
        activate_version(draft_pk, expected_revision=version_revision(atlas_two_versions.draft))

    writes = [params for sql, params in recorder.calls if '"status"' in sql]

    assert [params[0] for params in writes] == ["archived", "active"]
    assert [params[-1] for params in writes] == [previous_pk, draft_pk]


def test_activation_touches_no_other_version_rows(atlas_two_versions, atlas_v1):
    """Two rows move, and no third one does — status, stamps and graph alike.

    The plan's Step 3 says activation "never touches other versions' rows". Read
    literally that contradicts spec §8.3 step 4 and the plan's own assertion that
    the previous active becomes ``archived``, so the reading the spec and the
    test share is: only the rows the publication concerns are written, and a
    sibling draft's status, layout, revision, stamps and graph stay exactly as
    they were.
    """
    sibling = atlas_v1.version
    sibling_before = _version_snapshot(AtlasVersion.objects.get(pk=sibling.pk))
    previous_before = _version_snapshot(
        AtlasVersion.objects.get(pk=atlas_two_versions.previous_active.pk)
    )

    activate_version(
        atlas_two_versions.draft.pk,
        expected_revision=version_revision(atlas_two_versions.draft),
    )

    assert _version_snapshot(AtlasVersion.objects.get(pk=sibling.pk)) == sibling_before
    previous_after = _version_snapshot(
        AtlasVersion.objects.get(pk=atlas_two_versions.previous_active.pk)
    )
    assert previous_after.status == "archived"
    assert previous_after.graph == previous_before.graph
    assert previous_after.published_at == previous_before.published_at, "history is kept"
    assert previous_after.layout == previous_before.layout


def test_activation_reports_the_blockers_it_stopped_on(atlas_two_versions):
    """``ValidationFailed`` carries *which* blockers stopped it, and the wire report."""
    atlas_two_versions.break_fa_projection()
    draft = atlas_two_versions.draft
    with pytest.raises(ValidationFailed) as exc:
        activate_version(draft.pk, expected_revision=version_revision(draft))

    assert "MISSING_LOCALE_PROJECTION" in exc.value.codes
    assert exc.value.codes == tuple(sorted(set(exc.value.codes)))
    assert exc.value.issues == tuple(exc.value.report.blocking)
    assert {issue.code for issue in exc.value.issues} == set(exc.value.codes)
    wire = exc.value.report.to_dict()
    assert set(wire) == {"blocking", "warnings"}
    json.dumps(wire)


def test_activation_returns_the_refreshed_row(atlas_two_versions):
    """The returned instance is the row as the transaction left it, not what came in."""
    draft = atlas_two_versions.draft
    activated = activate_version(draft.pk, expected_revision=version_revision(draft))
    stored = AtlasVersion.objects.get(pk=draft.pk)
    for field in ("status", "published_at", "layout", "layout_revision", "updated_at"):
        assert getattr(activated, field) == getattr(stored, field), field
    assert activated.published_at is not None


def test_a_first_publication_has_no_previous_active_to_archive(atlas_dag):
    """With nothing active the archiving step is a no-op — warnings never block."""
    report = validate_version(atlas_dag.diamond)
    assert report.blocking_codes() == []
    assert report.warnings, "the premise: this fixture warns, so warnings do mean something"

    activated = activate_version(
        atlas_dag.diamond.pk, expected_revision=version_revision(atlas_dag.diamond)
    )

    assert activated.status == "active"
    assert AtlasVersion.objects.filter(status="active").count() == 1


def test_unknown_and_archived_targets_are_refused(atlas_two_versions):
    """A missing row is the ORM's ``DoesNotExist``; a non-draft row a precondition.

    Plan B maps the first to its ``404`` and the second to ``409``; an archived
    version is history and is never resurrected as the served topology without
    going through a clone (spec §8.1's lifecycle is ``draft → active →
    archived``).
    """
    with pytest.raises(AtlasVersion.DoesNotExist):
        activate_version(10**6, expected_revision="0-x")

    archived = _version(status="archived", label="atlas-history")
    with pytest.raises(PreconditionFailed):
        activate_version(archived.pk, expected_revision=version_revision(archived))
    assert AtlasVersion.objects.get(pk=archived.pk).status == "archived"


# ---------------------------------------------------------------------------
# recompute_layout: spec §8.2's layout action, draft-only.
# ---------------------------------------------------------------------------


def test_recompute_layout_stores_the_coordinates_the_gate_wants(atlas_two_versions):
    """The action that clears ``MISSING_LAYOUT``: a stored coordinate per visible node."""
    draft = atlas_two_versions.draft
    AtlasVersion.objects.filter(pk=draft.pk).update(layout={})
    assert "MISSING_LAYOUT" in validate_version(
        AtlasVersion.objects.get(pk=draft.pk)
    ).blocking_codes()

    revision = recompute_layout(draft)

    stored = AtlasVersion.objects.get(pk=draft.pk)
    assert revision == stored.layout_revision == draft.layout_revision + 1
    assert {node.public_key for node in stored.nodes.filter(visible=True)} <= set(stored.layout)
    assert "MISSING_LAYOUT" not in validate_version(stored).blocking_codes()
    assert all(len(coordinate) == 3 for coordinate in stored.layout.values())


def test_recompute_layout_preserves_pins(atlas_v1):
    """Spec §8.2's parenthetical: "Recompute layout (pins preserved)"."""
    pinned = atlas_v1.pinned_node
    before = (pinned.pin_x, pinned.pin_y, pinned.pin_z)
    recompute_layout(atlas_v1.version)
    stored = AtlasVersion.objects.get(pk=atlas_v1.version.pk).layout[atlas_v1.pinned_key]
    assert stored[:2] == [before[0], before[1]]
    node = AtlasNode.objects.get(pk=pinned.pk)
    assert (node.pin_x, node.pin_y, node.pin_z) == before


def test_recompute_layout_reads_the_row_fresh_not_the_instance_it_was_handed(atlas_two_versions):
    """Ledger ``13-note``, on the one service whose argument *is* an instance.

    The handed instance is made stale by a bare ``.update()``; the revision the
    action counts up from must be the stored one, so the stale view cannot make
    the counter go backwards.
    """
    draft = atlas_two_versions.draft
    AtlasVersion.objects.filter(pk=draft.pk).update(layout_revision=7)
    assert draft.layout_revision != 7, "premise: the handed instance is the stale view"

    assert recompute_layout(draft) == 8
    assert AtlasVersion.objects.get(pk=draft.pk).layout_revision == 8


def test_version_revision_is_the_row_identity_plus_its_stamp(atlas_two_versions):
    """``f"{pk}-{updated_at.isoformat()}"`` — the ``If-Match`` value Plan B reuses."""
    draft = atlas_two_versions.draft
    assert version_revision(draft) == f"{draft.pk}-{draft.updated_at.isoformat()}"
    assert version_revision(draft) == version_revision(AtlasVersion.objects.get(pk=draft.pk))

    activated = activate_version(draft.pk, expected_revision=version_revision(draft))

    assert version_revision(activated) != version_revision(draft), "a write makes the old one stale"
    assert version_revision(activated).startswith(f"{draft.pk}-")
