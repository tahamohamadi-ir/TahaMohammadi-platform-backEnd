"""`AtlasRelation`, `AtlasRelationTranslation`, `AtlasGroup` and membership — plan Task 7.

Builders live in ``apps/atlas/tests/factories.py`` (ruling R4). The relation-type
half of the taxonomy lifecycle rules — key immutability and ``PROTECT``-on-delete
against a *real* relation row — is re-proven in ``apps/atlas/tests/test_taxonomy.py``,
the file whose rules those are (Task 6's precedent for the node-type half).

Two review carry-forwards are closed here instead of in ``keys.py``:

* a group key must carry no ``~``, exactly as a node key must not (``AtlasGroup.clean()``
  mirrors ``AtlasNode.clean()``), so a ``~`` in a URL key unambiguously means "relation";
* a composed relation key is validated **per segment** — ``is_valid_public_key`` caps a
  single key at 80 characters, which is *shorter* than a legal composed key
  (80 + 1 + 64 + 1 + 80), so calling it on a whole relation key would reject valid ones.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.atlas.keys import is_valid_public_key, new_group_key
from apps.atlas.models import (
    AtlasGroup,
    AtlasGroupMembership,
    AtlasGroupTranslation,
    AtlasRelation,
    AtlasRelationTranslation,
    AtlasVersion,
    is_valid_relation_public_key,
)
from apps.atlas.tests.factories import (
    _group,
    _group_translation,
    _node,
    _relation,
    _relation_translation,
    _relation_type,
)

pytestmark = pytest.mark.django_db


def test_relation_duplicate_and_mirror_rules():
    source, target = _node(public_key="research-area-1a2b3c4d"), _node(
        public_key="project-2b3c4d5e"
    )
    relation_type = _relation_type("related-to", directed_default=False, overridable_direction=True)
    AtlasRelation.objects.create(
        version=source.version, source=source, target=target,
        relation_type=relation_type, directed=False,
    )
    # `transaction.atomic()` keeps the failed INSERT's broken transaction from
    # poisoning the queries that follow (plan snippet omitted it; Tasks 5 and 6
    # fixed the same shape in test_taxonomy.py / test_models_version_node.py).
    with pytest.raises(IntegrityError), transaction.atomic():  # exact duplicate
        AtlasRelation.objects.create(
            version=source.version, source=source, target=target,
            relation_type=relation_type, directed=False,
        )
    with pytest.raises(ValidationError):  # mirrored undirected pair, caught by clean()
        AtlasRelation.objects.create(
            version=source.version, source=target, target=source,
            relation_type=relation_type, directed=False,
        ).full_clean()


def test_self_loop_only_where_the_type_permits_it():
    node, forbidden = _node(), _relation_type("related-to")
    with pytest.raises(ValidationError):
        AtlasRelation.objects.create(
            version=node.version, source=node, target=node, relation_type=forbidden
        ).full_clean()
    allowed = _relation_type("related-to-self", self_loop_policy="allow")
    relation = AtlasRelation.objects.create(
        version=node.version, source=node, target=node, relation_type=allowed
    )
    relation.full_clean()
    assert relation.public_key == f"{node.public_key}~related-to-self~{node.public_key}"


def test_endpoints_must_share_the_version():
    other = AtlasVersion.objects.create(status="draft", label="v2")
    source = _node()
    with pytest.raises(ValidationError):
        AtlasRelation.objects.create(
            version=source.version,
            source=source,
            target=_node(version=other),
            relation_type=_relation_type("uses"),
        ).full_clean()


def test_group_membership_is_many_to_many_and_localized():
    group = AtlasGroup.objects.create(version=_node().version, public_key=new_group_key())
    for locale, label in (("en", "Vision & language"), ("fa", "زبان و بینایی")):
        AtlasGroupTranslation.objects.create(group=group, locale=locale, label=label)
    node_a, node_b = _node(), _node()
    AtlasGroupMembership.objects.create(group=group, node=node_a)
    AtlasGroupMembership.objects.create(group=group, node=node_b)
    assert group.members.count() == 2
    with pytest.raises(IntegrityError), transaction.atomic():
        AtlasGroupMembership.objects.create(group=group, node=node_a)


def test_relation_public_key_is_composed_and_never_stored():
    source, target = _node(public_key="identity-2b3c4d5e"), _node(
        public_key="research-area-1a2b3c4d"
    )
    relation = _relation(
        source, target, relation_type=_relation_type("research-focus"), directed=True
    )
    assert "public_key" not in {field.name for field in AtlasRelation._meta.get_fields()}
    assert relation.public_key == "identity-2b3c4d5e~research-focus~research-area-1a2b3c4d"


def test_undirected_relation_key_does_not_depend_on_insertion_order():
    source, target = _node(public_key="a-11111111"), _node(public_key="b-22222222")
    relation_type = _relation_type("related-to", directed_default=False)
    forward = _relation(source, target, relation_type=relation_type, directed=False)
    reverse = _relation(target, source, relation_type=relation_type, directed=False)
    assert forward.public_key == reverse.public_key == "a-11111111~related-to~b-22222222"


def test_composed_relation_key_is_validated_per_segment():
    """The 80-character single-key cap is *smaller* than a legal composed key.

    Both endpoints carry the longest legal node key, so the composed key is 172
    characters: ``is_valid_public_key`` — the single-key grammar — rejects it, which
    is exactly why relation keys must be validated segment by segment instead.
    """
    long_source = "research-area-" + "a" * 66  # 80 characters: the longest legal node key
    long_target = "research-area-" + "b" * 66
    source, target = _node(public_key=long_source), _node(public_key=long_target)
    relation = _relation(source, target, relation_type=_relation_type("related-to"))
    composed = relation.public_key
    assert len(composed) == 172
    assert not is_valid_public_key(composed)
    assert is_valid_relation_public_key(composed)
    relation.full_clean()  # the model accepts its own composed key


def test_composed_relation_key_rejects_a_bad_segment():
    source, target = _node(public_key="research-area-1a2b3c4d"), _node(
        public_key="project-2b3c4d5e"
    )
    assert is_valid_relation_public_key(
        f"{source.public_key}~related-to~{target.public_key}"
    )
    for bad in (
        source.public_key,                                            # not composed at all
        f"{source.public_key}~related-to",                            # two segments
        f"{source.public_key}~related-to~{target.public_key}~x",      # four segments
        f"{source.public_key}~Related-To~{target.public_key}",        # type: uppercase
        f"{source.public_key}~related_to~{target.public_key}",        # type: underscore
        f"{source.public_key}~~{target.public_key}",                  # type: empty
        f"UPPER-case~related-to~{target.public_key}",                 # source grammar
        f"{source.public_key}~related-to~{'x' * 81}",                 # target too long
    ):
        assert not is_valid_relation_public_key(bad), bad


def test_relation_clean_reports_an_invalid_endpoint_segment():
    source, target = _node(), _node()
    relation = _relation(source, target, relation_type=_relation_type("uses"))
    relation.target.public_key = "UPPER-case"  # in memory only: `AtlasNode.clean()` rejects it
    with pytest.raises(ValidationError) as exc:
        relation.clean()
    assert "target" in exc.value.message_dict


def test_membership_endpoints_must_share_the_version():
    other = AtlasVersion.objects.create(status="draft", label="v2")
    group = _group(version=_node().version)
    # Unsaved on purpose: `create()` inserts before `full_clean()` runs, so only an
    # in-memory instance proves the rule *and* leaves no row behind.
    membership = AtlasGroupMembership(group=group, node=_node(version=other))
    with pytest.raises(ValidationError) as exc:
        membership.full_clean()
    assert "node" in exc.value.message_dict
    assert group.members.count() == 0


def test_group_key_never_contains_a_tilde():
    """'~' separates relation keys, so no group key may carry one (spec §5.3).

    ``clean()`` is called directly as well as through ``full_clean()``: the
    ``SlugField`` validator rejects a tilde on its own, so only the direct call
    proves *this* rule — the one Plan C's URL codec relies on to read a ``~`` in a
    URL key as "relation".
    """
    group = _group()
    group.public_key = "group~1a2b3c4d"
    with pytest.raises(ValidationError) as exc:
        group.clean()
    assert "public_key" in exc.value.message_dict
    assert "~" in exc.value.message_dict["public_key"][0]
    with pytest.raises(ValidationError):
        group.full_clean()


def test_group_public_key_is_unique_across_versions():
    """Globally unique, like a node key (spec §5.3: "immutable, unique").

    Two versions may not share a group key: the constraint is on ``public_key``
    alone, exactly as ``AtlasNode``'s is. This is also why Plan A's ``clone_version``
    (Task 13) cannot copy keys verbatim onto a second live version — reported as a
    cross-task finding, not re-litigated here.
    """
    group = _group(public_key="group-1a2b3c4d")
    with pytest.raises(IntegrityError), transaction.atomic():
        _group(version=group.version, public_key=group.public_key)
    other_version = AtlasVersion.objects.create(status="draft", label="v2")
    with pytest.raises(IntegrityError), transaction.atomic():
        _group(version=other_version, public_key=group.public_key)
    assert AtlasGroup.objects.filter(public_key=group.public_key).count() == 1


def test_generated_group_keys_pass_the_model_rule():
    group = _group()  # `new_group_key()`: tilde-free by construction
    assert group.public_key.startswith("group-")
    assert "~" not in group.public_key
    group.full_clean()


def test_db_tables_and_locale_uniqueness():
    assert AtlasRelation._meta.db_table == "atlas_relation"
    assert AtlasRelationTranslation._meta.db_table == "atlas_relation_translation"
    assert AtlasGroup._meta.db_table == "atlas_group"
    assert AtlasGroupTranslation._meta.db_table == "atlas_group_translation"
    assert AtlasGroupMembership._meta.db_table == "atlas_group_membership"

    relation = _relation(relation_type=_relation_type("uses"))
    _relation_translation(relation, locale="en", explanation="Explained")
    with pytest.raises(IntegrityError), transaction.atomic():
        _relation_translation(relation, locale="en", explanation="Second explanation")

    group = _group()
    _group_translation(group, locale="en", label="Vision & language")
    with pytest.raises(IntegrityError), transaction.atomic():
        _group_translation(group, locale="en", label="Second label")
    blank = _group_translation(group, locale="fa")
    assert blank.label == "" and blank.description == ""
