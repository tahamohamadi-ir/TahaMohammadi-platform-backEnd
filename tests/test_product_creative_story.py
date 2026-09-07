"""Tests for PU-04-creative: attach nullable localized story to creative-work,
validate admin storyId, and expose sanitized public detail.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-creative.md
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.composition.models import (
    CompositionBlock,
    CompositionPage,
    CompositionSection,
)
from apps.content.models import CreativeWork, LifecycleStatus


@pytest.fixture(autouse=True)
def db_access(db):
    pass


@pytest.fixture
def admin_api_client(db, admin_user):
    totp_device = TOTPDevice.objects.create(
        user=admin_user, name="default", confirmed=True
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session["django_otp_device_id"] = totp_device.persistent_id
    session.save()
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def _create_story(key: str, locale: str, title: str, status: str = "published") -> CompositionPage:
    page = CompositionPage.objects.create(
        key=key,
        kind=CompositionPage.KIND_STORY,
        locale=locale,
        title=title,
        status=status,
        published_at=timezone.now() if status == "published" else None,
    )
    sec = CompositionSection.objects.create(page=page, position=0, layout="1col", enabled=True)
    CompositionBlock.objects.create(
        section=sec,
        position=0,
        block_type="text",
        settings={"body": "<p>Creative work exhibition notes and gallery context.</p>"},
        enabled=True,
    )
    return page


def test_creative_work_model_has_story_field():
    """Verify CreativeWork model defines nullable ForeignKey to CompositionPage."""
    field_names = [f.name for f in CreativeWork._meta.get_fields()]
    assert "story" in field_names, "CreativeWork must define a 'story' relation"
    field = CreativeWork._meta.get_field("story")
    assert field.is_relation
    assert field.null is True
    assert field.blank is True
    assert field.related_model == CompositionPage


def test_admin_content_schema_exposes_creative_work_story_id(admin_api_client):
    """Admin schema must expose storyId for creative-work."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    entities = res.json()["entities"]
    assert "creative-work" in entities
    cw_fields = {f["key"]: f for f in entities["creative-work"]["fields"]}
    assert "storyId" in cw_fields
    assert cw_fields["storyId"]["type"] == "number"


def test_admin_crud_creative_work_story_id(admin_api_client):
    """Setting storyId via admin content CRUD updates the relation with optimistic locking."""
    story = _create_story("creative-story-1", "fa", "استوری اثر خلاقانه ۱")

    # 1. Create with storyId
    create_payload = {
        "locale": "fa",
        "slug": "test-creative-with-story",
        "title": "اثر با استوری",
        "status": "draft",
        "fields": {
            "storyId": story.pk,
            "description": "توضیحات اثر خلاقانه",
        },
    }
    create_res = admin_api_client.post(
        "/api/v1/admin/content/creative-work",
        create_payload,
        content_type="application/json",
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    cw_id = created_data["id"]
    assert created_data["fields"]["storyId"] == story.pk

    cw = CreativeWork.objects.get(pk=cw_id)
    assert cw.story == story

    # 2. Update to clear storyId
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/creative-work/{cw_id}",
        {"fields": {"storyId": None}},
        content_type="application/json",
        HTTP_IF_MATCH=created_data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["storyId"] is None

    cw.refresh_from_db()
    assert cw.story is None


def test_admin_creative_work_story_validation(admin_api_client):
    """storyId must reject non-existent PK, non-story kinds, and locale mismatches."""
    # Non-existent ID
    res = admin_api_client.post(
        "/api/v1/admin/content/creative-work",
        {
            "locale": "fa",
            "slug": "cw-invalid-story-id",
            "title": "اثر نامعتبر",
            "status": "draft",
            "fields": {"storyId": 999999},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"

    # Landing composition (not a story)
    landing = CompositionPage.objects.create(
        key="landing-page-cw",
        kind=CompositionPage.KIND_LANDING,
        locale="fa",
        title="صفحه اصلی",
    )
    res = admin_api_client.post(
        "/api/v1/admin/content/creative-work",
        {
            "locale": "fa",
            "slug": "cw-landing-story",
            "title": "اثر با صفحه لندینگ",
            "status": "draft",
            "fields": {"storyId": landing.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "storyId must reference a story composition" in res.json()["message"]

    # Locale mismatch
    en_story = _create_story("en-cw-story-1", "en", "English Creative Story")
    res = admin_api_client.post(
        "/api/v1/admin/content/creative-work",
        {
            "locale": "fa",
            "slug": "cw-locale-mismatch",
            "title": "عدم تطابق زبان اثر",
            "status": "draft",
            "fields": {"storyId": en_story.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "storyId locale must match" in res.json()["message"]


def test_public_creative_work_detail_exposes_story():
    """Published creative work with published story returns sanitized story document."""
    client = Client()
    story = _create_story("cw-story-pub", "fa", "استوری اثر منتشرشده", status="published")
    cw = CreativeWork.objects.create(
        locale="fa",
        slug="published-cw-with-story",
        title="اثر با استوری",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=story,
        description="توضیحات اثر",
    )

    res = client.get(f"/api/creative/fa/{cw.slug}")
    assert res.status_code == 200
    data = res.json()
    assert "story" in data
    assert data["story"] is not None
    assert data["story"]["locale"] == "fa"
    assert data["story"]["title"] == "استوری اثر منتشرشده"
    assert len(data["story"]["sections"]) == 1
    blocks = data["story"]["sections"][0]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["blockType"] == "text"
    expected_text = "<p>Creative work exhibition notes and gallery context.</p>"
    assert expected_text in blocks[0]["settings"]["body"]


def test_public_creative_work_detail_draft_isolation():
    """Story is omitted (returns None) if story is draft or story locale mismatches."""
    client = Client()
    draft_story = _create_story("cw-story-draft", "fa", "استوری پیش‌نویس اثر", status="draft")
    cw = CreativeWork.objects.create(
        locale="fa",
        slug="cw-with-draft-story",
        title="اثر با استوری پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
        description="توضیحات اثر",
    )

    res = client.get(f"/api/creative/fa/{cw.slug}")
    assert res.status_code == 200
    data = res.json()
    assert "story" in data
    assert data["story"] is None
