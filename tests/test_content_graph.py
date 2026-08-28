"""Graph storage model tests (BK-04) - version gate, uniqueness, edge/related validation."""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.content.models import (
    Article,
    GraphEdge,
    GraphGroup,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    GraphVersionStatus,
    LifecycleStatus,
    Locale,
    TopicTag,
)

_pair_counter = {"n": 0}


def past() -> timezone.datetime:
    return timezone.now() - timedelta(days=1)


def future() -> timezone.datetime:
    return timezone.now() + timedelta(days=1)


def make_version(**overrides) -> GraphVersion:
    defaults = {"locale": Locale.EN, "status": GraphVersionStatus.DRAFT}
    defaults.update(overrides)
    return GraphVersion.objects.create(**defaults)


def make_node(version: GraphVersion, node_id="self", **overrides) -> GraphNode:
    defaults = {
        "version": version,
        "node_id": node_id,
        "label": node_id.replace("-", " ").title(),
        "type": "concept",
        "color_role": "brand",
        "icon_role": "dot",
    }
    defaults.update(overrides)
    return GraphNode.objects.create(**defaults)


def make_edge(
    version: GraphVersion, source: GraphNode, target: GraphNode, **overrides
) -> GraphEdge:
    defaults = {
        "version": version,
        "source": source,
        "target": target,
        "relation_type": "relates-to",
    }
    defaults.update(overrides)
    return GraphEdge.objects.create(**defaults)


def make_article(**overrides) -> Article:
    _pair_counter["n"] += 1
    n = _pair_counter["n"]
    defaults = {
        "locale": Locale.EN,
        "slug": f"graph-fixture-{n}",
        "title": f"Graph fixture {n}",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return Article.objects.create(**defaults)


def make_related(node: GraphNode, obj) -> GraphNodeRelated:
    return GraphNodeRelated.objects.create(
        node=node,
        content_type=ContentType.objects.get_for_model(type(obj)),
        object_id=obj.pk,
    )


@pytest.mark.django_db
class TestGraphVersion:
    def test_defaults_and_str(self):
        version = make_version()
        assert version.status == GraphVersionStatus.DRAFT
        assert version.created_at is not None
        assert version.updated_at is not None
        assert str(version) == f"Graph en #{version.pk} (draft)"

    def test_one_active_version_per_locale_enforced_by_db(self):
        make_version(locale=Locale.FA, status=GraphVersionStatus.ACTIVE)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_version(locale=Locale.FA, status=GraphVersionStatus.ACTIVE)

    def test_active_constraint_is_locale_scoped(self):
        active = make_version(locale=Locale.EN, status=GraphVersionStatus.ACTIVE)
        other = make_version(locale=Locale.FA, status=GraphVersionStatus.ACTIVE)
        assert other.pk != active.pk

    def test_multiple_drafts_allowed_per_locale(self):
        first = make_version(locale=Locale.FA)
        second = make_version(locale=Locale.FA)
        assert first.status == second.status == GraphVersionStatus.DRAFT

    def test_demoting_active_version_frees_the_slot(self):
        old = make_version(locale=Locale.EN, status=GraphVersionStatus.ACTIVE)
        old.status = GraphVersionStatus.DRAFT
        old.save()
        new = make_version(locale=Locale.EN, status=GraphVersionStatus.ACTIVE)
        assert GraphVersion.objects.latest_active(Locale.EN) == new

    def test_ordering_newest_first_within_locale(self):
        older = make_version(locale=Locale.FA)
        newer = make_version(locale=Locale.FA)
        assert list(GraphVersion.objects.filter(locale=Locale.FA)) == [newer, older]

    def test_latest_active_fail_closed_when_none(self):
        make_version(locale=Locale.EN)
        make_version(locale=Locale.EN, status=GraphVersionStatus.DRAFT)
        assert GraphVersion.objects.latest_active(Locale.EN) is None

    def test_latest_active_isolates_locales(self):
        make_version(locale=Locale.FA, status=GraphVersionStatus.ACTIVE)
        assert GraphVersion.objects.latest_active(Locale.EN) is None

    def test_latest_active_picks_newest_active(self):
        superseded = make_version(locale=Locale.EN, status=GraphVersionStatus.ACTIVE)
        superseded.status = GraphVersionStatus.DRAFT
        superseded.save()
        current = make_version(locale=Locale.EN, status=GraphVersionStatus.ACTIVE)
        assert GraphVersion.objects.latest_active(Locale.EN) == current

    def test_delete_cascades_to_nodes_edges_groups(self):
        version = make_version()
        node = make_node(version)
        other = make_node(version, node_id="other")
        make_edge(version, node, other)
        group = GraphGroup.objects.create(version=version, label="Cluster", color_role="brand")
        node.group = group
        node.save()
        version.delete()
        assert GraphNode.objects.count() == 0
        assert GraphEdge.objects.count() == 0
        assert GraphGroup.objects.count() == 0


@pytest.mark.django_db
class TestGraphNode:
    def test_defaults_and_str(self):
        version = make_version()
        node = make_node(version, node_id="research-fit")
        assert node.weight == 0
        assert node.summary == ""
        assert node.accessible_label == ""
        assert node.pos_x is None and node.pos_y is None and node.pos_z is None
        assert node.group is None
        assert str(node) == "Research Fit (research-fit)"

    def test_position_coordinates_persist(self):
        node = make_node(make_version(), pos_x=1.5, pos_y=-2.0, pos_z=3.25)
        node.refresh_from_db()
        assert (node.pos_x, node.pos_y, node.pos_z) == (1.5, -2.0, 3.25)

    def test_node_id_unique_within_version(self):
        version = make_version()
        make_node(version, node_id="self")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_node(version, node_id="self")

    def test_same_node_id_allowed_in_other_version(self):
        make_node(make_version(locale=Locale.EN), node_id="self")
        twin = make_node(make_version(locale=Locale.FA), node_id="self")
        assert twin.node_id == "self"

    def test_ordering_by_version_then_node_id(self):
        version = make_version()
        mid = make_node(version, node_id="work")
        first = make_node(version, node_id="identity")
        assert list(GraphNode.objects.filter(version=version)) == [first, mid]

    def test_group_set_null_on_delete(self):
        version = make_version()
        group = GraphGroup.objects.create(version=version, label="Cluster", color_role="brand")
        node = make_node(version, group=group)
        group.delete()
        node.refresh_from_db()
        assert node.group is None
        assert str(group) == f"Cluster ({version.pk})"


@pytest.mark.django_db
class TestGraphNodeRelated:
    def test_published_article_reference_passes(self):
        node = make_node(make_version())
        row = make_related(node, make_article())
        row.full_clean()
        assert row.content_object.title.startswith("Graph fixture")

    def test_draft_article_reference_rejected(self):
        node = make_node(make_version())
        row = make_related(node, make_article(status=LifecycleStatus.DRAFT))
        with pytest.raises(ValidationError):
            row.full_clean()

    def test_published_but_not_yet_public_article_rejected(self):
        node = make_node(make_version())
        row = make_related(
            node, make_article(status=LifecycleStatus.PUBLISHED, published_at=future())
        )
        with pytest.raises(ValidationError):
            row.full_clean()

    def test_dangling_reference_allowed(self):
        node = make_node(make_version())
        row = GraphNodeRelated.objects.create(
            node=node,
            content_type=ContentType.objects.get_for_model(Article),
            object_id=999_999,
        )
        row.full_clean()
        assert row.content_object is None

    def test_model_without_public_gate_rejected(self):
        node = make_node(make_version())
        tag = TopicTag.objects.create(name="Graph", slug="graph", locale=Locale.EN)
        row = make_related(node, tag)
        with pytest.raises(ValidationError):
            row.full_clean()

    def test_unique_target_per_node(self):
        node = make_node(make_version())
        article = make_article()
        make_related(node, article)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_related(node, article)

    def test_version_clean_surfaces_unpublished_related(self):
        version = make_version()
        node = make_node(version)
        make_related(node, make_article(status=LifecycleStatus.DRAFT))
        with pytest.raises(ValidationError):
            version.full_clean()

    def test_node_delete_cascades_related_rows(self):
        node = make_node(make_version())
        make_related(node, make_article())
        node.delete()
        assert GraphNodeRelated.objects.count() == 0


@pytest.mark.django_db
class TestGraphEdge:
    def test_defaults_and_str(self):
        version = make_version()
        edge = make_edge(version, make_node(version), make_node(version, node_id="work"))
        assert edge.directed is True
        assert edge.weight == 0
        assert edge.explanation == ""
        assert str(edge) == f"relates-to: {edge.source_id}->{edge.target_id}"

    def test_duplicate_directed_pair_same_relation_rejected(self):
        version = make_version()
        a = make_node(version)
        b = make_node(version, node_id="work")
        make_edge(version, a, b)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_edge(version, a, b)

    def test_same_pair_different_relation_type_allowed(self):
        version = make_version()
        a = make_node(version)
        b = make_node(version, node_id="work")
        make_edge(version, a, b, relation_type="relates-to")
        mentor = make_edge(version, a, b, relation_type="mentors")
        assert mentor.pk is not None

    def test_reverse_directed_pair_allowed(self):
        version = make_version()
        a = make_node(version)
        b = make_node(version, node_id="work")
        make_edge(version, a, b)
        back = make_edge(version, b, a)
        back.full_clean()
        assert back.pk is not None

    def test_undirected_mirror_pair_rejected_by_clean(self):
        version = make_version()
        a = make_node(version)
        b = make_node(version, node_id="work")
        make_edge(version, a, b, directed=False)
        mirror = GraphEdge(version=version, source=b, target=a, directed=False)
        with pytest.raises(ValidationError):
            mirror.full_clean()

    def test_undirected_mirror_check_ignores_other_relation_type(self):
        version = make_version()
        a = make_node(version)
        b = make_node(version, node_id="work")
        make_edge(version, a, b, directed=False, relation_type="relates-to")
        mirror = GraphEdge(
            version=version, source=b, target=a, directed=False, relation_type="mentors"
        )
        mirror.full_clean()

    def test_cross_version_endpoints_rejected_by_clean(self):
        v1 = make_version()
        v2 = make_version()
        a = make_node(v2)
        b = make_node(v2, node_id="work")
        edge = GraphEdge(version=v1, source=a, target=b)
        with pytest.raises(ValidationError):
            edge.full_clean()

    def test_version_clean_surfaces_bad_edge(self):
        v1 = make_version()
        v2 = make_version()
        a = make_node(v2)
        b = make_node(v2, node_id="work")
        GraphEdge.objects.create(version=v1, source=a, target=b)
        with pytest.raises(ValidationError):
            v1.full_clean()

    def test_duplicate_exact_pair_rejected_by_version_clean(self):
        """Swapped-undirected duplicates are a clean()-level rule (BK-04):
        the DB stays deliberately unconstrained; GraphEdge.clean() raises
        ValidationError, surfaced version-wide via GraphVersion.clean()."""
        version = make_version()
        a = make_node(version)
        b = make_node(version, node_id="work")
        GraphEdge.objects.create(
            version=version, source=b, target=a, directed=False
        )
        duplicate = GraphEdge(
            version=version, source=a, target=b, directed=False
        )
        with pytest.raises(ValidationError):
            duplicate.full_clean()
