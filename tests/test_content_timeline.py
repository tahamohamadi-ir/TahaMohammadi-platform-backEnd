"""Timeline/journey record model tests (BK-02) - locale isolation, gate, validation."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.content.models import (
    LifecycleStatus,
    Locale,
    Profile,
    TimelineRecord,
    TimelineRecordType,
)


def past() -> timezone.datetime:
    return timezone.now() - timedelta(days=1)


def future() -> timezone.datetime:
    return timezone.now() + timedelta(days=1)


def make_record(**overrides) -> TimelineRecord:
    defaults = {
        "locale": Locale.EN,
        "type": TimelineRecordType.MILESTONE,
        "label": "Shipped P14",
        "order": 1,
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return TimelineRecord.objects.create(**defaults)


@pytest.mark.django_db
def test_crud_basics_via_orm():
    record = make_record()
    fetched = TimelineRecord.objects.get(pk=record.pk)
    assert fetched.label == "Shipped P14"
    assert fetched.type == TimelineRecordType.MILESTONE
    assert fetched.weight == 0
    assert str(fetched) == "Shipped P14 (en)"

    fetched.label = "Renamed"
    fetched.save()
    assert TimelineRecord.objects.get(pk=fetched.pk).label == "Renamed"

    fetched.delete()
    assert TimelineRecord.objects.filter(pk=record.pk).count() == 0


@pytest.mark.django_db
def test_defaults_order_one_weight_zero():
    record = TimelineRecord.objects.create(
        locale=Locale.FA, type=TimelineRecordType.EXPERIENCE, label="X"
    )
    assert record.order == 1
    assert record.weight == 0
    assert record.detail_url == ""
    assert record.attach is None


@pytest.mark.django_db
def test_for_locale_isolates_locales():
    fa = make_record(locale=Locale.FA, label="FA row")
    en = make_record(locale=Locale.EN, label="EN row")
    assert list(TimelineRecord.objects.for_locale(Locale.FA)) == [fa]
    assert list(TimelineRecord.objects.for_locale(Locale.EN)) == [en]


@pytest.mark.django_db
def test_for_locale_orders_stably_by_order_then_id():
    third = make_record(order=2)
    first = make_record(order=1)
    second = make_record(order=1)
    assert list(TimelineRecord.objects.for_locale(Locale.EN)) == [first, second, third]


@pytest.mark.django_db
def test_published_for_locale_gates_lifecycle():
    make_record(status=LifecycleStatus.DRAFT)
    make_record(status=LifecycleStatus.REVIEW, published_at=past())
    make_record(status=LifecycleStatus.ARCHIVED, published_at=past())
    make_record(published_at=future())
    published = make_record()
    assert list(TimelineRecord.objects.published_for_locale(Locale.EN)) == [published]


@pytest.mark.django_db
def test_draft_never_leaks_to_published_for_locale():
    make_record(locale=Locale.FA, label="draft", status=LifecycleStatus.DRAFT)
    make_record(locale=Locale.FA, label="public")
    keys = [r.label for r in TimelineRecord.objects.published_for_locale(Locale.FA)]
    assert keys == ["public"]


@pytest.mark.django_db
def test_locale_isolation_on_published_gate():
    make_record(locale=Locale.FA, label="fa public")
    assert list(TimelineRecord.objects.published_for_locale(Locale.EN)) == []


@pytest.mark.django_db
def test_attach_profile_and_set_null_on_delete():
    profile = Profile.objects.create(
        locale=Locale.EN, slug="cv", title="CV", status=LifecycleStatus.DRAFT
    )
    record = make_record(attach=profile)
    assert record.attach == profile
    assert list(profile.timeline_records.all()) == [record]

    profile.delete()
    record.refresh_from_db()
    assert record.attach is None
    assert TimelineRecord.objects.filter(pk=record.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("", True),
        ("/fa/x", True),
        ("https://example.com/a", True),
        ("http://example.com", True),
        ("javascript:alert(1)", False),
        ("ftp://example.com/x", False),
        ("example.com/page", False),
        ("//evil.com", False),
    ],
)
def test_detail_url_validator_contract(value, valid):
    record = make_record(detail_url=value)
    if valid:
        record.full_clean()
    else:
        with pytest.raises(ValidationError):
            record.full_clean()


@pytest.mark.django_db
def test_order_below_one_rejected_by_check_constraint():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_record(order=0)
    assert TimelineRecord.objects.exists() is False
