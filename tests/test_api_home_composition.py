"""Public home composition API tests (BK-01) - fail-closed, published+visible only."""

from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    HomeModule,
    HomeModuleKey,
    LifecycleStatus,
    Locale,
)

PUBLIC_KEYS = {"revision", "modules"}
MODULE_KEYS = {"key", "order"}
FORBIDDEN_MODULE_KEYS = {
    "status",
    "locale",
    "visible",
    "selection_mode",
    "provenance_note",
    "created_at",
    "updated_at",
}


@pytest.fixture
def api_client():
    return Client()


def _published_visible_row(locale, key, order):
    return HomeModule.objects.create(
        locale=locale,
        key=key,
        visible=True,
        order=order,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )


@pytest.fixture
def composition(db):
    """fa: 3 visible rows inserted out of order + leak candidates; en: 1 row."""
    _published_visible_row(Locale.FA, HomeModuleKey.JOURNEY, 2)
    _published_visible_row(Locale.FA, HomeModuleKey.IDENTITY, 1)
    _published_visible_row(Locale.FA, HomeModuleKey.CTA, 3)
    HomeModule.objects.create(
        locale=Locale.FA,
        key=HomeModuleKey.GRAPH,
        visible=True,
        order=0,
        status=LifecycleStatus.DRAFT,
    )
    HomeModule.objects.create(
        locale=Locale.FA,
        key=HomeModuleKey.PREVIEWS,
        visible=False,
        order=4,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )
    _published_visible_row(Locale.EN, HomeModuleKey.PROJECTS, 1)


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


def test_fa_visible_rows_returned_in_order(api_client, composition):
    data = assert_json(api_client.get("/api/home-composition/fa"), 200)
    assert set(data) == PUBLIC_KEYS
    assert [m["key"] for m in data["modules"]] == ["identity", "journey", "cta"]
    assert [m["order"] for m in data["modules"]] == [1, 2, 3]
    assert all(set(m) == MODULE_KEYS for m in data["modules"])
    assert FORBIDDEN_MODULE_KEYS.isdisjoint(data["modules"][0])


def test_revision_is_iso_timestamp_of_latest_row_update(api_client, composition):
    data = assert_json(api_client.get("/api/home-composition/fa"), 200)
    parsed = datetime.fromisoformat(data["revision"])
    assert parsed <= timezone.now()


def test_en_isolation_fa_rows_never_leak(api_client, composition):
    data = assert_json(api_client.get("/api/home-composition/en"), 200)
    assert [m["key"] for m in data["modules"]] == ["projects"]


def test_draft_row_never_leaks_anonymously(api_client, composition):
    data = assert_json(api_client.get("/api/home-composition/fa"), 200)
    assert all(m["key"] != "graph" for m in data["modules"])


def test_invisible_row_never_leaks(api_client, composition):
    data = assert_json(api_client.get("/api/home-composition/fa"), 200)
    assert all(m["key"] != "previews" for m in data["modules"])


def test_empty_locale_returns_404(api_client, db):
    data = assert_json(api_client.get("/api/home-composition/fa"), 404)
    assert "detail" in data


def test_locale_with_only_invisible_rows_returns_404(api_client, db):
    """Fail-closed: a locale whose rows are all invisible has no projection."""
    HomeModule.objects.create(
        locale=Locale.EN,
        key=HomeModuleKey.CTA,
        visible=False,
        order=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )
    data = assert_json(api_client.get("/api/home-composition/en"), 404)
    assert "detail" in data


def test_invalid_locale_returns_404(api_client, composition):
    response = api_client.get("/api/home-composition/xx")
    assert response.status_code == 404
    assert response["content-type"].startswith("application/json")
    assert "detail" in response.json()
    assert "Traceback" not in response.text


def test_duplicate_locale_key_rejected(db):
    """unique(locale, key): the per-locale-row interpretation is enforced."""
    from django.db import transaction
    from django.db.utils import IntegrityError

    _published_visible_row(Locale.FA, HomeModuleKey.IDENTITY, 1)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            HomeModule.objects.create(
                locale=Locale.FA,
                key=HomeModuleKey.IDENTITY,
                visible=True,
                order=9,
                status=LifecycleStatus.PUBLISHED,
                published_at=timezone.now() - timedelta(days=1),
            )
