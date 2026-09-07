"""Tests for PU-06-talk: extend existing talk detail and generic editor map with story/metadata.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I01/I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-talk.md
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.composition.models import (
    CompositionBlock,
    CompositionPage,
    CompositionSection,
)
from apps.content.models import (
    Article,
    LifecycleStatus,
    Talk,
)


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
        settings={"body": "<p>Talk story body.</p>"},
        enabled=True,
    )
    return page


def test_talk_model_has_story_field():
    """Talk model must define a nullable ForeignKey to composition story."""
    fields = {f.name: f for f in Talk._meta.get_fields()}
    assert "story" in fields
    assert fields["story"].is_relation
    assert fields["story"].related_model == CompositionPage


def test_admin_content_schema_exposes_talk_story_id(admin_api_client):
    """Admin schema for talk entity must expose storyId."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    talk_schema = res.json()["entities"]["talk"]
    field_keys = [f["key"] for f in talk_schema["fields"]]
    assert "storyId" in field_keys


def test_admin_crud_talk_story(admin_api_client):
    """Admin can create and update talk with storyId."""
    story = _create_story("talk-story-admin", "fa", "استوری ارائه")

    # 1. Validation: story locale mismatch
    story_en = _create_story("talk-story-en", "en", "Talk Story EN")
    bad_res = admin_api_client.post(
        "/api/v1/admin/content/talk",
        {
            "locale": "fa",
            "slug": "bad-story-talk",
            "title": "ارائه با زبان استوری نامعتبر",
            "status": "draft",
            "fields": {"storyId": story_en.pk},
        },
        content_type="application/json",
    )
    assert bad_res.status_code == 400
    assert bad_res.json()["code"] == "VALIDATION"

    # 2. Validation: non-story composition page
    non_story = CompositionPage.objects.create(
        key="home-comp-talk-test",
        kind="home",
        locale="fa",
        title="صفحه اصلی",
    )
    bad_res2 = admin_api_client.post(
        "/api/v1/admin/content/talk",
        {
            "locale": "fa",
            "slug": "bad-kind-talk",
            "title": "ارائه با نوع نامعتبر",
            "status": "draft",
            "fields": {"storyId": non_story.pk},
        },
        content_type="application/json",
    )
    assert bad_res2.status_code == 400
    assert bad_res2.json()["code"] == "VALIDATION"

    # 3. Create talk with valid story
    create_res = admin_api_client.post(
        "/api/v1/admin/content/talk",
        {
            "locale": "fa",
            "slug": "ai-keynote-fa",
            "title": "سخنرانی هوش مصنوعی",
            "status": "draft",
            "fields": {
                "storyId": story.pk,
                "speakers": "طاها محمدی",
                "eventName": "کنفرانس هوش مصنوعی",
                "abstract": "چکیده ارائه تخصصی",
            },
        },
        content_type="application/json",
    )
    assert create_res.status_code == 201
    data = create_res.json()
    talk_id = data["id"]
    assert data["fields"]["storyId"] == story.pk

    # 4. Update talk (clear storyId)
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/talk/{talk_id}",
        {"fields": {"storyId": None}},
        content_type="application/json",
        HTTP_IF_MATCH=data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["storyId"] is None


def test_public_talk_detail_exposes_story_and_metadata():
    """Public GET /api/talks/{locale}/{slug} exposes sanitized story and metadata."""
    client = Client()
    story = _create_story("keynote-story", "fa", "استوری سخنرانی کلیدی")
    article = Article.objects.create(
        locale="fa",
        slug="related-talk-art",
        title="مقاله مرتبط ارائه",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    t_key = str(uuid.uuid4())

    talk_fa = Talk.objects.create(
        locale="fa",
        slug="keynote-talk",
        title="سخنرانی کلیدی",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        abstract="چکیده سخنرانی",
        story=story,
        translation_key=t_key,
        related_records=[{"family": "article", "id": str(article.pk)}],
    )
    Talk.objects.create(
        locale="en",
        slug="keynote-talk-en",
        title="Keynote Talk",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        translation_key=t_key,
    )

    res = client.get(f"/api/talks/fa/{talk_fa.slug}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["slug"] == "keynote-talk"
    assert detail["story"] is not None
    assert detail["story"]["title"] == "استوری سخنرانی کلیدی"
    assert len(detail["story"]["sections"]) == 1
    body_text = detail["story"]["sections"][0]["blocks"][0]["settings"]["body"]
    assert body_text == "<p>Talk story body.</p>"

    # Metadata projections
    assert len(detail["alternates"]) == 1
    assert detail["alternates"][0]["locale"] == "en"
    assert detail["alternates"][0]["slug"] == "keynote-talk-en"
    assert detail["alternates"][0]["routeFamily"] == "talks"

    assert len(detail["relatedRecords"]) == 1
    assert detail["relatedRecords"][0]["slug"] == "related-talk-art"
    assert detail["relatedRecords"][0]["family"] == "article"


def test_public_talk_detail_draft_isolation():
    """Draft story or draft talk isolation."""
    client = Client()

    # Published talk with draft story
    draft_story = _create_story("draft-talk-story", "fa", "استوری پیش‌نویس", status="draft")
    talk = Talk.objects.create(
        locale="fa",
        slug="draft-story-talk",
        title="ارائه با استوری پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
    )
    res = client.get(f"/api/talks/fa/{talk.slug}")
    assert res.status_code == 200
    assert res.json()["story"] is None

    # Draft talk returns 404
    draft_talk = Talk.objects.create(
        locale="fa",
        slug="draft-talk",
        title="ارائه پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )
    res2 = client.get(f"/api/talks/fa/{draft_talk.slug}")
    assert res2.status_code == 404
