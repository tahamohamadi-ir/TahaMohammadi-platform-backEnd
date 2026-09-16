"""`AtlasVersion`, `AtlasNode` and `AtlasNodeTranslation` — plan Task 6.

Builders live in ``apps/atlas/tests/factories.py`` (ruling R4). The taxonomy
lifecycle rules these models activate — the immutable-key guard and
deletion protection against a *real* node — are re-proven in
``apps/atlas/tests/test_taxonomy.py`` (Task 5's carry-forward).
"""

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.atlas.keys import new_node_key
from apps.atlas.models import AtlasNode, AtlasNodeTranslation, AtlasVersion
from apps.atlas.tests.factories import (
    DEFAULT_VERSION_LABEL,
    _default_version,
    _node,
    _node_type,
    _version,
)


@pytest.mark.django_db
def test_only_one_active_version_can_exist():
    AtlasVersion.objects.create(status="active", label="v1")
    with pytest.raises(IntegrityError):
        AtlasVersion.objects.create(status="active", label="v2")


@pytest.mark.django_db
def test_a_canonical_record_appears_once_per_version():
    node_type = _node_type("publication", canonical_source="publication")
    version = AtlasVersion.objects.create(status="draft", label="v1")
    key = uuid4()
    AtlasNode.objects.create(
        version=version,
        public_key=new_node_key("publication"),
        node_type=node_type,
        canonical_model="publication",
        canonical_translation_key=key,
    )
    with pytest.raises(IntegrityError):
        AtlasNode.objects.create(
            version=version,
            public_key=new_node_key("publication"),
            node_type=node_type,
            canonical_model="publication",
            canonical_translation_key=key,
        )


@pytest.mark.django_db
def test_localized_override_is_optional_and_unique_per_locale():
    node = _node()
    AtlasNodeTranslation.objects.create(node=node, locale="en", label_override="Custom EN title")
    # `transaction.atomic()` keeps the failed INSERT's broken transaction from
    # poisoning the queries that follow (plan snippet omitted it; Task 5 fixed
    # the same shape in test_taxonomy.py).
    with pytest.raises(IntegrityError), transaction.atomic():
        AtlasNodeTranslation.objects.create(
            node=node, locale="en", label_override="Second EN title"
        )
    blank = AtlasNodeTranslation.objects.create(node=node, locale="fa")
    assert blank.label_override == "" and blank.aliases == []


@pytest.mark.django_db
def test_node_public_key_is_globally_unique_across_versions():
    first, second = _node(), _node(version=AtlasVersion.objects.create(status="draft", label="v2"))
    second.public_key = first.public_key
    with pytest.raises(IntegrityError):
        second.save()


@pytest.mark.django_db
def test_mobile_overview_priority_defaults_and_rejects_unknown_values():
    node = _node()                                   # no explicit mobile_overview_priority
    assert node.mobile_overview_priority == "auto"
    node.mobile_overview_priority = "sometimes"
    with pytest.raises(ValidationError):
        node.full_clean()


@pytest.mark.django_db
def test_node_key_never_contains_a_tilde():
    """'~' separates relation keys, so no node key may carry one (spec §5.3).

    ``clean()`` is called directly as well as through ``full_clean()``: the
    ``SlugField`` validator rejects a tilde on its own, so only the direct call
    proves *this* rule — the one Plan C's URL codec relies on to read a ``~`` in
    a URL key as "relation".
    """
    node = _node()
    node.public_key = f"{node.node_type.key}~1a2b3c4d"
    with pytest.raises(ValidationError) as exc:
        node.clean()
    assert "public_key" in exc.value.message_dict
    assert "~" in exc.value.message_dict["public_key"][0]
    with pytest.raises(ValidationError):
        node.full_clean()


@pytest.mark.django_db
def test_node_public_key_must_match_the_key_grammar():
    """The grammar half of the key rule (spec §5.3) — `~` has its own test above.

    ``clean()`` is called directly: the ``SlugField`` validator is a *different*
    grammar (it allows uppercase, which a public key must not carry), so only this
    call proves the Atlas key grammar itself.
    """
    node = _node()
    for bad in ("UPPER-case", "x", "-leading-hyphen", "a" * 81):
        node.public_key = bad
        with pytest.raises(ValidationError) as exc:
            node.clean()
        assert "public_key" in exc.value.message_dict, bad
        assert "grammar" in exc.value.message_dict["public_key"][0], bad
    node.public_key = "research-area-1a2b3c4d"  # control: a legal key raises nothing
    node.clean()


@pytest.mark.django_db
def test_canonical_model_must_equal_its_node_types_canonical_source():
    """spec §5.3: the node's `canonical_model` equals its type's `canonical_source`."""
    node_type = _node_type("publication", canonical_source="publication")
    node = _node(node_type=node_type, canonical_model="research_topic")
    with pytest.raises(ValidationError) as exc:
        node.clean()
    assert "canonical_model" in exc.value.message_dict
    assert "publication" in exc.value.message_dict["canonical_model"][0]
    with pytest.raises(ValidationError):
        node.full_clean()


@pytest.mark.django_db
def test_save_copies_the_node_types_canonical_source_onto_the_node():
    """spec §5.3: `canonical_model` is copied from `node_type.canonical_source` at save.

    The copy is what makes ``clean()``'s equality rule satisfiable for a caller that
    only knows the node's *type*: ``none`` is not a value a node of a canonical type
    may hold. ``_node()`` copies it in the builder, so the two save paths are
    exercised directly: the field default (``none``) and an explicit blank.
    """
    node_type = _node_type("publication", canonical_source="publication")
    node = AtlasNode.objects.create(
        version=_version(), public_key="publication-1a2b3c4d", node_type=node_type
    )
    assert node.canonical_model == "publication"  # the 'none' default was replaced
    node.full_clean()  # so the equality rule now holds on the saved instance
    blank = _node(node_type=node_type, canonical_model="")
    assert blank.canonical_model == "publication"


@pytest.mark.django_db
def test_pin_x_requires_pin_y():
    """spec §5.3: `pin_x`/`pin_y` must both be present when either is set."""
    node = _node(pin_x=1.0)
    with pytest.raises(ValidationError) as exc:
        node.clean()
    assert "pin_x" in exc.value.message_dict
    assert "together" in exc.value.message_dict["pin_x"][0]

    mirrored = _node(pin_y=2.0)  # the mirror image: pin_y alone is equally invalid
    with pytest.raises(ValidationError) as exc:
        mirrored.clean()
    assert "pin_x" in exc.value.message_dict

    node.pin_y = 2.0
    node.clean()  # control: both coordinates set → allowed


@pytest.mark.django_db
def test_pin_z_requires_pin_x_and_pin_y():
    """spec §5.3: a lone `pin_z` pins nothing."""
    node = _node(pin_z=3.0)
    with pytest.raises(ValidationError) as exc:
        node.clean()
    assert "pin_z" in exc.value.message_dict
    assert "pin_x" in exc.value.message_dict["pin_z"][0]
    node.pin_x, node.pin_y = 1.0, 2.0
    node.clean()  # control: a full pin is allowed


@pytest.mark.django_db
def test_aliases_are_a_list_of_non_empty_strings_of_at_most_120_characters():
    """spec §5.3:271 — "list of non-empty strings, each ≤ 120 characters".

    A 120-character alias is inside the limit; one character more is not. The long
    case uses Persian because the limit counts characters, not bytes.
    """
    translation = AtlasNodeTranslation(node=_node(), locale="en", aliases=["بینایی ماشین"])
    translation.full_clean()  # control: a valid list is accepted

    translation.aliases = ["vision", "x" * 120]
    translation.clean()  # the boundary itself is legal

    translation.aliases = ["vision", ""]
    with pytest.raises(ValidationError) as exc:
        translation.clean()
    empty_message = exc.value.message_dict["aliases"][0]
    assert "''" in empty_message  # the offending value is named
    assert "non-empty" in empty_message

    long_alias = "ب" * 121
    translation.aliases = [long_alias]
    with pytest.raises(ValidationError) as exc:
        translation.clean()
    long_message = exc.value.message_dict["aliases"][0]
    assert long_alias in long_message
    assert "121" in long_message and "120" in long_message


@pytest.mark.django_db
def test_aliases_must_be_a_list_of_strings():
    """`aliases` is a JSONField, so any JSON value can land in it; only a list is an alias list."""
    translation = AtlasNodeTranslation(node=_node(), locale="en", aliases="vision")
    with pytest.raises(ValidationError) as exc:
        translation.clean()
    assert "list" in exc.value.message_dict["aliases"][0]

    translation.aliases = ["vision", 7]
    with pytest.raises(ValidationError) as exc:
        translation.clean()
    assert "7" in exc.value.message_dict["aliases"][0]


@pytest.mark.django_db
def test_version_builder_and_the_shared_default_version_do_not_collide():
    """Regression (Task 6 review): ``_version()`` must not shadow the shared fixture row.

    Once ``_default_version()`` has created the shared ``("draft", DEFAULT_VERSION_LABEL)``
    row, a ``_version()`` carrying that same identity makes the *next* version-less
    builder's ``get_or_create`` raise ``MultipleObjectsReturned`` — a database error
    raised far from its cause. The three-call combination is the regression's subject.
    """
    shared = _default_version()  # the shared row exists first
    created = _version()  # a second, dedicated version
    assert created.label != DEFAULT_VERSION_LABEL  # a distinct row, not the shared one

    node = _node()  # used to raise MultipleObjectsReturned here
    assert node.version_id == shared.pk
    assert AtlasVersion.objects.filter(status="draft", label=DEFAULT_VERSION_LABEL).count() == 1
    assert _default_version().pk == shared.pk  # still exactly one shared row
