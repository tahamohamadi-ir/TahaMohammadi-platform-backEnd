"""Tests for PU-04-course: attach nullable localized story to course,
validate admin storyId, and expose sanitized public detail.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-course.md
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
from apps.content.models import Course, LifecycleStatus


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
        settings={"body": "<p>Course syllabus and introduction.</p>"},
        enabled=True,
    )
    return page


def test_course_model_has_story_field():
    """Verify Course model defines nullable ForeignKey to CompositionPage."""
    field_names = [f.name for f in Course._meta.get_fields()]
    assert "story" in field_names, "Course must define a 'story' relation"
    field = Course._meta.get_field("story")
    assert field.is_relation
    assert field.null is True
    assert field.blank is True
    assert field.related_model == CompositionPage


def test_admin_content_schema_exposes_course_story_id(admin_api_client):
    """Admin schema must expose storyId for course."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    entities = res.json()["entities"]
    assert "course" in entities
    course_fields = {f["key"]: f for f in entities["course"]["fields"]}
    assert "storyId" in course_fields
    assert course_fields["storyId"]["type"] == "number"


def test_admin_crud_course_story_id(admin_api_client):
    """Setting storyId via admin content CRUD updates the relation with optimistic locking."""
    story = _create_story("course-story-1", "fa", "استوری درس ۱")

    # 1. Create with storyId
    create_payload = {
        "locale": "fa",
        "slug": "test-course-with-story",
        "title": "دوره با استوری",
        "status": "draft",
        "fields": {
            "storyId": story.pk,
            "description": "توضیحات دوره آموزشی",
        },
    }
    create_res = admin_api_client.post(
        "/api/v1/admin/content/course",
        create_payload,
        content_type="application/json",
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    course_id = created_data["id"]
    assert created_data["fields"]["storyId"] == story.pk

    course = Course.objects.get(pk=course_id)
    assert course.story == story

    # 2. Update to clear storyId
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/course/{course_id}",
        {"fields": {"storyId": None}},
        content_type="application/json",
        HTTP_IF_MATCH=created_data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["storyId"] is None

    course.refresh_from_db()
    assert course.story is None


def test_admin_course_story_validation(admin_api_client):
    """storyId must reject non-existent PK, non-story kinds, and locale mismatches."""
    # Non-existent ID
    res = admin_api_client.post(
        "/api/v1/admin/content/course",
        {
            "locale": "fa",
            "slug": "course-invalid-story-id",
            "title": "دوره نامعتبر",
            "status": "draft",
            "fields": {"storyId": 999999},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"

    # Landing composition (not a story)
    landing = CompositionPage.objects.create(
        key="landing-page-course",
        kind=CompositionPage.KIND_LANDING,
        locale="fa",
        title="صفحه اصلی",
    )
    res = admin_api_client.post(
        "/api/v1/admin/content/course",
        {
            "locale": "fa",
            "slug": "course-landing-story",
            "title": "دوره با صفحه لندینگ",
            "status": "draft",
            "fields": {"storyId": landing.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "storyId must reference a story composition" in res.json()["message"]

    # Locale mismatch
    en_story = _create_story("en-course-story-1", "en", "English Course Story")
    res = admin_api_client.post(
        "/api/v1/admin/content/course",
        {
            "locale": "fa",
            "slug": "course-locale-mismatch",
            "title": "عدم تطابق زبان دوره",
            "status": "draft",
            "fields": {"storyId": en_story.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "storyId locale must match" in res.json()["message"]


def test_public_course_detail_exposes_story():
    """Published course with published story returns sanitized story document."""
    client = Client()
    story = _create_story("course-story-pub", "fa", "استوری دوره منتشرشده", status="published")
    course = Course.objects.create(
        locale="fa",
        slug="published-course-with-story",
        title="دوره با استوری",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=story,
        description="توضیحات دوره",
    )

    res = client.get(f"/api/teaching/fa/{course.slug}")
    assert res.status_code == 200
    data = res.json()
    assert "story" in data
    assert data["story"] is not None
    assert data["story"]["locale"] == "fa"
    assert data["story"]["title"] == "استوری دوره منتشرشده"
    assert len(data["story"]["sections"]) == 1
    blocks = data["story"]["sections"][0]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["blockType"] == "text"
    assert "<p>Course syllabus and introduction.</p>" in blocks[0]["settings"]["body"]


def test_public_course_detail_draft_isolation():
    """Story is omitted (returns None) if story is draft or story locale mismatches."""
    client = Client()
    draft_story = _create_story("course-story-draft", "fa", "استوری پیش‌نویس دوره", status="draft")
    course = Course.objects.create(
        locale="fa",
        slug="course-with-draft-story",
        title="دوره با استوری پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
        description="توضیحات دوره",
    )

    res = client.get(f"/api/teaching/fa/{course.slug}")
    assert res.status_code == 200
    data = res.json()
    assert "story" in data
    assert data["story"] is None
