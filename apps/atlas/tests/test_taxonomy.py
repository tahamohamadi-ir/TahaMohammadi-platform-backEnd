"""`AtlasNodeType` / `AtlasRelationType` — taxonomy fields, key grammar and lifecycle rules.

Builders live in ``apps/atlas/tests/factories.py`` (plan Task 6, ruling R4).

The row-backed halves of the lifecycle rules — ``PROTECT``-on-delete and key
immutability for a *real* node — were only provable against a stub while
``AtlasNode`` did not exist. They are re-proven here with real rows now that it
does: ``test_node_type_key_is_immutable_once_used`` and
``test_in_use_taxonomy_cannot_be_deleted`` close Task 5's carry-forward. The two
monkeypatched tests stay, because they prove the guard branch on its own.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from apps.atlas import models as atlas_models
from apps.atlas.models import (
    CANONICAL_SOURCES,
    SELF_LOOP_POLICIES,
    SEMANTIC_ROLES,
    VISUAL_ROLES,
    AtlasNode,
    AtlasNodeType,
    AtlasRelationType,
    _referencing_model,
)
from apps.atlas.tests.factories import _node, _node_type, _relation_type

pytestmark = pytest.mark.django_db


def test_node_type_defaults_match_spec():
    node_type = AtlasNodeType.objects.create(
        key="identity",
        label_en="Identity",
        label_fa="هویت",
        semantic_role="anchor",
        visual_role="anchor",
    )
    assert AtlasNodeType._meta.db_table == "atlas_node_type"
    assert node_type.canonical_source == "none"
    assert node_type.default_importance == 50
    assert node_type.allow_as_root is False
    assert node_type.allow_children is True
    assert node_type.filter_visible is True
    assert node_type.active is True
    assert node_type.sort_order == 0
    assert str(node_type) == "Identity (identity)"


def test_node_type_key_is_unique():
    first = _node_type("project", canonical_source="project")
    with pytest.raises(IntegrityError), transaction.atomic():
        _node_type("project", canonical_source="project")
    assert AtlasNodeType.objects.filter(key=first.key).count() == 1


def test_key_never_contains_tilde():
    node_type = AtlasNodeType(
        key="a~b",
        label_en="A",
        label_fa="آ",
        semantic_role="area",
        visual_role="domain",
    )
    with pytest.raises(ValidationError) as exc:
        node_type.full_clean()
    assert "key" in exc.value.message_dict


def test_key_grammar_is_lowercase_digits_hyphens():
    for bad in ("Research-Area", "research area", "research_area", "research/area"):
        node_type = AtlasNodeType(
            key=bad,
            label_en="X",
            label_fa="ایکس",
            semantic_role="area",
            visual_role="domain",
        )
        with pytest.raises(ValidationError):
            node_type.full_clean()
    valid = _node_type("research-area")
    valid.full_clean()  # the canonical lowercase-hyphenated shape passes


def test_unused_node_type_key_can_be_renamed():
    node_type = _node_type("research-area")
    node_type.key = "research-domain"
    node_type.save()
    assert AtlasNodeType.objects.get(pk=node_type.pk).key == "research-domain"


def test_unused_taxonomy_can_be_deleted():
    node_type = _node_type("project", canonical_source="project")
    relation_type = _relation_type("uses")
    node_type.delete()
    relation_type.delete()
    assert not AtlasNodeType.objects.filter(pk=node_type.pk).exists()
    assert not AtlasRelationType.objects.filter(pk=relation_type.pk).exists()


class _StubReferencingRows:
    """Stands in for a referencing manager: every ``filter`` matches one row."""

    def filter(self, **kwargs):
        return self

    def exists(self):
        return True


class _StubReferencingModel:
    """Mirrors the real model shape the guard reads: both manager slots exist."""

    _default_manager = _StubReferencingRows()
    _base_manager = _StubReferencingRows()


class _StubFilteringRows:
    """A *default* manager whose filter would hide an in-use row."""

    def filter(self, **kwargs):
        return self

    def exists(self):
        return False


class _StubBaseRows(_StubFilteringRows):
    """The base manager, which still sees the row the filter hides."""

    def exists(self):
        return True


class _StubModelWithFilteringDefaultManager:
    _default_manager = _StubFilteringRows()
    _base_manager = _StubBaseRows()


def test_node_type_key_is_immutable_once_a_node_uses_it(monkeypatch):
    node_type = _node_type("research-area")
    monkeypatch.setattr(
        atlas_models, "_referencing_model", lambda *args, **kwargs: _StubReferencingModel
    )
    node_type.key = "research-area-renamed"
    with pytest.raises(ValidationError) as exc:
        node_type.save()
    assert "key" in exc.value.message_dict
    assert AtlasNodeType.objects.get(pk=node_type.pk).key == "research-area"


def test_relation_type_key_is_immutable_once_a_relation_uses_it(monkeypatch):
    relation_type = _relation_type("uses")
    monkeypatch.setattr(
        atlas_models, "_referencing_model", lambda *args, **kwargs: _StubReferencingModel
    )
    relation_type.key = "uses-renamed"
    with pytest.raises(ValidationError) as exc:
        relation_type.save()
    assert "key" in exc.value.message_dict
    assert AtlasRelationType.objects.get(pk=relation_type.pk).key == "uses"


def test_relation_type_records_its_policies():
    for node_key, semantic_role, visual_role, canonical_source in (
        ("project", "record", "record", "project"),
        ("method", "utility", "fine", "method"),
        ("technology", "utility", "fine", "technology"),
    ):
        _node_type(
            node_key,
            semantic_role=semantic_role,
            visual_role=visual_role,
            canonical_source=canonical_source,
        )
    relation_type = _relation_type(
        "uses", allowed_sources=["project"], allowed_targets=["method", "technology"]
    )
    assert relation_type.directed_default is True
    assert relation_type.hierarchy_role is False
    assert relation_type.self_loop_policy == "forbid"
    assert relation_type.overridable_direction is False
    assert list(relation_type.allowed_source_types.values_list("key", flat=True)) == ["project"]
    assert set(relation_type.allowed_target_types.values_list("key", flat=True)) == {
        "method",
        "technology",
    }


def test_relation_type_defaults_match_spec():
    relation_type = _relation_type(
        "related-to", label_en="related to", label_fa="مرتبط با", semantic_role="utility"
    )
    assert AtlasRelationType._meta.db_table == "atlas_relation_type"
    assert relation_type.directed_default is True
    assert relation_type.overridable_direction is False
    assert relation_type.hierarchy_role is False
    assert relation_type.default_weight == 1
    assert relation_type.visual_priority == 50
    assert relation_type.self_loop_policy == "forbid"
    assert relation_type.active is True
    assert relation_type.sort_order == 0
    assert not relation_type.allowed_source_types.exists()
    assert not relation_type.allowed_target_types.exists()
    assert str(relation_type) == "related to (related-to)"


def test_choice_vocabularies_match_spec():
    assert [value for value, _ in SEMANTIC_ROLES.choices] == [
        "anchor",
        "area",
        "record",
        "utility",
    ]
    assert [value for value, _ in VISUAL_ROLES.choices] == [
        "anchor",
        "domain",
        "record",
        "fine",
    ]
    assert [value for value, _ in SELF_LOOP_POLICIES.choices] == ["forbid", "allow"]
    assert [value for value, _ in CANONICAL_SOURCES.choices] == [
        "research_topic",
        "project",
        "publication",
        "method",
        "technology",
        "profile",
        "none",
    ]
    assert AtlasNodeType._meta.get_field("semantic_role").choices == SEMANTIC_ROLES.choices
    assert AtlasNodeType._meta.get_field("canonical_source").default == "none"
    assert AtlasRelationType._meta.get_field("self_loop_policy").default == "forbid"


def test_node_type_key_is_immutable_once_used():
    """Task 5 carry-forward, real rows: an existing node locks its type's key.

    Task 5 could only prove this with a monkeypatched stub (``AtlasNode`` did not
    exist yet); here the guard resolves the real foreign key. The error is keyed
    by field and carries the node-type message verbatim (spec §5.3).
    """
    node_type = _node_type("research-area")
    _node(node_type=node_type)
    node_type.key = "research-area-renamed"
    with pytest.raises(ValidationError) as exc:
        node_type.save()
    assert exc.value.message_dict == {
        "key": ["A node type key is immutable once a node uses it."]
    }
    assert AtlasNodeType.objects.get(pk=node_type.pk).key == "research-area"


def test_in_use_taxonomy_cannot_be_deleted():
    """Task 5 carry-forward, real rows: ``PROTECT`` blocks deleting a used type.

    The node FK is the only thing carrying this protection (no custom code), so
    the test fails if the FK ever loses ``on_delete=models.PROTECT``.
    """
    node_type = _node_type("project", canonical_source="project")
    node = _node(node_type=node_type)
    with pytest.raises(ProtectedError), transaction.atomic():
        node_type.delete()
    assert AtlasNodeType.objects.filter(pk=node_type.pk).exists()
    assert AtlasNode.objects.filter(pk=node.pk).exists()


def test_retiring_an_in_use_type_is_allowed():
    """Deletion is blocked; retirement is the sanctioned escape hatch (spec §5.3)."""
    node_type = _node_type("research-area")
    _node(node_type=node_type)
    node_type.active = False
    node_type.save()
    assert AtlasNodeType.objects.get(pk=node_type.pk).active is False


def test_the_in_use_guard_is_armed_for_real_node_rows():
    """The guard resolves the referencing model by name and *swallows* ``LookupError``.

    A typo in that name — or a model that never lands — would therefore disable
    the immutability and deletion protections silently and forever, so the
    resolution itself is asserted here. The relation half arrives with
    ``AtlasRelation`` and is asserted by plan Task 7 (recorded as a carry-forward).
    """
    assert _referencing_model("atlas", "AtlasNode") is not None


def test_in_use_detection_reads_through_the_base_manager(monkeypatch):
    """A filtering default manager must not hide in-use rows from the guard.

    ``_base_manager`` (not ``_default_manager``) is read, so a model that later
    gains a filtered default manager cannot silently disable the rules.
    """
    node_type = _node_type("research-area")
    stub = _StubModelWithFilteringDefaultManager
    monkeypatch.setattr(atlas_models, "_referencing_model", lambda *args, **kwargs: stub)
    node_type.key = "research-area-renamed"
    with pytest.raises(ValidationError):
        node_type.save()
