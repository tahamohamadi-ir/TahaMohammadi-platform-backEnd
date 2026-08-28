"""Featured/diagram/screenshot Media rewire — public projection + usage registry."""

from datetime import date, timedelta

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.api.admin_media import MEDIA_REFERENCE_FIELDS, media_usage_count
from apps.content.models import (
    Article,
    EvidenceVisibility,
    LifecycleStatus,
    Locale,
    Project,
    ProjectDiagram,
    ProjectScreenshot,
    ProjectType,
)
from apps.media.models import Media

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device, media_root):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def api_client(media_root):
    return Client()


def past():
    return timezone.now() - timedelta(days=1)


def _make_image(*, title="hero", is_active=True) -> Media:
    return Media.objects.create(
        file=SimpleUploadedFile(f"{title}.png", PNG_1X1, content_type="image/png"),
        title=title,
        alt_text=f"{title}-alt",
        is_active=is_active,
    )


def test_media_reference_fields_cover_content_image_fks():
    assert ("content.Article", "featured_image") in MEDIA_REFERENCE_FIELDS
    assert ("content.ProjectDiagram", "diagram_image") in MEDIA_REFERENCE_FIELDS
    assert ("content.ProjectScreenshot", "screenshot_image") in MEDIA_REFERENCE_FIELDS
    assert ("content.ResearchStatement", "statement_pdf") in MEDIA_REFERENCE_FIELDS


def _make_story_with_blocks(*, media):
    from apps.composition.models import (
        CompositionBlock,
        CompositionPage,
        CompositionSection,
    )

    page = CompositionPage.objects.create(
        key="story-json-usage",
        kind=CompositionPage.KIND_STORY,
        locale=Locale.FA,
        title="داستان",
    )
    section = CompositionSection.objects.create(page=page, position=0)
    CompositionBlock.objects.create(
        section=section,
        position=0,
        block_type="image",
        settings={"mediaId": str(media.pk)},  # numeric string must count too
    )
    CompositionBlock.objects.create(
        section=section,
        position=1,
        block_type="gallery",
        settings={"mediaIds": [media.pk, 999_999]},
    )
    CompositionBlock.objects.create(
        section=section,
        position=2,
        block_type="before_after",
        settings={"beforeMediaId": media.pk, "afterMediaId": "not-a-number"},
    )
    return page


@pytest.mark.django_db
def test_composition_block_json_media_counts_toward_usage():
    used = _make_image(title="story-media")
    unused = _make_image(title="story-unused")
    _make_story_with_blocks(media=used)
    # One reference per block: image (mediaId) + gallery (mediaIds) + before_after.
    assert media_usage_count(used) == 3
    assert media_usage_count(unused) == 0


@pytest.mark.django_db
def test_media_usage_counts_featured_image_reference():
    media = _make_image(title="used-featured")
    unused = _make_image(title="unused")
    Article.objects.create(
        locale=Locale.EN,
        slug="with-hero",
        title="With hero",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
        featured_image=media,
    )
    assert media_usage_count(media) == 1
    assert media_usage_count(unused) == 0


@pytest.mark.django_db
def test_public_article_exposes_active_featured_image(api_client):
    active = _make_image(title="public-hero", is_active=True)
    inactive = _make_image(title="hidden-hero", is_active=False)
    Article.objects.create(
        locale=Locale.EN,
        slug="hero-article",
        title="Hero article",
        excerpt="ex",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
        featured_image=active,
    )
    Article.objects.create(
        locale=Locale.EN,
        slug="inactive-hero",
        title="Inactive hero",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
        featured_image=inactive,
    )

    ok = api_client.get("/api/articles/en/hero-article")
    assert ok.status_code == 200
    body = ok.json()
    assert body["featured_image"] is not None
    assert body["featured_image"]["url"]
    assert body["featured_image"]["alt"] == "public-hero-alt"
    assert "featured_image_id" not in body

    hidden = api_client.get("/api/articles/en/inactive-hero")
    assert hidden.status_code == 200
    assert hidden.json()["featured_image"] is None


@pytest.mark.django_db
def test_public_project_diagram_screenshot_image_urls(api_client):
    media = _make_image(title="diagram")
    project = Project.objects.create(
        locale=Locale.EN,
        slug="media-case",
        title="Media case",
        project_type=ProjectType.ENGINEERING,
        objective="obj",
        methods_summary="m",
        role="r",
        license="cc-by-4",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    ProjectDiagram.objects.create(
        project=project,
        title="Arch",
        version="1",
        diagram_date=date(2026, 1, 1),
        alt_text="architecture diagram",
        long_description="desc",
        diagram_image=media,
        visibility=EvidenceVisibility.PUBLIC,
    )
    ProjectScreenshot.objects.create(
        project=project,
        caption="UI",
        alt_text="screenshot alt",
        screenshot_image=media,
        visibility=EvidenceVisibility.PUBLIC,
    )

    response = api_client.get("/api/projects/en/media-case")
    assert response.status_code == 200
    data = response.json()
    assert data["diagrams"][0]["image"]["url"]
    assert data["screenshots"][0]["image"]["url"]
    assert "diagram_image" not in str(data)
    assert "screenshot_image" not in str(data)


@pytest.mark.django_db
def test_admin_can_set_featured_image_id(admin_api_client):
    media = _make_image(title="admin-hero")
    article = Article.objects.create(
        locale=Locale.FA,
        slug="admin-hero-article",
        title="Admin hero",
        status=LifecycleStatus.DRAFT,
    )
    detail = admin_api_client.get(f"/api/v1/admin/content/article/{article.pk}")
    assert detail.status_code == 200
    updated_at = detail.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/content/article/{article.pk}",
        data={
            "fields": {"featuredImageId": media.pk},
        },
        content_type="application/json",
        HTTP_IF_MATCH=updated_at,
    )
    assert response.status_code == 200, response.content
    assert response.json()["fields"]["featuredImageId"] == media.pk
    article.refresh_from_db()
    assert article.featured_image_id == media.pk

    schema = admin_api_client.get("/api/v1/admin/content/schema")
    assert schema.status_code == 200
    article_fields = {
        f["key"]: f["type"] for f in schema.json()["entities"]["article"]["fields"]
    }
    assert article_fields["featuredImageId"] == "media"


@pytest.mark.django_db
def test_admin_project_case_media_image_assign(admin_api_client):
    media = _make_image(title="case-diagram")
    project = Project.objects.create(
        locale=Locale.EN,
        slug="case-media-admin",
        title="Case media admin",
        status=LifecycleStatus.DRAFT,
    )
    diagram = ProjectDiagram.objects.create(
        project=project,
        title="D",
        version="1",
        diagram_date=date(2026, 2, 2),
        alt_text="alt",
        visibility=EvidenceVisibility.INTERNAL,
    )
    listed = admin_api_client.get(f"/api/v1/admin/content/project/{project.pk}/case-media")
    assert listed.status_code == 200
    assert listed.json()["diagrams"][0]["diagramImageId"] is None

    updated = admin_api_client.put(
        f"/api/v1/admin/content/project/{project.pk}/diagrams/{diagram.pk}",
        data={"diagramImageId": media.pk},
        content_type="application/json",
    )
    assert updated.status_code == 200, updated.content
    assert updated.json()["diagramImageId"] == media.pk
    diagram.refresh_from_db()
    assert diagram.diagram_image_id == media.pk


@pytest.mark.django_db
def test_orphans_endpoint_excludes_block_referenced_media(admin_api_client):
    block_used = _make_image(title="block-used")
    orphan = _make_image(title="true-orphan")
    _make_story_with_blocks(media=block_used)

    listed = admin_api_client.get("/api/v1/admin/media/orphans")
    assert listed.status_code == 200
    orphan_ids = {item["id"] for item in listed.json()["items"]}
    assert orphan.pk in orphan_ids
    assert block_used.pk not in orphan_ids

    detail = admin_api_client.get(f"/api/v1/admin/media/{block_used.pk}")
    assert detail.status_code == 200
    assert detail.json()["usageCount"] == 3
