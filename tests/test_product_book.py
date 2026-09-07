"""Tests for PU-06-book: extend existing book detail and generic editor map with story/metadata.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I01/I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-book.md
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
    Book,
    LifecycleStatus,
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
        settings={"body": "<p>Book story body.</p>"},
        enabled=True,
    )
    return page


def test_book_model_has_story_field():
    """Book model must define a nullable ForeignKey to composition story."""
    fields = {f.name: f for f in Book._meta.get_fields()}
    assert "story" in fields
    assert fields["story"].is_relation
    assert fields["story"].related_model == CompositionPage


def test_admin_content_schema_exposes_book_story_id(admin_api_client):
    """Admin schema for book entity must expose storyId."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    book_schema = res.json()["entities"]["book"]
    field_keys = [f["key"] for f in book_schema["fields"]]
    assert "storyId" in field_keys


def test_admin_crud_book_story(admin_api_client):
    """Admin can create and update book with storyId."""
    story = _create_story("book-story-admin", "fa", "استوری کتاب")

    # 1. Validation: story locale mismatch
    story_en = _create_story("book-story-en", "en", "Book Story EN")
    bad_res = admin_api_client.post(
        "/api/v1/admin/content/book",
        {
            "locale": "fa",
            "slug": "bad-story-book",
            "title": "کتاب با زبان استوری نامعتبر",
            "status": "draft",
            "fields": {"storyId": story_en.pk},
        },
        content_type="application/json",
    )
    assert bad_res.status_code == 400
    assert bad_res.json()["code"] == "VALIDATION"

    # 2. Validation: non-story composition page
    non_story = CompositionPage.objects.create(
        key="home-comp-page",
        kind="home",
        locale="fa",
        title="صفحه اصلی",
    )
    bad_res2 = admin_api_client.post(
        "/api/v1/admin/content/book",
        {
            "locale": "fa",
            "slug": "bad-kind-book",
            "title": "کتاب با نوع نامعتبر",
            "status": "draft",
            "fields": {"storyId": non_story.pk},
        },
        content_type="application/json",
    )
    assert bad_res2.status_code == 400
    assert bad_res2.json()["code"] == "VALIDATION"

    # 3. Create book with valid story
    create_res = admin_api_client.post(
        "/api/v1/admin/content/book",
        {
            "locale": "fa",
            "slug": "deep-learning-fa",
            "title": "یادگیری عمیق",
            "status": "draft",
            "fields": {
                "storyId": story.pk,
                "authors": "طاها محمدی",
                "description": "کتاب تخصصی",
            },
        },
        content_type="application/json",
    )
    assert create_res.status_code == 201
    data = create_res.json()
    book_id = data["id"]
    assert data["fields"]["storyId"] == story.pk

    # 4. Update book (clear storyId)
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/book/{book_id}",
        {"fields": {"storyId": None}},
        content_type="application/json",
        HTTP_IF_MATCH=data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["storyId"] is None


def test_public_book_detail_exposes_story_and_metadata():
    """Public GET /api/books/{locale}/{slug} exposes sanitized story and metadata."""
    client = Client()
    story = _create_story("math-book-story", "fa", "استوری کتاب ریاضی")
    article = Article.objects.create(
        locale="fa",
        slug="related-math-art",
        title="مقاله مرتبط",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    t_key = str(uuid.uuid4())

    book_fa = Book.objects.create(
        locale="fa",
        slug="advanced-math",
        title="ریاضیات پیشرفته",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        description="توضیحات کتاب",
        story=story,
        translation_key=t_key,
        related_records=[{"family": "article", "id": str(article.pk)}],
    )
    Book.objects.create(
        locale="en",
        slug="advanced-math-en",
        title="Advanced Mathematics",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        translation_key=t_key,
    )

    res = client.get(f"/api/books/fa/{book_fa.slug}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["slug"] == "advanced-math"
    assert detail["story"] is not None
    assert detail["story"]["title"] == "استوری کتاب ریاضی"
    assert len(detail["story"]["sections"]) == 1
    body_text = detail["story"]["sections"][0]["blocks"][0]["settings"]["body"]
    assert body_text == "<p>Book story body.</p>"

    # Metadata projections
    assert len(detail["alternates"]) == 1
    assert detail["alternates"][0]["locale"] == "en"
    assert detail["alternates"][0]["slug"] == "advanced-math-en"
    assert detail["alternates"][0]["routeFamily"] == "books"

    assert len(detail["relatedRecords"]) == 1
    assert detail["relatedRecords"][0]["slug"] == "related-math-art"
    assert detail["relatedRecords"][0]["family"] == "article"


def test_public_book_detail_draft_isolation():
    """Draft story or draft book isolation."""
    client = Client()

    # Published book with draft story
    draft_story = _create_story("draft-book-story", "fa", "استوری پیش‌نویس", status="draft")
    book = Book.objects.create(
        locale="fa",
        slug="phys-book",
        title="فیزیک",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
    )
    res = client.get(f"/api/books/fa/{book.slug}")
    assert res.status_code == 200
    assert res.json()["story"] is None

    # Draft book returns 404
    draft_book = Book.objects.create(
        locale="fa",
        slug="draft-book",
        title="کتاب پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )
    res2 = client.get(f"/api/books/fa/{draft_book.slug}")
    assert res2.status_code == 404
