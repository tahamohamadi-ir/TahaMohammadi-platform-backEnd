"""Tests for PU-04-metadata: shared SEO, explicit translation identity, and related records.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-metadata.md
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.content.models import (
    Article,
    Book,
    Course,
    CreativeWork,
    Download,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Talk,
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


@pytest.fixture
def sample_image(db, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    media = Media.objects.create(
        file=SimpleUploadedFile(
            "cover.jpg", b"\xff\xd8\xff\xe0 test image", content_type="image/jpeg"
        ),
        title="Cover Image",
        mime="image/jpeg",
        is_active=True,
    )
    return media


class TestProductMetadata:
    """Test suite for PU-04-metadata fields, admin CRUD, and public projections."""

    def test_models_have_metadata_fields(self):
        """Initial failing gap: content models must possess the publication metadata fields."""
        models_to_check = [
            Article,
            Project,
            ResearchTopic,
            ResearchStatement,
            Publication,
            Book,
            Talk,
            Download,
            Course,
            CreativeWork,
        ]
        expected_fields = [
            "seo_title",
            "seo_description",
            "social_image",
            "translation_key",
            "related_records",
        ]
        for model in models_to_check:
            field_names = {f.name for f in model._meta.get_fields()}
            for exp in expected_fields:
                assert exp in field_names, f"Model {model.__name__} missing field '{exp}'"

    def test_admin_content_schema_exposes_metadata_fields(self, admin_api_client):
        """Admin content schema must declare the new metadata fields for entities."""
        res = admin_api_client.get("/api/v1/admin/content/schema")
        assert res.status_code == 200
        entities = res.json()["entities"]
        assert "article" in entities
        article_keys = {f["key"] for f in entities["article"]["fields"]}
        assert "seoTitle" in article_keys
        assert "seoDescription" in article_keys
        assert "socialImageId" in article_keys
        assert "translationKey" in article_keys
        assert "relatedRecords" in article_keys

    def test_admin_put_and_get_metadata_fields(self, admin_api_client, sample_image):
        """Admin can save and retrieve metadata fields under optimistic locking."""
        article = Article.objects.create(
            title="مقاله تست متادیتا",
            slug="test-metadata-article",
            locale="fa",
            status="draft",
        )
        t_key = str(uuid.uuid4())
        related_data = [{"family": "project", "id": "1"}]

        # Detail read before update
        res = admin_api_client.get(f"/api/v1/admin/content/article/{article.pk}")
        assert res.status_code == 200
        updated_at = res.headers.get("ETag") or res.json()["updatedAt"]

        # PUT update with If-Match
        put_res = admin_api_client.put(
            f"/api/v1/admin/content/article/{article.pk}",
            data={
                "fields": {
                    "seoTitle": "عنوان سئو تستی",
                    "seoDescription": "توضیحات متادیتا برای جستجو",
                    "socialImageId": sample_image.pk,
                    "translationKey": t_key,
                    "relatedRecords": related_data,
                }
            },
            content_type="application/json",
            HTTP_IF_MATCH=f'"{updated_at}"',
        )
        assert put_res.status_code == 200
        fields = put_res.json()["fields"]
        assert fields["seoTitle"] == "عنوان سئو تستی"
        assert fields["seoDescription"] == "توضیحات متادیتا برای جستجو"
        assert fields["socialImageId"] == sample_image.pk
        assert fields["translationKey"] == t_key
        assert fields["relatedRecords"] == related_data

    def test_admin_validates_related_records(self, admin_api_client):
        """Admin rejects malformed related records (bad family, malformed ID, or extra keys)."""
        article = Article.objects.create(
            title="مقاله تست اعتبارسنجی",
            slug="test-validation-article",
            locale="fa",
            status="draft",
        )
        res = admin_api_client.get(f"/api/v1/admin/content/article/{article.pk}")
        updated_at = res.headers.get("ETag") or res.json()["updatedAt"]

        # Reject unknown family
        put_res = admin_api_client.put(
            f"/api/v1/admin/content/article/{article.pk}",
            data={"fields": {"relatedRecords": [{"family": "invalid_family", "id": "1"}]}},
            content_type="application/json",
            HTTP_IF_MATCH=f'"{updated_at}"',
        )
        assert put_res.status_code == 400
        assert "family" in put_res.json()["message"].lower()

        # Reject non-canonical ID (leading zero)
        put_res = admin_api_client.put(
            f"/api/v1/admin/content/article/{article.pk}",
            data={"fields": {"relatedRecords": [{"family": "project", "id": "01"}]}},
            content_type="application/json",
            HTTP_IF_MATCH=f'"{updated_at}"',
        )
        assert put_res.status_code == 400
        assert "canonical" in put_res.json()["message"].lower()

    def test_public_article_detail_seo_fallback(self, client):
        """Public projection of seo falls back to title and summary when seo fields are empty."""
        article = Article.objects.create(
            title="مقاله بدون سئو اختصاصی",
            slug="article-no-seo",
            locale="fa",
            status="published",
            published_at=timezone.now(),
            excerpt="خلاصه پیش‌فرض برای سئو",
        )
        res = client.get(f"/api/articles/fa/{article.slug}")
        assert res.status_code == 200
        data = res.json()
        assert "seo" in data
        assert data["seo"]["title"] == "مقاله بدون سئو اختصاصی"
        assert data["seo"]["description"] == "خلاصه پیش‌فرض برای سئو"

    def test_public_article_detail_alternates(self, client):
        """Public alternates resolved via explicit translationKey matching published siblings."""
        t_key = uuid.uuid4()
        fa_article = Article.objects.create(
            title="مقاله فارسی",
            slug="article-fa",
            locale="fa",
            status="published",
            published_at=timezone.now(),
            translation_key=t_key,
        )
        en_article = Article.objects.create(
            title="English Article",
            slug="article-en",
            locale="en",
            status="published",
            published_at=timezone.now(),
            translation_key=t_key,
        )
        Article.objects.create(
            title="Draft Alternate",
            slug="article-draft",
            locale="en",
            status="draft",
            translation_key=t_key,
        )

        res = client.get(f"/api/articles/fa/{fa_article.slug}")
        assert res.status_code == 200
        alternates = res.json().get("alternates", [])
        assert len(alternates) == 1
        assert alternates[0]["locale"] == "en"
        assert alternates[0]["slug"] == en_article.slug
        assert alternates[0]["routeFamily"] == "blog"

    def test_public_article_detail_related_records_resolution(self, client):
        """Public relatedRecords resolves published same-locale records, omits drafts/missing."""
        pub_project = Project.objects.create(
            title="پروژه مرتبط",
            slug="related-project",
            locale="fa",
            status="published",
            published_at=timezone.now(),
            objective="هدف پروژه برای خلاصه",
        )
        draft_project = Project.objects.create(
            title="پروژه پیش‌نویس",
            slug="draft-project",
            locale="fa",
            status="draft",
        )
        en_project = Project.objects.create(
            title="English Project",
            slug="en-project",
            locale="en",
            status="published",
            published_at=timezone.now(),
        )

        article = Article.objects.create(
            title="مقاله با موارد مرتبط",
            slug="article-with-related",
            locale="fa",
            status="published",
            published_at=timezone.now(),
            related_records=[
                {"family": "project", "id": str(pub_project.pk)},
                # Unpublished: should be omitted
                {"family": "project", "id": str(draft_project.pk)},
                # Different locale: should be omitted
                {"family": "project", "id": str(en_project.pk)},
                # Non-existent: should be omitted
                {"family": "project", "id": "999999"},
            ],
        )

        res = client.get(f"/api/articles/fa/{article.slug}")
        assert res.status_code == 200
        related = res.json().get("relatedRecords", [])
        assert len(related) == 1
        assert related[0]["family"] == "project"
        assert related[0]["id"] == str(pub_project.pk)
        assert related[0]["slug"] == pub_project.slug
        assert related[0]["title"] == pub_project.title
        assert related[0]["summary"] == "هدف پروژه برای خلاصه"
        assert related[0]["routeFamily"] == "projects"
