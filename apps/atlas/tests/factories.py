"""Shared ORM builders for the Atlas test suite (plan Task 6, ruling R4).

One home for the builders the plan's Task 6/7/8 test snippets call by name —
``_node_type``, ``_relation_type``, ``_node``. Task 8 extends this module with
the canonical ``seed_pairs`` fixtures and the small ``atlas_v1`` family; Task 18
adds the scale fixture. Nothing here is production code: a builder exists so a
test states only the fields the behaviour under test depends on.

Two rules these builders encode:

* a caller that does not care about identity still gets a valid, unique
  ``public_key`` (``new_node_key``), because the key is globally unique;
* a node's ``canonical_model`` and ``importance`` agree with its node type
  (spec §5: the type's ``canonical_source``/``default_importance`` are copied
  into the node at creation), so ``full_clean()`` on a factory-built node
  reports only the error under test.
"""

from __future__ import annotations

from apps.atlas.keys import new_node_key
from apps.atlas.models import AtlasNode, AtlasNodeType, AtlasRelationType, AtlasVersion

#: Label of the shared draft version a builder lands in when it is not given
#: ``version=``. Nodes, relations and groups of one topology must share their
#: version, so every builder without an explicit version must resolve to the
#: *same* row — hence ``get_or_create`` on this stable label rather than a fresh
#: version per call. A test that wants a second version creates one explicitly.
DEFAULT_VERSION_LABEL = "fixture"

NODE_TYPE_DEFAULTS = {
    "label_en": "Research area",
    "label_fa": "دامنهٔ پژوهشی",
    "semantic_role": "area",
    "visual_role": "domain",
    "canonical_source": "research_topic",
}


def _node_type(key="research-area", **overrides):
    """Create an `AtlasNodeType` with spec-shaped defaults (`research-area`)."""
    row = dict(NODE_TYPE_DEFAULTS)
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


def _version(status="draft", label=DEFAULT_VERSION_LABEL, **overrides):
    """Create an `AtlasVersion` row (the shared draft version by default)."""
    return AtlasVersion.objects.create(status=status, label=label, **overrides)


def _default_version():
    """The shared draft version used when a builder is not given one."""
    version, _created = AtlasVersion.objects.get_or_create(
        status="draft", label=DEFAULT_VERSION_LABEL
    )
    return version


def _default_node_type():
    """The shared `research-area` node type every plain ``_node()`` hangs off."""
    node_type, _created = AtlasNodeType.objects.get_or_create(
        key="research-area", defaults=dict(NODE_TYPE_DEFAULTS)
    )
    return node_type


def _node(version=None, *, node_type=None, public_key=None, **overrides):
    """Create an `AtlasNode`; defaults to the shared version and node type.

    ``canonical_model`` and ``importance`` default to the node type's
    ``canonical_source``/``default_importance`` (spec §5 copy-at-creation), so
    ``full_clean()`` passes unless the test changes something itself.
    """
    version = _default_version() if version is None else version
    node_type = _default_node_type() if node_type is None else node_type
    row = {
        "public_key": new_node_key(node_type.key) if public_key is None else public_key,
        "canonical_model": node_type.canonical_source,
        "importance": node_type.default_importance,
    }
    row.update(overrides)
    return AtlasNode.objects.create(version=version, node_type=node_type, **row)
