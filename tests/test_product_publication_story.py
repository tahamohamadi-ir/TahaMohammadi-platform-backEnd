"""Tests for PU-04-publication: attach nullable localized story to publication,
validate admin storyId, and expose sanitized public detail.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-publication.md
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
from apps.content.models import LifecycleStatus, Publication


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
        settings={"body": "<p>Sample story paragraph.</p>"},
        enabled=True,
    )
    return page


def test_publication_model_has_story_field():
    """Verify Publication model defines nullable ForeignKey to CompositionPage."""
    field_names = [f.name for f in Publication._meta.get_fields()]
    assert "story" in field_names, "Publication must define a 'story' relation"
    field = Publication._meta.get_field("story")
    assert field.is_relation
    assert field.null is True
    assert field.blank is True
    assert field.related_model == CompositionPage


def test_admin_content_schema_exposes_publication_story_id(admin_api_client):
    """Admin schema must expose storyId for publication."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    entities = res.json()["entities"]
    assert "publication" in entities
    pub_fields = {f["key"]: f for f in entities["publication"]["fields"]}
    assert "storyId" in pub_fields
    assert pub_fields["storyId"]["type"] == "number"


def test_admin_crud_publication_story_id(admin_api_client):
    """Setting storyId via admin content CRUD updates the relation with optimistic locking."""
    story = _create_story("pub-story-1", "fa", "داستان مقاله ۱")

    # 1. Create with storyId
    create_payload = {
        "locale": "fa",
        "slug": "test-pub-with-story",
        "title": "مقاله با استوری",
        "status": "draft",
        "fields": {
            "storyId": story.pk,
            "abstract": "چکیده نمونه",
        },
    }
    create_res = admin_api_client.post(
        "/api/v1/admin/content/publication",
        create_payload,
        content_type="application/json",
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    pub_id = created_data["id"]
    assert created_data["fields"]["storyId"] == story.pk

    pub = Publication.objects.get(pk=pub_id)
    assert pub.story == story

    # 2. Update to clear storyId
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/publication/{pub_id}",
        {"fields": {"storyId": None}},
        content_type="application/json",
        HTTP_IF_MATCH=created_data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["storyId"] is None

    pub.refresh_from_db()
    assert pub.story is None


def test_admin_publication_story_validation(admin_api_client):
    """storyId must reject non-existent PK, non-story kinds, and locale mismatches."""
    # Non-existent ID
    res = admin_api_client.post(
        "/api/v1/admin/content/publication",
        {
            "locale": "fa",
            "slug": "pub-invalid-story-id",
            "title": "مقاله نامعتبر",
            "status": "draft",
            "fields": {"storyId": 999999},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"

    # Landing composition (not a story)
    landing = CompositionPage.objects.create(
        key="landing-page-1",
        kind=CompositionPage.KIND_LANDING,
        locale="fa",
        title="صفحه اصلی",
    )
    res = admin_api_client.post(
        "/api/v1/admin/content/publication",
        {
            "locale": "fa",
            "slug": "pub-landing-story",
            "title": "مقاله با صفحه لندینگ",
            "status": "draft",
            "fields": {"storyId": landing.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "storyId must reference a story composition" in res.json()["message"]

    # Locale mismatch
    en_story = _create_story("en-story-1", "en", "English Story")
    res = admin_api_client.post(
        "/api/v1/admin/content/publication",
        {
            "locale": "fa",
            "slug": "pub-locale-mismatch",
            "title": "عدم تطابق زبان",
            "status": "draft",
            "fields": {"storyId": en_story.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "storyId locale must match" in res.json()["message"]


def test_public_publication_detail_exposes_story():
    """Published publication with published story returns sanitized story document."""
    client = Client()
    story = _create_story("pub-story-pub", "fa", "استوری منتشرشده", status="published")
    pub = Publication.objects.create(
        locale="fa",
        slug="published-pub-with-story",
        title="مقاله پژوهشی با استوری",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=story,
        abstract="خلاصه مقاله پژوهشی",
    )

    res = client.get(f"/api/publications/fa/{pub.slug}")
    assert res.status_code == 200
    data = res.json()
    assert "story" in data
    assert data["story"] is not None
    assert data["story"]["locale"] == "fa"
    assert data["story"]["title"] == "استوری منتشرشده"
    assert len(data["story"]["sections"]) == 1
    blocks = data["story"]["sections"][0]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["blockType"] == "text"
    assert "<p>Sample story paragraph.</p>" in blocks[0]["settings"]["body"]


def test_public_publication_detail_draft_isolation():
    """Story is omitted (returns None) if story is draft or story locale mismatches."""
    client = Client()
    draft_story = _create_story("pub-story-draft", "fa", "استوری پیش‌نویس", status="draft")
    pub = Publication.objects.create(
        locale="fa",
        slug="pub-with-draft-story",
        title="مقاله با استوری پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
        abstract="خلاصه مقاله پژوهشی",
    )

    res = client.get(f"/api/publications/fa/{pub.slug}")
    assert res.status_code == 200
    data = res.json()
    assert "story" in data
    assert data["story"] is None
