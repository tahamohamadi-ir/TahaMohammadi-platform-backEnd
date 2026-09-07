"""Tests for PU-06-collection: collection detail and ordered public membership.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I05
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-collection.md
"""

from __future__ import annotations

import datetime
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
    Collection,
    LifecycleStatus,
    Project,
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
        settings={"body": "<p>Collection story body.</p>"},
        enabled=True,
    )
    return page


def test_collection_model_has_story_and_members_fields():
    """Collection model must define story and members fields."""
    fields = {f.name: f for f in Collection._meta.get_fields()}
    assert "story" in fields
    assert fields["story"].is_relation
    assert fields["story"].related_model == CompositionPage
    assert "members" in fields


def test_admin_content_schema_exposes_collection_entity_and_fields(admin_api_client):
    """Admin schema must expose collection entity with storyId and members."""
    res = admin_api_client.get("/api/v1/admin/content/schema")
    assert res.status_code == 200
    entities = res.json()["entities"]
    assert "collection" in entities
    collection_schema = entities["collection"]
    field_keys = [f["key"] for f in collection_schema["fields"]]
    assert "storyId" in field_keys
    assert "members" in field_keys
    assert "curatorName" in field_keys
    assert "criteria" in field_keys
    assert "curatedDate" in field_keys


def test_admin_crud_collection_with_members_and_story(admin_api_client):
    """Admin can create and update collection with validated members and storyId."""
    story_fa = _create_story("coll-story-fa", "fa", "استوری مجموعه")
    story_en = _create_story("coll-story-en", "en", "Collection Story EN")

    article = Article.objects.create(
        locale="fa",
        slug="article-in-coll",
        title="مقاله در مجموعه",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    project = Project.objects.create(
        locale="fa",
        slug="project-in-coll",
        title="پروژه در مجموعه",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    article_en = Article.objects.create(
        locale="en",
        slug="article-en-in-coll",
        title="English Article",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )

    # 1. Validation: story locale mismatch
    bad_res1 = admin_api_client.post(
        "/api/v1/admin/content/collection",
        {
            "locale": "fa",
            "slug": "bad-story-coll",
            "title": "مجموعه نامعتبر",
            "status": "draft",
            "fields": {
                "storyId": story_en.pk,
            },
        },
        content_type="application/json",
    )
    assert bad_res1.status_code == 400
    assert bad_res1.json()["code"] == "VALIDATION"

    # 2. Validation: member unknown family
    bad_res2 = admin_api_client.post(
        "/api/v1/admin/content/collection",
        {
            "locale": "fa",
            "slug": "bad-member-family",
            "title": "مجموعه عضو نامعتبر",
            "status": "draft",
            "fields": {
                "members": [{"family": "unknown_family", "id": "1", "position": 0}],
            },
        },
        content_type="application/json",
    )
    assert bad_res2.status_code == 400
    assert bad_res2.json()["code"] == "VALIDATION"

    # 3. Validation: member duplicate
    bad_res3 = admin_api_client.post(
        "/api/v1/admin/content/collection",
        {
            "locale": "fa",
            "slug": "dup-member-coll",
            "title": "مجموعه عضو تکراری",
            "status": "draft",
            "fields": {
                "members": [
                    {"family": "article", "id": str(article.pk), "position": 0},
                    {"family": "article", "id": str(article.pk), "position": 1},
                ],
            },
        },
        content_type="application/json",
    )
    assert bad_res3.status_code == 400
    assert bad_res3.json()["code"] == "VALIDATION"

    # 4. Validation: member does not exist
    bad_res4 = admin_api_client.post(
        "/api/v1/admin/content/collection",
        {
            "locale": "fa",
            "slug": "missing-member-coll",
            "title": "مجموعه عضو ناموجود",
            "status": "draft",
            "fields": {
                "members": [{"family": "article", "id": "9999999", "position": 0}],
            },
        },
        content_type="application/json",
    )
    assert bad_res4.status_code == 400
    assert bad_res4.json()["code"] == "VALIDATION"

    # 5. Validation: member locale mismatch
    bad_res5 = admin_api_client.post(
        "/api/v1/admin/content/collection",
        {
            "locale": "fa",
            "slug": "locale-mismatch-coll",
            "title": "مجموعه ناهمخوانی زبان عضو",
            "status": "draft",
            "fields": {
                "members": [{"family": "article", "id": str(article_en.pk), "position": 0}],
            },
        },
        content_type="application/json",
    )
    assert bad_res5.status_code == 400
    assert bad_res5.json()["code"] == "VALIDATION"

    # 6. Create valid collection with story and members
    create_res = admin_api_client.post(
        "/api/v1/admin/content/collection",
        {
            "locale": "fa",
            "slug": "ai-research-curation",
            "title": "مجموعه پژوهش‌های هوش مصنوعی",
            "status": "draft",
            "fields": {
                "description": "مجموعه‌ای گزیده از مقالات و پروژه‌ها",
                "curatorName": "طاها محمدی",
                "curatorTitle": "پژوهشگر ارشد",
                "criteria": "کیفیت بالا و نوآوری در روش",
                "curatedDate": "2026-09-01",
                "storyId": story_fa.pk,
                "members": [
                    {"family": "article", "id": str(article.pk), "position": 0},
                    {"family": "project", "id": str(project.pk), "position": 1},
                ],
            },
        },
        content_type="application/json",
    )
    assert create_res.status_code == 201
    coll_data = create_res.json()
    coll_id = coll_data["id"]
    assert coll_data["fields"]["storyId"] == story_fa.pk
    assert len(coll_data["fields"]["members"]) == 2
    assert coll_data["fields"]["curatorName"] == "طاها محمدی"

    # 7. Validation: Cycle rejection on update (collection containing itself)
    cycle_res = admin_api_client.put(
        f"/api/v1/admin/content/collection/{coll_id}",
        {
            "fields": {
                "members": [{"family": "collection", "id": str(coll_id), "position": 0}],
            }
        },
        content_type="application/json",
        HTTP_IF_MATCH=coll_data["updatedAt"],
    )
    assert cycle_res.status_code == 400
    assert cycle_res.json()["code"] == "VALIDATION"

    # 8. Update: Clear story and change members
    update_res = admin_api_client.put(
        f"/api/v1/admin/content/collection/{coll_id}",
        {
            "fields": {
                "storyId": None,
                "members": [{"family": "project", "id": str(project.pk), "position": 0}],
            }
        },
        content_type="application/json",
        HTTP_IF_MATCH=coll_data["updatedAt"],
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["fields"]["storyId"] is None
    assert len(updated_data["fields"]["members"]) == 1
    assert updated_data["fields"]["members"][0]["family"] == "project"


def test_public_collections_list_and_detail():
    """Public GET /api/v1/collections/{locale} and /{slug} expose card and detail."""
    client = Client()
    story = _create_story("deep-learning-coll-story", "fa", "استوری یادگیری عمیق")
    article = Article.objects.create(
        locale="fa",
        slug="dl-fundamentals",
        title="مبانی یادگیری عمیق",
        excerpt="چکیده مبانی",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    project = Project.objects.create(
        locale="fa",
        slug="dl-framework",
        title="فریم‌ورک یادگیری عمیق",
        objective="اهداف پروژه",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    t_key = str(uuid.uuid4())

    Collection.objects.create(
        locale="fa",
        slug="curated-dl",
        title="مجموعه یادگیری عمیق",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        description="توضیحات مجموعه",
        curator_name="طاها محمدی",
        curator_title="مدیر علمی",
        criteria="معیارهای انتخاب",
        curated_date=datetime.date(2026, 8, 15),
        story=story,
        translation_key=t_key,
        members=[
            {"family": "article", "id": str(article.pk), "position": 0},
            {"family": "project", "id": str(project.pk), "position": 1},
        ],
    )
    Collection.objects.create(
        locale="en",
        slug="curated-dl-en",
        title="Curated Deep Learning",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        translation_key=t_key,
    )

    # 1. Public list
    list_res = client.get("/api/v1/collections/fa")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["count"] >= 1
    card = next(c for c in list_data["items"] if c["slug"] == "curated-dl")
    assert card["title"] == "مجموعه یادگیری عمیق"
    assert card["curatorName"] == "طاها محمدی"
    assert card["criteria"] == "معیارهای انتخاب"
    assert card["curatedDate"] == "2026-08-15"
    assert "story" not in card
    assert "items" not in card

    # Alternates on card
    assert len(card["alternates"]) == 1
    assert card["alternates"][0]["locale"] == "en"
    assert card["alternates"][0]["slug"] == "curated-dl-en"
    assert card["alternates"][0]["routeFamily"] == "collections"

    # 2. Public detail
    detail_res = client.get("/api/v1/collections/fa/curated-dl")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["slug"] == "curated-dl"
    assert detail["curatorName"] == "طاها محمدی"
    assert detail["story"] is not None
    assert detail["story"]["title"] == "استوری یادگیری عمیق"
    assert len(detail["story"]["sections"]) == 1

    # Ordered public membership items
    assert len(detail["items"]) == 2
    assert detail["items"][0]["family"] == "article"
    assert detail["items"][0]["slug"] == "dl-fundamentals"
    assert detail["items"][0]["routeFamily"] == "blog"
    assert detail["items"][1]["family"] == "project"
    assert detail["items"][1]["slug"] == "dl-framework"
    assert detail["items"][1]["routeFamily"] == "projects"


def test_public_collection_draft_isolation():
    """Draft collections and draft members are omitted from public projections."""
    client = Client()

    draft_article = Article.objects.create(
        locale="fa",
        slug="draft-member-article",
        title="مقاله پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )
    pub_article = Article.objects.create(
        locale="fa",
        slug="pub-member-article",
        title="مقاله منتشر شده",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    draft_story = _create_story("draft-coll-story", "fa", "استوری پیش‌نویس", status="draft")

    pub_coll = Collection.objects.create(
        locale="fa",
        slug="coll-with-drafts",
        title="مجموعه با اعضای پیش‌نویس",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        story=draft_story,
        members=[
            {"family": "article", "id": str(draft_article.pk), "position": 0},
            {"family": "article", "id": str(pub_article.pk), "position": 1},
        ],
    )

    detail_res = client.get(f"/api/v1/collections/fa/{pub_coll.slug}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    # Draft story resolves to None
    assert detail["story"] is None
    # Draft member is filtered out
    assert len(detail["items"]) == 1
    assert detail["items"][0]["slug"] == "pub-member-article"

    # Draft collection returns 404
    draft_coll = Collection.objects.create(
        locale="fa",
        slug="draft-coll",
        title="مجموعه پیش‌نویس",
        status=LifecycleStatus.DRAFT,
    )
    assert client.get(f"/api/v1/collections/fa/{draft_coll.slug}").status_code == 404

    # Unsupported locale returns 404
    assert client.get("/api/v1/collections/fr").status_code == 404
