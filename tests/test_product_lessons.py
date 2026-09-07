"""Tests for PU-05-lessons: course lessons with stable localized slugs,
publication guards, ordered neighbors, and admin registration.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I05
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-05-lessons.md
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
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
    Course,
    Lesson,
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


def _create_course(slug: str, locale: str = "fa", status: str = "published") -> Course:
    return Course.objects.create(
        locale=locale,
        slug=slug,
        title=f"Course {slug}",
        status=status,
        published_at=timezone.now() if status == "published" else None,
        description=f"Description for {slug}",
    )


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
        settings={"body": "<p>Lesson content.</p>"},
        enabled=True,
    )
    return page


def test_lesson_model_declaration_and_clean():
    """Lesson model must declare course, position, summary, story, and validate locale match."""
    field_names = [f.name for f in Lesson._meta.get_fields()]
    assert "course" in field_names
    assert "position" in field_names
    assert "summary" in field_names
    assert "story" in field_names
    assert "related_records" in field_names
    assert "translation_key" in field_names

    course_fa = _create_course("course-fa", "fa")
    lesson = Lesson(
        course=course_fa,
        locale="en",
        slug="mismatched-locale-lesson",
        title="Mismatch",
    )
    with pytest.raises(ValidationError):
        lesson.clean()


def test_admin_content_schema_exposes_lesson(admin_api_client):
    """Admin schema must expose lesson entity with courseId, position, and metadata fields."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    entities = res.json()["entities"]
    assert "lesson" in entities
    fields = {f["key"]: f for f in entities["lesson"]["fields"]}
    assert "courseId" in fields
    assert fields["courseId"]["type"] == "number"
    assert "position" in fields
    assert "storyId" in fields
    assert "seoTitle" in fields
    assert "relatedRecords" in fields


def test_admin_crud_lesson(admin_api_client):
    """Admin content endpoints support lesson creation, validation, and update."""
    course = _create_course("course-admin", "fa")
    story = _create_story("lesson-story-admin", "fa", "استوری درس")

    # 1. Validation: course locale mismatch
    course_en = _create_course("course-en", "en")
    res = admin_api_client.post(
        "/api/v1/admin/content/lesson",
        {
            "locale": "fa",
            "slug": "bad-course-locale",
            "title": "درس با دوره نامطابق",
            "status": "draft",
            "fields": {"courseId": course_en.pk},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"

    # 2. Create lesson
    create_res = admin_api_client.post(
        "/api/v1/admin/content/lesson",
        {
            "locale": "fa",
            "slug": "lesson-1",
            "title": "درس اول",
            "status": "draft",
            "fields": {
                "courseId": course.pk,
                "position": 1,
                "summary": "خلاصه درس اول",
                "storyId": story.pk,
            },
        },
        content_type="application/json",
    )
    assert create_res.status_code == 201
    data = create_res.json()
    lesson_id = data["id"]
    assert data["fields"]["courseId"] == course.pk
    assert data["fields"]["position"] == 1
    assert data["fields"]["storyId"] == story.pk

    # 3. Update lesson
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/lesson/{lesson_id}",
        {"fields": {"position": 2}},
        content_type="application/json",
        HTTP_IF_MATCH=data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["position"] == 2


def test_public_lesson_list():
    """GET /api/v1/lessons/{locale}?course={courseSlug} returns cards ordered by position."""
    client = Client()
    course = _create_course("algebra-101", "fa")
    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="lesson-2",
        title="درس دوم",
        position=2,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        summary="خلاصه دوم",
    )
    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="lesson-1",
        title="درس اول",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        summary="خلاصه اول",
    )
    # Draft lesson (must be excluded)
    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="lesson-draft",
        title="درس پیش‌نویس",
        position=0,
        status=LifecycleStatus.DRAFT,
    )

    res = client.get("/api/v1/lessons/fa?course=algebra-101")
    assert res.status_code == 200
    payload = res.json()
    assert payload["count"] == 2
    assert len(payload["items"]) == 2
    assert payload["items"][0]["slug"] == "lesson-1"
    assert payload["items"][0]["position"] == 1
    assert payload["items"][0]["courseSlug"] == "algebra-101"
    assert payload["items"][1]["slug"] == "lesson-2"
    assert payload["items"][1]["position"] == 2


def test_public_lesson_detail_with_ordered_neighbors():
    """GET /api/v1/lessons/{locale}/{courseSlug}/{lessonSlug} returns detail with previous/next."""
    client = Client()
    course = _create_course("cs-101", "fa")
    story = _create_story("cs-story-2", "fa", "استوری درس ۲")

    article = Article.objects.create(
        locale="fa",
        slug="related-art",
        title="مقاله مرتبط",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="intro",
        title="مقدمه",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="variables",
        title="متغیرها",
        position=2,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        summary="خلاصه درس متغیرها",
        story=story,
        related_records=[{"family": "article", "id": str(article.pk)}],
    )
    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="functions",
        title="توابع",
        position=3,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    res = client.get("/api/v1/lessons/fa/cs-101/variables")
    assert res.status_code == 200
    detail = res.json()
    assert detail["slug"] == "variables"
    assert detail["courseSlug"] == "cs-101"
    assert detail["position"] == 2
    assert detail["summary"] == "خلاصه درس متغیرها"
    assert detail["story"] is not None
    assert detail["story"]["title"] == "استوری درس ۲"

    # Previous neighbor is intro
    assert detail["previous"] is not None
    assert detail["previous"]["slug"] == "intro"
    assert detail["previous"]["family"] == "lesson"
    assert detail["previous"]["courseSlug"] == "cs-101"

    # Next neighbor is functions
    assert detail["next"] is not None
    assert detail["next"]["slug"] == "functions"
    assert detail["next"]["family"] == "lesson"
    assert detail["next"]["courseSlug"] == "cs-101"

    # Resources (resolved related records)
    assert len(detail["resources"]) == 1
    assert detail["resources"][0]["slug"] == "related-art"
    assert detail["resources"][0]["family"] == "article"


def test_public_lesson_draft_isolation_and_parent_publication_guard():
    """Draft course, draft lesson, or cross-locale return 404 on public detail."""
    client = Client()
    draft_course = _create_course("draft-course", "fa", status="draft")
    lesson_in_draft_course = Lesson.objects.create(
        course=draft_course,
        locale="fa",
        slug="lesson-orphan",
        title="درس دوره پیش‌نویس",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    # When parent course is draft: detail returns 404
    res = client.get(f"/api/v1/lessons/fa/draft-course/{lesson_in_draft_course.slug}")
    assert res.status_code == 404

    # List returns count 0
    list_res = client.get("/api/v1/lessons/fa?course=draft-course")
    assert list_res.status_code == 200
    assert list_res.json()["count"] == 0

    # When lesson itself is draft in published course
    pub_course = _create_course("pub-course", "fa", status="published")
    draft_lesson = Lesson.objects.create(
        course=pub_course,
        locale="fa",
        slug="draft-lesson",
        title="درس پیش‌نویس",
        position=1,
        status=LifecycleStatus.DRAFT,
    )
    res2 = client.get(f"/api/v1/lessons/fa/pub-course/{draft_lesson.slug}")
    assert res2.status_code == 404


def test_public_lesson_ordered_neighbors_skip_drafts():
    """Drafts must never be public neighbors.

    Lesson 1 and Lesson 3 are neighbors if Lesson 2 is draft.
    """
    client = Client()
    course = _create_course("python-core", "fa")

    l1 = Lesson.objects.create(
        course=course,
        locale="fa",
        slug="lesson-01",
        title="درس اول",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    Lesson.objects.create(
        course=course,
        locale="fa",
        slug="lesson-02-draft",
        title="درس دوم پیش‌نویس",
        position=2,
        status=LifecycleStatus.DRAFT,
    )
    l3 = Lesson.objects.create(
        course=course,
        locale="fa",
        slug="lesson-03",
        title="درس سوم",
        position=3,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    res1 = client.get(f"/api/v1/lessons/fa/python-core/{l1.slug}")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["previous"] is None
    assert data1["next"] is not None
    assert data1["next"]["slug"] == "lesson-03"
    assert data1["next"]["courseSlug"] == "python-core"

    res3 = client.get(f"/api/v1/lessons/fa/python-core/{l3.slug}")
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["next"] is None
    assert data3["previous"] is not None
    assert data3["previous"]["slug"] == "lesson-01"
    assert data3["previous"]["courseSlug"] == "python-core"


def test_public_lesson_alternates_with_course_slug():
    """Lesson alternates include courseSlug and education routeFamily."""
    import uuid

    client = Client()
    course_fa = _create_course("course-fa", "fa")
    course_en = _create_course("course-en", "en")
    t_key = str(uuid.uuid4())

    lesson_fa = Lesson.objects.create(
        course=course_fa,
        locale="fa",
        slug="lesson-intro",
        title="معرفی",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        translation_key=t_key,
    )
    Lesson.objects.create(
        course=course_en,
        locale="en",
        slug="intro-lesson",
        title="Introduction",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        translation_key=t_key,
    )

    res = client.get(f"/api/v1/lessons/fa/course-fa/{lesson_fa.slug}")
    assert res.status_code == 200
    detail = res.json()
    assert len(detail["alternates"]) == 1
    alt = detail["alternates"][0]
    assert alt["locale"] == "en"
    assert alt["slug"] == "intro-lesson"
    assert alt["routeFamily"] == "education"
    assert alt["courseSlug"] == "course-en"


def test_lesson_as_related_record_work_ref():
    """When a lesson is referenced in related_records, WorkRef includes courseSlug."""
    client = Client()
    course = _create_course("science-101", "fa")
    target_lesson = Lesson.objects.create(
        course=course,
        locale="fa",
        slug="target-lesson",
        title="درس هدف",
        position=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        summary="خلاصه درس هدف",
    )

    source_lesson = Lesson.objects.create(
        course=course,
        locale="fa",
        slug="source-lesson",
        title="درس مبدا",
        position=2,
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        related_records=[{"family": "lesson", "id": str(target_lesson.pk)}],
    )

    res = client.get(f"/api/v1/lessons/fa/science-101/{source_lesson.slug}")
    assert res.status_code == 200
    detail = res.json()
    assert len(detail["resources"]) == 1
    ref = detail["resources"][0]
    assert ref["family"] == "lesson"
    assert ref["id"] == str(target_lesson.pk)
    assert ref["slug"] == "target-lesson"
    assert ref["routeFamily"] == "education"
    assert ref["courseSlug"] == "science-101"
