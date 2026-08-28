"""Story composition catalog, article FK, and published-only public projection."""

import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.composition.blocks import KIND_STORY, BlockValidationError, validate_block_settings
from apps.composition.models import CompositionBlock, CompositionPage, CompositionSection
from apps.content.models import (
    Article,
    LifecycleStatus,
    Locale,
    Profile,
    ProfileExperience,
    Project,
    ResearchStatement,
    ResearchTopic,
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
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def admin_api_client(media_root, admin_user, totp_device):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def test_landing_schema_unchanged(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/composition/schema")
    assert response.status_code == 200
    types = {item["type"] for item in response.json()["blockTypes"]}
    assert "hero" in types
    assert "figure" not in types
    assert response.json()["kind"] == "landing"


def test_story_schema_is_single_locale(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/composition/schema?kind=story")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "story"
    types = {item["type"] for item in body["blockTypes"]}
    assert {"figure", "video", "audio", "math", "text"} <= types
    assert "hero" not in types
    text = next(item for item in body["blockTypes"] if item["type"] == "text")
    keys = {field["key"] for field in text["fields"]}
    assert keys == {"body"}


def test_story_blocks_reject_landing_keys(db):
    with pytest.raises(BlockValidationError):
        validate_block_settings(
            "text", {"bodyFa": "x", "bodyEn": "y"}, kind=KIND_STORY
        )


def test_story_figure_requires_image_media(db, media_root):
    image = Media.objects.create(
        file=SimpleUploadedFile("photo.png", PNG_1X1, content_type="image/png"),
        title="Photo",
        is_active=True,
    )
    validate_block_settings("figure", {"mediaId": image.pk, "caption": "c"}, kind=KIND_STORY)
    with pytest.raises(BlockValidationError):
        validate_block_settings("video", {"mediaId": image.pk}, kind=KIND_STORY)


def _put_json(client, path, payload, updated_at):
    return client.put(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )


def test_story_page_rejects_hero_block(admin_api_client):
    created = admin_api_client.post(
        "/api/v1/admin/composition",
        data=json.dumps(
            {
                "key": "article-1-story",
                "locale": "en",
                "title": "Story",
                "kind": "story",
            }
        ),
        content_type="application/json",
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "story"
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]
    response = _put_json(
        admin_api_client,
        f"/api/v1/admin/composition/{page_id}",
        {
            "sections": [
                {
                    "layout": "1col",
                    "ratio": "",
                    "enabled": True,
                    "blocks": [
                        {
                            "blockType": "hero",
                            "settings": {
                                "titleFa": "t",
                                "titleEn": "t",
                                "leadFa": "l",
                                "leadEn": "e",
                            },
                            "enabled": True,
                        }
                    ],
                }
            ]
        },
        updated_at,
    )
    assert response.status_code == 400


def test_story_schema_includes_rich_blocks_v2(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/composition/schema?kind=story")
    assert response.status_code == 200
    types = {item["type"] for item in response.json()["blockTypes"]}
    assert {"accordion", "tabs", "timeline", "counters", "before_after", "slider"} <= types


def test_story_accordion_validates_items(db):
    validate_block_settings(
        "accordion",
        {"items": [{"title": "One", "body": "<p>Body</p>"}]},
        kind=KIND_STORY,
    )
    with pytest.raises(BlockValidationError):
        validate_block_settings("accordion", {"items": []}, kind=KIND_STORY)
    with pytest.raises(BlockValidationError):
        validate_block_settings(
            "accordion",
            {"items": [{"title": "One", "body": "<p>x</p>", "extra": "nope"}]},
            kind=KIND_STORY,
        )


def test_story_tabs_requires_two_panels(db):
    validate_block_settings(
        "tabs",
        {
            "items": [
                {"label": "A", "body": "<p>1</p>"},
                {"label": "B", "body": "<p>2</p>"},
            ]
        },
        kind=KIND_STORY,
    )
    with pytest.raises(BlockValidationError):
        validate_block_settings(
            "tabs",
            {"items": [{"label": "Only", "body": "<p>1</p>"}]},
            kind=KIND_STORY,
        )


def test_story_timeline_and_counters(db):
    validate_block_settings(
        "timeline",
        {"items": [{"date": "2024", "title": "Event", "body": "<p>Detail</p>"}]},
        kind=KIND_STORY,
    )
    validate_block_settings(
        "counters",
        {"items": [{"value": "42", "label": "Projects"}]},
        kind=KIND_STORY,
    )
    with pytest.raises(BlockValidationError):
        validate_block_settings(
            "counters",
            {"items": [{"value": "42"}]},
            kind=KIND_STORY,
        )


def test_story_before_after_requires_image_media(db, media_root):
    before = Media.objects.create(
        file=SimpleUploadedFile("before.png", PNG_1X1, content_type="image/png"),
        title="Before",
        is_active=True,
    )
    after = Media.objects.create(
        file=SimpleUploadedFile("after.png", PNG_1X1, content_type="image/png"),
        title="After",
        is_active=True,
    )
    validate_block_settings(
        "before_after",
        {"beforeMediaId": before.pk, "afterMediaId": after.pk},
        kind=KIND_STORY,
    )
    with pytest.raises(BlockValidationError):
        validate_block_settings(
            "before_after",
            {"beforeMediaId": before.pk, "afterMediaId": before.pk, "unknown": "x"},
            kind=KIND_STORY,
        )


def test_story_slider_allows_up_to_twelve_images(db, media_root):
    images = [
        Media.objects.create(
            file=SimpleUploadedFile(f"slide-{i}.png", PNG_1X1, content_type="image/png"),
            title=f"Slide {i}",
            is_active=True,
        )
        for i in range(12)
    ]
    validate_block_settings(
        "slider",
        {"mediaIds": [image.pk for image in images]},
        kind=KIND_STORY,
    )
    with pytest.raises(BlockValidationError):
        validate_block_settings("slider", {"mediaIds": []}, kind=KIND_STORY)


def test_public_article_story_published_only(db, media_root):
    image = Media.objects.create(
        file=SimpleUploadedFile("photo.png", PNG_1X1, content_type="image/png"),
        title="Photo",
        is_active=True,
    )
    draft_story = CompositionPage.objects.create(
        key="draft-story",
        kind=KIND_STORY,
        locale="en",
        title="Draft story",
        status="draft",
    )
    article = Article.objects.create(
        locale=Locale.EN,
        slug="story-post",
        title="Story post",
        body="<p>Fallback body</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
    )
    client = Client()
    draft_json = client.get("/api/articles/en/story-post").json()
    assert draft_json["story"] is None
    assert "Fallback body" in draft_json["body"]

    draft_story.status = "published"
    draft_story.published_at = timezone.now()
    draft_story.save()
    section = CompositionSection.objects.create(
        page=draft_story, position=0, layout="1col", enabled=True
    )
    CompositionBlock.objects.create(
        section=section,
        position=0,
        block_type="text",
        settings={"body": "<p>Story text</p><script>x</script>"},
        enabled=True,
    )
    CompositionBlock.objects.create(
        section=section,
        position=1,
        block_type="figure",
        settings={"mediaId": image.pk, "caption": "A photo"},
        enabled=True,
    )
    published = client.get("/api/articles/en/story-post").json()
    assert published["story"] is not None
    assert published["story"]["sections"][0]["blocks"][0]["blockType"] == "text"
    assert "<script>" not in published["story"]["sections"][0]["blocks"][0]["settings"]["body"]
    figure = published["story"]["sections"][0]["blocks"][1]
    assert figure["settings"]["media"]["url"].endswith(image.file.name)
    assert article.slug == "story-post"


def test_public_project_story_published_only(db, media_root):
    draft_story = CompositionPage.objects.create(
        key="project-draft-story",
        kind=KIND_STORY,
        locale="en",
        title="Project draft story",
        status="draft",
    )
    project = Project.objects.create(
        locale=Locale.EN,
        slug="story-project",
        title="Story project",
        objective="Fallback objective",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        show_on_projects=True,
        story=draft_story,
    )
    client = Client()
    draft_json = client.get("/api/projects/en/story-project").json()
    assert draft_json["story"] is None
    assert draft_json["objective"] == "Fallback objective"

    draft_story.status = "published"
    draft_story.published_at = timezone.now()
    draft_story.save()
    section = CompositionSection.objects.create(
        page=draft_story, position=0, layout="1col", enabled=True
    )
    CompositionBlock.objects.create(
        section=section,
        position=0,
        block_type="text",
        settings={"body": "<p>Project story</p>"},
        enabled=True,
    )
    published = client.get("/api/projects/en/story-project").json()
    assert published["story"] is not None
    assert published["story"]["sections"][0]["blocks"][0]["settings"]["body"]
    assert project.slug == "story-project"


def test_public_research_topic_and_statement_story(db, media_root):
    story = CompositionPage.objects.create(
        key="topic-story",
        kind=KIND_STORY,
        locale="en",
        title="Topic story",
        status="published",
        published_at=timezone.now(),
    )
    section = CompositionSection.objects.create(
        page=story, position=0, layout="1col", enabled=True
    )
    CompositionBlock.objects.create(
        section=section,
        position=0,
        block_type="text",
        settings={"body": "<p>Topic body</p>"},
        enabled=True,
    )
    topic = ResearchTopic.objects.create(
        locale=Locale.EN,
        slug="story-topic",
        title="Story topic",
        summary="Summary fallback",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=story,
    )
    statement_story = CompositionPage.objects.create(
        key="statement-story",
        kind=KIND_STORY,
        locale="en",
        title="Statement story",
        status="published",
        published_at=timezone.now(),
    )
    stmt_section = CompositionSection.objects.create(
        page=statement_story, position=0, layout="1col", enabled=True
    )
    CompositionBlock.objects.create(
        section=stmt_section,
        position=0,
        block_type="text",
        settings={"body": "<p>Statement story body</p>"},
        enabled=True,
    )
    statement = ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="story-statement",
        title="Story statement",
        body="<p>Richtext fallback</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=statement_story,
    )
    client = Client()
    topic_json = client.get("/api/research/topics/en/story-topic").json()
    assert topic_json["story"]["sections"][0]["blocks"][0]["blockType"] == "text"
    statement_json = client.get("/api/research/statements/en/story-statement").json()
    assert statement_json["story"] is not None
    assert topic.slug == "story-topic"
    assert statement.slug == "story-statement"


def test_profile_experience_story_public_projection(db, media_root):
    story = CompositionPage.objects.create(
        key="experience-story",
        kind=KIND_STORY,
        locale="en",
        title="Experience story",
        status="published",
        published_at=timezone.now(),
    )
    section = CompositionSection.objects.create(
        page=story, position=0, layout="1col", enabled=True
    )
    CompositionBlock.objects.create(
        section=section,
        position=0,
        block_type="text",
        settings={"body": "<p>Experience narrative</p>"},
        enabled=True,
    )
    profile = Profile.objects.create(
        locale=Locale.EN,
        slug="taha",
        title="Taha",
        short_bio="Bio",
        availability="Open",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    ProfileExperience.objects.create(
        profile=profile,
        ordering=0,
        organization="Org",
        role="Engineer",
        period="2020-2024",
        bullets=["Did things"],
        slug="mci-backend",
        detail_body="Fallback detail",
        story=story,
    )
    client = Client()
    payload = client.get("/api/profiles/en/taha").json()
    experience = payload["experience"][0]
    assert experience["storyId"] == story.pk
    assert experience["story"]["sections"][0]["blocks"][0]["blockType"] == "text"


def test_admin_project_story_id_field(admin_api_client):
    schema = admin_api_client.get("/api/v1/admin/content/schema").json()
    project_keys = {field["key"] for field in schema["entities"]["project"]["fields"]}
    topic_keys = {
        field["key"] for field in schema["entities"]["research-topic"]["fields"]
    }
    statement_keys = {
        field["key"] for field in schema["entities"]["research-statement"]["fields"]
    }
    assert "storyId" in project_keys
    assert "storyId" in topic_keys
    assert "storyId" in statement_keys
