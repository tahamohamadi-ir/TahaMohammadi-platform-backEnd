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
from apps.atlas.tests.factories import _node, _node_type


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
