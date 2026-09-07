"""Tests for PU-06-resource: extend existing download detail and editor map.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I01/I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-resource.md
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.composition.models import (
    CompositionBlock,
    CompositionPage,
    CompositionSection,
)
from apps.content.models import (
    AccessState,
    Article,
    Download,
    LifecycleStatus,
)
from apps.media.models import Media


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
        settings={"body": "<p>Resource story body.</p>"},
        enabled=True,
    )
    return page


def _create_media(title: str = "Sample Media", is_active: bool = True) -> Media:
    content = b"%PDF-1.4 test download file content"
    uploaded = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
    return Media.objects.create(
        file=uploaded,
        title=title,
        mime="application/pdf",
        size=len(content),
        is_active=is_active,
    )


def test_download_model_has_story_field():
    """Download model must define a nullable ForeignKey to composition story."""
    fields = {f.name: f for f in Download._meta.get_fields()}
    assert "story" in fields
    assert fields["story"].is_relation
    assert fields["story"].related_model == CompositionPage


def test_admin_content_schema_exposes_download_story_id(admin_api_client):
    """Admin schema for download entity must expose storyId."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    download_schema = res.json()["entities"]["download"]
    field_keys = [f["key"] for f in download_schema["fields"]]
    assert "storyId" in field_keys


def test_admin_crud_download_story(admin_api_client):
    """Admin can create and update download with storyId."""
    story = _create_story("resource-story-admin", "fa", "استوری منبع")
    media = _create_media("منبع دانلود", is_active=True)

    # 1. Validation: story locale mismatch
    story_en = _create_story("resource-story-en", "en", "Resource Story EN")
    bad_res = admin_api_client.post(
        "/api/v1/admin/content/download",
        {
            "locale": "fa",
            "slug": "bad-story-download",
            "title": "منبع با زبان استوری نامعتبر",
            "status": "draft",
            "fields": {
                "mediaId": media.pk,
                "storyId": story_en.pk,
            },
        },
        content_type="application/json",
    )
    assert bad_res.status_code == 400
    assert bad_res.json()["code"] == "VALIDATION"

    # 2. Validation: non-story composition page
    non_story = CompositionPage.objects.create(
        key="home-comp-resource-test",
        kind="home",
        locale="fa",
        title="صفحه اصلی",
    )
    bad_res2 = admin_api_client.post(
        "/api/v1/admin/content/download",
        {
            "locale": "fa",
            "slug": "bad-kind-download",
            "title": "منبع با نوع نامعتبر",
            "status": "draft",
            "fields": {
                "mediaId": media.pk,
                "storyId": non_story.pk,
            },
        },
        content_type="application/json",
    )
    assert bad_res2.status_code == 400
    assert bad_res2.json()["code"] == "VALIDATION"

    # 3. Create download with valid story
    create_res = admin_api_client.post(
        "/api/v1/admin/content/download",
        {
            "locale": "fa",
            "slug": "ai-paper-pdf-fa",
            "title": "فایل مقاله هوش مصنوعی",
            "status": "draft",
            "fields": {
                "mediaId": media.pk,
                "storyId": story.pk,
                "downloadType": "pdf",
                "description": "توضیحات دانلود مقاله",
            },
        },
        content_type="application/json",
    )
    assert create_res.status_code == 201
    data = create_res.json()
    download_id = data["id"]
    assert data["fields"]["storyId"] == story.pk

    # 4. Update download (clear storyId)
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/download/{download_id}",
        {"fields": {"storyId": None}},
        content_type="application/json",
        HTTP_IF_MATCH=data["updatedAt"],
    )
    assert update_res.status_code == 200
    assert update_res.json()["fields"]["storyId"] is None


def test_public_download_detail_exposes_story_and_metadata():
    """Public GET /api/downloads/{locale}/{slug} exposes sanitized story and metadata."""
    client = Client()
    story = _create_story("dataset-story", "fa", "استوری مجموعه داده")
    article = Article.objects.create(
        locale="fa",
        slug="related-resource-art",
        title="مقاله مرتبط منبع",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    media = _create_media("داده‌های پژوهش", is_active=True)
    t_key = str(uuid.uuid4())

    download_fa = Download.objects.create(
        locale="fa",
        slug="research-dataset",
        title="مجموعه داده‌های پژوهش",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        description="توضیحات منبع پژوهشی",
        media=media,
        story=story,
        translation_key=t_key,
        related_records=[{"family": "article", "id": str(article.pk)}],
    )
    media_en = _create_media("Research Dataset EN", is_active=True)
    Download.objects.create(
        locale="en",
        slug="research-dataset-en",
        title="Research Dataset",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        media=media_en,
        translation_key=t_key,
    )

    res = client.get(f"/api/downloads/fa/{download_fa.slug}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["slug"] == "research-dataset"
    assert detail["story"] is not None
    assert detail["story"]["title"] == "استوری مجموعه داده"
    assert len(detail["story"]["sections"]) == 1
    body_text = detail["story"]["sections"][0]["blocks"][0]["settings"]["body"]
    assert body_text == "<p>Resource story body.</p>"

    # Metadata projections
    assert len(detail["alternates"]) == 1
    assert detail["alternates"][0]["locale"] == "en"
    assert detail["alternates"][0]["slug"] == "research-dataset-en"
    assert detail["alternates"][0]["routeFamily"] == "resources"

    assert len(detail["relatedRecords"]) == 1
    assert detail["relatedRecords"][0]["slug"] == "related-resource-art"
    assert detail["relatedRecords"][0]["family"] == "article"


def test_public_download_detail_draft_isolation():
    """Draft story or draft download isolation."""
    client = Client()
    media = _create_media("منبع پیش‌نویس", is_active=True)

    # Published download with draft story
    draft_story = _create_story("draft-resource-story", "fa", "استوری پیش‌نویس منبع", status="draft")
    download = Download.objects.create(
        locale="fa",
        slug="draft-story-resource",
        title="منبع با استوری پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        media=media,
        story=draft_story,
    )
    res = client.get(f"/api/downloads/fa/{download.slug}")
    assert res.status_code == 200
    assert res.json()["story"] is None

    # Draft download returns 404
    draft_download = Download.objects.create(
        locale="fa",
        slug="draft-download",
        title="منبع پیش‌نویس",
        status=LifecycleStatus.DRAFT,
        media=media,
    )
    res2 = client.get(f"/api/downloads/fa/{draft_download.slug}")
    assert res2.status_code == 404


def test_gated_download_file_endpoint_preserved():
    """Verify gated file streaming endpoint /api/downloads/{locale}/{slug}/file and preservation."""
    client = Client()

    # 1. Public download with active media -> 200 FileResponse with correct security headers
    active_media = _create_media("فایل عمومی فعال", is_active=True)
    pub_download = Download.objects.create(
        locale="fa",
        slug="public-dataset-file",
        title="فایل عمومی",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        media=active_media,
        access_state=AccessState.PUBLIC,
    )
    res = client.get(f"/api/downloads/fa/{pub_download.slug}/file")
    assert res.status_code == 200
    assert res["X-Content-Type-Options"] == "nosniff"
    assert res["Cache-Control"] == "private, no-store"
    assert res["X-Robots-Tag"] == "noindex, nofollow"
    assert b"%PDF-1.4 test download file content" in res.getvalue()

    # Detail view also resolves file
    detail_res = client.get(f"/api/downloads/fa/{pub_download.slug}")
    assert detail_res.status_code == 200
    assert detail_res.json()["file"] is not None
    assert detail_res.json()["mime"] == "application/pdf"

    # 2. Restricted download -> 404 on /file and file: None on detail
    restricted_media = _create_media("فایل محدود", is_active=True)
    restr_download = Download.objects.create(
        locale="fa",
        slug="restricted-file",
        title="فایل محدود",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        media=restricted_media,
        access_state=AccessState.RESTRICTED,
    )
    res_restr = client.get(f"/api/downloads/fa/{restr_download.slug}/file")
    assert res_restr.status_code == 404

    detail_restr = client.get(f"/api/downloads/fa/{restr_download.slug}")
    assert detail_restr.status_code == 200
    assert detail_restr.json()["file"] is None
    assert detail_restr.json()["mime"] is None

    # 3. Inactive media -> 404 on /file and file: None on detail
    inactive_media = _create_media("فایل غیرفعال", is_active=False)
    inact_download = Download.objects.create(
        locale="fa",
        slug="inactive-media-download",
        title="منبع با مدیا غیرفعال",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        media=inactive_media,
        access_state=AccessState.PUBLIC,
    )
    res_inact = client.get(f"/api/downloads/fa/{inact_download.slug}/file")
    assert res_inact.status_code == 404

    detail_inact = client.get(f"/api/downloads/fa/{inact_download.slug}")
    assert detail_inact.status_code == 200
    assert detail_inact.json()["file"] is None
    assert detail_inact.json()["mime"] is None
