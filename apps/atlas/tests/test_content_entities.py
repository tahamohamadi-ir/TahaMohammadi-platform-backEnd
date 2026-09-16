"""`Method` / `Technology` entities — public gate, identity and per-locale uniqueness."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.content.models import Method, Technology

pytestmark = pytest.mark.django_db


def test_method_public_gate_and_identity():
    paired = uuid4()
    draft = Method.objects.create(
        locale="en",
        slug="draft-method",
        title="Draft",
        status="draft",
        translation_key=paired,
    )
    live = Method.objects.create(
        locale="en",
        slug="live-method",
        title="Live",
        status="published",
        published_at=timezone.now(),
        translation_key=paired,
    )
    future = Method.objects.create(
        locale="fa",
        slug="future-method",
        title="Future",
        status="published",
        published_at=timezone.now() + timedelta(days=1),
        translation_key=paired,
    )

    visible = set(Method.objects.public().values_list("pk", flat=True))
    assert live.pk in visible
    assert draft.pk not in visible
    assert future.pk not in visible
    assert Method._meta.db_table == "content_method"
    with pytest.raises(IntegrityError):
        Method.objects.create(
            locale="en",
            slug="live-method",
            title="Duplicate",
            translation_key=uuid4(),
        )


def test_technology_public_gate_and_identity():
    paired = uuid4()
    draft = Technology.objects.create(
        locale="en",
        slug="draft-technology",
        title="Draft",
        status="draft",
        translation_key=paired,
    )
    live = Technology.objects.create(
        locale="en",
        slug="live-technology",
        title="Live",
        status="published",
        published_at=timezone.now(),
        translation_key=paired,
    )
    future = Technology.objects.create(
        locale="fa",
        slug="future-technology",
        title="Future",
        status="published",
        published_at=timezone.now() + timedelta(days=1),
        translation_key=paired,
    )

    visible = set(Technology.objects.public().values_list("pk", flat=True))
    assert live.pk in visible
    assert draft.pk not in visible
    assert future.pk not in visible
    assert Technology._meta.db_table == "content_technology"
    with pytest.raises(IntegrityError):
        Technology.objects.create(
            locale="en",
            slug="live-technology",
            title="Duplicate",
            translation_key=uuid4(),
        )


def test_technology_and_method_are_distinct_models():
    assert Method is not Technology
    assert Method._meta.label != Technology._meta.label
    assert Method._meta.db_table != Technology._meta.db_table
    # Field objects compare by ``creation_counter``, so a set difference of the field
    # objects of two distinct models is never a singleton; compare names instead.
    difference = {field.name for field in Method._meta.get_fields()} - {
        field.name for field in Technology._meta.get_fields()
    }
    assert difference == set()
