"""Tests for PU-06-series: series detail and ordered public membership.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I05
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-series.md
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
    Project,
    Series,
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
        kind="story",
        locale=locale,
        title=title,
        status=status,
        published_at=timezone.now() if status == "published" else None,
    )
    sec = CompositionSection.objects.create(
        page=page,
        position=0,
        layout="1col",
        enabled=True,
    )
    CompositionBlock.objects.create(
        section=sec,
        position=0,
        block_type="text",
        settings={"body": f"<p>{title} story content</p>"},
        enabled=True,
    )
    return page


def test_series_model_has_story_and_members_fields():
    """Verify Series model exposes story and members fields."""
    fields = {f.name: f for f in Series._meta.get_fields()}
    assert "story" in fields, "Series model missing 'story' ForeignKey"
    assert "members" in fields, "Series model missing 'members' JSONField"

    story_field = fields["story"]
    assert story_field.is_relation
    assert story_field.null is True
    assert story_field.blank is True

    members_field = fields["members"]
    assert members_field.get_internal_type() == "JSONField"


def test_admin_content_schema_exposes_series_entity_and_fields(admin_api_client):
    """Admin content schema exposes series with storyId and members."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    data = res.json()
    entities = data.get("entities", {})
    assert "series" in entities, "Entity 'series' missing from admin schema"

    series_schema = entities["series"]
    field_keys = {f["key"] for f in series_schema.get("fields", [])}
    assert "storyId" in field_keys, "storyId missing from series schema fields"
    assert "members" in field_keys, "members missing from series schema fields"
    assert "seoTitle" in field_keys
    assert "seoDescription" in field_keys
    assert "socialImageId" in field_keys
    assert "translationKey" in field_keys
    assert "relatedRecords" in field_keys


def test_admin_crud_series_with_members_and_story(admin_api_client):
    """Admin CRUD on series enforces story locale and article-only members."""
    story_fa = _create_story("series-story-fa", "fa", "استوری سری ۱")
    story_en = _create_story("series-story-en", "en", "Series 1 Story")

    art1 = Article.objects.create(
        locale="fa",
        slug="article-part-1",
        title="بخش اول مقاله",
        excerpt="چکیده ۱",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    art2 = Article.objects.create(
        locale="fa",
        slug="article-part-2",
        title="بخش دوم مقاله",
        excerpt="چکیده ۲",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    art_en = Article.objects.create(
        locale="en",
        slug="article-part-en",
        title="Article English Part",
        excerpt="Excerpt EN",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    proj = Project.objects.create(
        locale="fa",
        slug="project-fa",
        title="پروژه",
        objective="هدف",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    # 1. Reject story locale mismatch
    res_locale = admin_api_client.post(
        "/api/v1/admin/content/series",
        {
            "locale": "fa",
            "slug": "series-math-fa",
            "title": "سری ریاضیات",
            "status": "draft",
            "fields": {
                "storyId": story_en.pk,
            },
        },
        content_type="application/json",
    )
    assert res_locale.status_code == 400
    assert res_locale.json()["code"] == "VALIDATION"

    # 2. Reject non-article member family (contract §I05: members are ordered articles only)
    res_fam = admin_api_client.post(
        "/api/v1/admin/content/series",
        {
            "locale": "fa",
            "slug": "series-math-fa",
            "title": "سری ریاضیات",
            "status": "draft",
            "fields": {
                "members": [{"family": "project", "id": str(proj.pk), "position": 0}],
            },
        },
        content_type="application/json",
    )
    assert res_fam.status_code == 400
    assert res_fam.json()["code"] == "VALIDATION"

    # 3. Reject duplicate article members
    res_dup = admin_api_client.post(
        "/api/v1/admin/content/series",
        {
            "locale": "fa",
            "slug": "series-math-fa",
            "title": "سری ریاضیات",
            "status": "draft",
            "fields": {
                "members": [
                    {"family": "article", "id": str(art1.pk), "position": 0},
                    {"family": "article", "id": str(art1.pk), "position": 1},
                ],
            },
        },
        content_type="application/json",
    )
    assert res_dup.status_code == 400
    assert res_dup.json()["code"] == "VALIDATION"

    # 4. Reject non-existent article member
    res_missing = admin_api_client.post(
        "/api/v1/admin/content/series",
        {
            "locale": "fa",
            "slug": "series-math-fa",
            "title": "سری ریاضیات",
            "status": "draft",
            "fields": {
                "members": [{"family": "article", "id": "999999", "position": 0}],
            },
        },
        content_type="application/json",
    )
    assert res_missing.status_code == 400
    assert res_missing.json()["code"] == "VALIDATION"

    # 5. Reject article locale mismatch
    res_mismatch = admin_api_client.post(
        "/api/v1/admin/content/series",
        {
            "locale": "fa",
            "slug": "series-math-fa",
            "title": "سری ریاضیات",
            "status": "draft",
            "fields": {
                "members": [{"family": "article", "id": str(art_en.pk), "position": 0}],
            },
        },
        content_type="application/json",
    )
    assert res_mismatch.status_code == 400
    assert res_mismatch.json()["code"] == "VALIDATION"

    # 6. Create valid series with story and article members
    res_create = admin_api_client.post(
        "/api/v1/admin/content/series",
        {
            "locale": "fa",
            "slug": "series-math-fa",
            "title": "سری ریاضیات",
            "status": "draft",
            "fields": {
                "description": "توضیحات سری ریاضیات",
                "storyId": story_fa.pk,
                "members": [
                    {"family": "article", "id": str(art2.pk), "position": 1},
                    {"family": "article", "id": str(art1.pk), "position": 0},
                ],
            },
        },
        content_type="application/json",
    )
    assert res_create.status_code == 201
    created_data = res_create.json()
    series_id = created_data["id"]
    assert created_data["fields"]["storyId"] == story_fa.pk
    assert len(created_data["fields"]["members"]) == 2
    # Verify positions normalized / preserved
    assert created_data["fields"]["members"][0]["id"] == str(art1.pk)
    assert created_data["fields"]["members"][0]["position"] == 0
    assert created_data["fields"]["members"][1]["id"] == str(art2.pk)
    assert created_data["fields"]["members"][1]["position"] == 1

    # Verify M2M articles is synced
    series_obj = Series.objects.get(pk=series_id)
    assert set(series_obj.articles.values_list("pk", flat=True)) == {art1.pk, art2.pk}

    # 7. Update series: Clear story and change members
    res_update = admin_api_client.put(
        f"/api/v1/admin/content/series/{series_id}",
        {
            "fields": {
                "storyId": None,
                "members": [{"family": "article", "id": str(art1.pk), "position": 0}],
            }
        },
        content_type="application/json",
        HTTP_IF_MATCH=created_data["updatedAt"],
    )
    assert res_update.status_code == 200
    updated_data = res_update.json()
    assert updated_data["fields"]["storyId"] is None
    assert len(updated_data["fields"]["members"]) == 1
    assert updated_data["fields"]["members"][0]["id"] == str(art1.pk)

    series_obj.refresh_from_db()
    assert set(series_obj.articles.values_list("pk", flat=True)) == {art1.pk}


def test_public_series_detail_and_members():
    """Public GET /api/v1/series/{locale}/{slug} returns detail with ordered article items."""
    client = Client()
    story = _create_story("series-detail-story", "fa", "استوری سری هوش مصنوعی")
    art1 = Article.objects.create(
        locale="fa",
        slug="ai-intro",
        title="مقدمه هوش مصنوعی",
        excerpt="چکیده مقدمه",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    art2 = Article.objects.create(
        locale="fa",
        slug="ai-advanced",
        title="هوش مصنوعی پیشرفته",
        excerpt="چکیده پیشرفته",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    t_key = str(uuid.uuid4())

    series_fa = Series.objects.create(
        locale="fa",
        slug="ai-series",
        title="سری هوش مصنوعی",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        description="توضیحات سری جامع هوش مصنوعی",
        story=story,
        translation_key=t_key,
        members=[
            {"family": "article", "id": str(art1.pk), "position": 0},
            {"family": "article", "id": str(art2.pk), "position": 1},
        ],
    )
    series_fa.articles.set([art1, art2])

    Series.objects.create(
        locale="en",
        slug="ai-series-en",
        title="AI Series",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        translation_key=t_key,
    )

    # 1. Detail endpoint GET /api/v1/series/fa/ai-series
    res = client.get("/api/v1/series/fa/ai-series")
    assert res.status_code == 200
    data = res.json()
    assert data["locale"] == "fa"
    assert data["slug"] == "ai-series"
    assert data["title"] == "سری هوش مصنوعی"
    assert data["description"] == "توضیحات سری جامع هوش مصنوعی"
    assert data["story"] is not None
    assert data["story"]["title"] == "استوری سری هوش مصنوعی"
    assert len(data["story"]["sections"]) == 1

    # Alternates
    assert len(data["alternates"]) == 1
    assert data["alternates"][0]["locale"] == "en"
    assert data["alternates"][0]["slug"] == "ai-series-en"
    assert data["alternates"][0]["routeFamily"] == "blog/series"

    # SEO
    assert data["seo"] is not None
    assert data["seo"]["title"] == "سری هوش مصنوعی"
    assert data["seo"]["description"] == "توضیحات سری جامع هوش مصنوعی"

    # Ordered items
    assert len(data["items"]) == 2
    assert data["items"][0]["family"] == "article"
    assert data["items"][0]["slug"] == "ai-intro"
    assert data["items"][0]["title"] == "مقدمه هوش مصنوعی"
    assert data["items"][0]["summary"] == "چکیده مقدمه"
    assert data["items"][0]["routeFamily"] == "blog"
    assert data["items"][1]["family"] == "article"
    assert data["items"][1]["slug"] == "ai-advanced"

    # 2. Existing series list preserved
    list_res = client.get("/api/series/fa")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert any(s["slug"] == "ai-series" for s in list_data)

    # 3. Existing article filter preserved
    art_filter_res = client.get("/api/articles/fa?series=ai-series")
    assert art_filter_res.status_code == 200
    filter_data = art_filter_res.json()
    assert len(filter_data) == 2


def test_public_series_draft_isolation():
    """Draft series 404, draft story resolves to None, draft member excluded."""
    client = Client()
    draft_story = _create_story("draft-series-story", "fa", "استوری پیش‌نویس", status="draft")

    art_pub = Article.objects.create(
        locale="fa",
        slug="art-pub",
        title="مقاله عمومی",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    art_draft = Article.objects.create(
        locale="fa",
        slug="art-draft",
        title="مقاله پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )

    Series.objects.create(
        locale="fa",
        slug="published-series-draft-parts",
        title="سری با بخش‌های پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
        members=[
            {"family": "article", "id": str(art_pub.pk), "position": 0},
            {"family": "article", "id": str(art_draft.pk), "position": 1},
        ],
    )

    # 1. Draft story resolves to None, draft article excluded from items
    res = client.get("/api/v1/series/fa/published-series-draft-parts")
    assert res.status_code == 200
    data = res.json()
    assert data["story"] is None
    assert len(data["items"]) == 1
    assert data["items"][0]["slug"] == "art-pub"

    # 2. Draft series 404
    Series.objects.create(
        locale="fa",
        slug="draft-only-series",
        title="سری کاملا پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )
    res_draft = client.get("/api/v1/series/fa/draft-only-series")
    assert res_draft.status_code == 404

    # 3. Invalid locale 404
    res_inv = client.get("/api/v1/series/invalid/published-series-draft-parts")
    assert res_inv.status_code == 404
