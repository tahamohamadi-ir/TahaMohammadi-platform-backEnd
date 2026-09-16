"""`AtlasNodeType` / `AtlasRelationType` — taxonomy fields, key grammar and lifecycle rules.

Builders are local to this module for now; plan Task 6 promotes the shared ones into
``apps/atlas/tests/factories.py``. The row-backed halves of the lifecycle rules —
``PROTECT``-on-delete and key immutability for a *real* node — become executable once
``AtlasNode`` ships (plan Task 6); here the immutability guard is proven against a stub
referencing model, and the node-backed cases are re-proven there.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.atlas import models as atlas_models
from apps.atlas.models import (
    CANONICAL_SOURCES,
    SELF_LOOP_POLICIES,
    SEMANTIC_ROLES,
    VISUAL_ROLES,
    AtlasNodeType,
    AtlasRelationType,
)

pytestmark = pytest.mark.django_db


def _node_type(key="research-area", **overrides):
    """Create an `AtlasNodeType` with spec-shaped defaults (`research-area`)."""
    row = {
        "label_en": "Research area",
        "label_fa": "دامنهٔ پژوهشی",
        "semantic_role": "area",
        "visual_role": "domain",
        "canonical_source": "research_topic",
    }
    row.update(overrides)
    return AtlasNodeType.objects.create(key=key, **row)


def _relation_type(key="uses", *, allowed_sources=(), allowed_targets=(), **overrides):
    """Create an `AtlasRelationType`; allowed type keys resolve to existing node types."""
    row = {
        "label_en": key,
        "label_fa": key,
        "inverse_label_en": f"inverse of {key}",
        "inverse_label_fa": key,
        "semantic_role": "utility",
    }
    row.update(overrides)
    relation_type = AtlasRelationType.objects.create(key=key, **row)
    if allowed_sources:
        relation_type.allowed_source_types.set(
            AtlasNodeType.objects.filter(key__in=allowed_sources)
        )
    if allowed_targets:
        relation_type.allowed_target_types.set(
            AtlasNodeType.objects.filter(key__in=allowed_targets)
        )
    return relation_type


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
    _default_manager = _StubReferencingRows()


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
