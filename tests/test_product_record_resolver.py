"""Tests for PU-03-resolver: published graph record resolver endpoint.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I04
Endpoint: GET /api/v1/records/{locale}/resolve?refs=family:id,family:id
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    Book,
    Course,
    CreativeWork,
    Download,
    Landing,
    LifecycleStatus,
    Locale,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)


@pytest.fixture(autouse=True)
def db_access(db):
    """Ensure database access for all tests."""
    pass


def past():
    return timezone.now() - timedelta(days=1)


def future():
    return timezone.now() + timedelta(days=1)


class TestProductRecordResolver:
    """Test suite for GET /api/v1/records/{locale}/resolve."""

    def test_record_resolver_endpoint_exists(self):
        """Focused failing test before implementation: route exists and answers."""
        client = Client()
        response = client.get("/api/v1/records/fa/resolve")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "unresolved" in data

    def test_resolve_published_records_in_request_order(self):
        """Published records resolve to WorkRef descriptors preserving request order."""
        client = Client()
        art = Article.objects.create(
            locale=Locale.FA,
            slug="article-alpha",
            title="Article Alpha",
            excerpt="Excerpt for alpha",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )
        proj = Project.objects.create(
            locale=Locale.FA,
            slug="project-beta",
            title="Project Beta",
            objective="Objective for beta",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )
        pub = Publication.objects.create(
            locale=Locale.FA,
            slug="publication-gamma",
            title="Publication Gamma",
            abstract="Abstract for gamma",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )

        # Request in order: project, article, publication
        refs = f"project:{proj.pk},article:{art.pk},publication:{pub.pk}"
        response = client.get(f"/api/v1/records/fa/resolve?refs={refs}")
        assert response.status_code == 200
        data = response.json()

        assert data["unresolved"] == []
        items = data["items"]
        assert len(items) == 3

        # Preserve request order
        assert items[0]["family"] == "project"
        assert items[0]["id"] == str(proj.pk)
        assert items[0]["locale"] == "fa"
        assert items[0]["slug"] == "project-beta"
        assert items[0]["title"] == "Project Beta"
        assert items[0]["summary"] == "Objective for beta"
        assert items[0]["routeFamily"] == "projects"
        assert items[0].get("courseSlug") is None

        assert items[1]["family"] == "article"
        assert items[1]["id"] == str(art.pk)
        assert items[1]["locale"] == "fa"
        assert items[1]["slug"] == "article-alpha"
        assert items[1]["title"] == "Article Alpha"
        assert items[1]["summary"] == "Excerpt for alpha"
        assert items[1]["routeFamily"] == "blog"
        assert items[1].get("courseSlug") is None

        assert items[2]["family"] == "publication"
        assert items[2]["id"] == str(pub.pk)
        assert items[2]["locale"] == "fa"
        assert items[2]["slug"] == "publication-gamma"
        assert items[2]["title"] == "Publication Gamma"
        assert items[2]["summary"] == "Abstract for gamma"
        assert items[2]["routeFamily"] == "publications"
        assert items[2].get("courseSlug") is None

    def test_resolve_exact_locale_isolation(self):
        """English record does not resolve when requesting Persian locale."""
        client = Client()
        art_en = Article.objects.create(
            locale=Locale.EN,
            slug="article-en",
            title="Article English",
            excerpt="English excerpt",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )

        response = client.get(f"/api/v1/records/fa/resolve?refs=article:{art_en.pk}")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["unresolved"] == [{"family": "article", "id": str(art_en.pk)}]

    def test_resolve_draft_and_scheduled_and_archived_exclusion(self):
        """Unpublished/draft/scheduled records land in unresolved without distinction."""
        client = Client()
        draft_art = Article.objects.create(
            locale=Locale.FA,
            slug="draft-art",
            title="Draft Article",
            status=LifecycleStatus.DRAFT,
        )
        scheduled_proj = Project.objects.create(
            locale=Locale.FA,
            slug="scheduled-proj",
            title="Scheduled Project",
            status=LifecycleStatus.SCHEDULED,
            published_at=future(),
        )
        archived_pub = Publication.objects.create(
            locale=Locale.FA,
            slug="archived-pub",
            title="Archived Publication",
            status=LifecycleStatus.ARCHIVED,
            published_at=past(),
        )

        refs = f"article:{draft_art.pk},project:{scheduled_proj.pk},publication:{archived_pub.pk}"
        response = client.get(f"/api/v1/records/fa/resolve?refs={refs}")
        assert response.status_code == 200
        data = response.json()

        assert data["items"] == []
        assert data["unresolved"] == [
            {"family": "article", "id": str(draft_art.pk)},
            {"family": "project", "id": str(scheduled_proj.pk)},
            {"family": "publication", "id": str(archived_pub.pk)},
        ]

    def test_resolve_missing_records(self):
        """Missing records land in unresolved without distinction."""
        client = Client()
        response = client.get("/api/v1/records/fa/resolve?refs=article:99999,book:88888")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["unresolved"] == [
            {"family": "article", "id": "99999"},
            {"family": "book", "id": "88888"},
        ]

    def test_resolve_mixed_preserves_request_order(self):
        """Mixed resolved and unresolved records preserve request order in their lists."""
        client = Client()
        art1 = Article.objects.create(
            locale=Locale.FA,
            slug="art-1",
            title="Art 1",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )
        art2 = Article.objects.create(
            locale=Locale.FA,
            slug="art-2",
            title="Art 2",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )

        refs = f"article:{art1.pk},article:99999,article:{art2.pk},talk:88888"
        response = client.get(f"/api/v1/records/fa/resolve?refs={refs}")
        assert response.status_code == 200
        data = response.json()

        assert [item["id"] for item in data["items"]] == [str(art1.pk), str(art2.pk)]
        assert data["unresolved"] == [
            {"family": "article", "id": "99999"},
            {"family": "talk", "id": "88888"},
        ]

    def test_resolve_deduplication(self):
        """Duplicate references in the query string are deduplicated preserving first occurrence."""
        client = Client()
        art = Article.objects.create(
            locale=Locale.FA,
            slug="art-dedup",
            title="Art Dedup",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )
        proj = Project.objects.create(
            locale=Locale.FA,
            slug="proj-dedup",
            title="Proj Dedup",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )

        refs = f"article:{art.pk},project:{proj.pk},article:{art.pk}"
        response = client.get(f"/api/v1/records/fa/resolve?refs={refs}")
        assert response.status_code == 200
        data = response.json()

        assert len(data["items"]) == 2
        assert [item["id"] for item in data["items"]] == [str(art.pk), str(proj.pk)]

    def test_resolve_all_supported_families(self):
        """Verify all 13 supported families resolve to correct routeFamily and summary."""
        client = Client()
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.media.models import Media

        test_media = Media.objects.create(
            file=SimpleUploadedFile("test.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
            title="Test PDF",
            is_active=True,
        )

        records = [
            (
                "landing",
                Landing.objects.create(
                    locale=Locale.FA,
                    slug="home",
                    title="Home Landing",
                    seo_description="Landing summary",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "home",
                "Landing summary",
            ),
            (
                "profile",
                Profile.objects.create(
                    locale=Locale.FA,
                    slug="about",
                    title="About Profile",
                    short_bio="Profile bio",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "about",
                "Profile bio",
            ),
            (
                "article",
                Article.objects.create(
                    locale=Locale.FA,
                    slug="test-art",
                    title="Test Article",
                    excerpt="Article excerpt",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "blog",
                "Article excerpt",
            ),
            (
                "series",
                Series.objects.create(
                    locale=Locale.FA,
                    slug="test-series",
                    title="Test Series",
                    description="Series description",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "blog/series",
                "Series description",
            ),
            (
                "researchtopic",
                ResearchTopic.objects.create(
                    locale=Locale.FA,
                    slug="test-topic",
                    title="Test Topic",
                    summary="Topic summary",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "research",
                "Topic summary",
            ),
            (
                "researchstatement",
                ResearchStatement.objects.create(
                    locale=Locale.FA,
                    slug="test-stmt",
                    title="Test Statement",
                    body="<p>Statement body</p>",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "research/statements",
                "Statement body",
            ),
            (
                "project",
                Project.objects.create(
                    locale=Locale.FA,
                    slug="test-proj",
                    title="Test Project",
                    objective="Project objective",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "projects",
                "Project objective",
            ),
            (
                "publication",
                Publication.objects.create(
                    locale=Locale.FA,
                    slug="test-pub",
                    title="Test Publication",
                    abstract="Pub abstract",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "publications",
                "Pub abstract",
            ),
            (
                "book",
                Book.objects.create(
                    locale=Locale.FA,
                    slug="test-book",
                    title="Test Book",
                    description="Book description",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "books",
                "Book description",
            ),
            (
                "talk",
                Talk.objects.create(
                    locale=Locale.FA,
                    slug="test-talk",
                    title="Test Talk",
                    abstract="Talk abstract",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "talks",
                "Talk abstract",
            ),
            (
                "download",
                Download.objects.create(
                    locale=Locale.FA,
                    slug="test-dl",
                    title="Test Download",
                    description="Download description",
                    media=test_media,
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "resources",
                "Download description",
            ),
            (
                "course",
                Course.objects.create(
                    locale=Locale.FA,
                    slug="test-course",
                    title="Test Course",
                    description="Course description",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "education",
                "Course description",
            ),
            (
                "creativework",
                CreativeWork.objects.create(
                    locale=Locale.FA,
                    slug="test-cw",
                    title="Test Creative Work",
                    description="Work description",
                    status=LifecycleStatus.PUBLISHED,
                    published_at=past(),
                ),
                "gallery",
                "Work description",
            ),
        ]

        refs = ",".join(f"{family}:{obj.pk}" for family, obj, _, _ in records)
        response = client.get(f"/api/v1/records/fa/resolve?refs={refs}")
        assert response.status_code == 200
        data = response.json()
        assert data["unresolved"] == []
        assert len(data["items"]) == len(records)

        for i, (expected_family, obj, expected_route_family, expected_summary) in enumerate(
            records
        ):
            item = data["items"][i]
            assert item["family"] == expected_family
            assert item["id"] == str(obj.pk)
            assert item["routeFamily"] == expected_route_family
            assert item["summary"] == expected_summary
            assert item["title"] == obj.title
            assert item["slug"] == obj.slug

    def test_resolve_fallback_summary_to_title(self):
        """When optional summary/excerpt/abstract is blank, falls back to title."""
        client = Client()
        art = Article.objects.create(
            locale=Locale.FA,
            slug="no-excerpt",
            title="No Excerpt Title",
            excerpt="",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )
        response = client.get(f"/api/v1/records/fa/resolve?refs=article:{art.pk}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["summary"] == "No Excerpt Title"

    @pytest.mark.parametrize(
        "bad_ref",
        [
            "article",
            "article:1:extra",
            "article:",
            ":1",
            "article:abc",
            "article:-1",
            "article:0",
            "article:01",
            "article:0001",
            "article:1,,article:2",
            "article:1,",
            ",article:1",
            ",",
            "unknownfamily:1",
        ],
    )
    def test_resolve_malformed_input_returns_400(self, bad_ref):
        """Malformed reference query parameter returns 400 with error envelope."""
        client = Client()
        response = client.get(f"/api/v1/records/fa/resolve?refs={bad_ref}")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert "field_errors" in data
        assert "request_id" in data

    def test_resolve_more_than_50_references_returns_400(self):
        """More than 50 unique references returns 400 with error envelope."""
        client = Client()
        refs = ",".join(f"article:{i}" for i in range(1, 52))
        response = client.get(f"/api/v1/records/fa/resolve?refs={refs}")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert "field_errors" in data
        assert "request_id" in data

    def test_resolve_empty_refs_returns_empty_results(self):
        """Empty or absent refs parameter returns 200 with empty items and unresolved."""
        client = Client()
        response = client.get("/api/v1/records/fa/resolve")
        assert response.status_code == 200
        assert response.json() == {"items": [], "unresolved": []}

        response2 = client.get("/api/v1/records/fa/resolve?refs=")
        assert response2.status_code == 200
        assert response2.json() == {"items": [], "unresolved": []}

    def test_resolve_invalid_locale_returns_404(self):
        """Non-supported locale returns 404 with error envelope."""
        client = Client()
        response = client.get("/api/v1/records/fr/resolve?refs=article:1")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"
        assert "field_errors" in data
        assert "request_id" in data

    def test_regression_superscript_two_returns_400_not_500(self):
        """U+00B2 (superscript two) must return controlled HTTP 400, not ValueError 500."""
        client = Client()
        response = client.get("/api/v1/records/fa/resolve?refs=article:\u00b2")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert "\u00b2" not in data["message"]
        assert "field_errors" in data
        assert "request_id" in data and len(data["request_id"]) > 0

    def test_regression_overlong_5000_digits_returns_400_not_500(self):
        """5000 ASCII digits must return controlled HTTP 400, not ValueError 500."""
        client = Client()
        bad_id = "9" * 5000
        response = client.get(f"/api/v1/records/fa/resolve?refs=article:{bad_id}")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert bad_id not in data["message"]
        assert "field_errors" in data
        assert "request_id" in data and len(data["request_id"]) > 0

    def test_regression_non_ascii_digits_returns_400(self):
        """Non-ASCII digits (e.g. Persian numerals) must be rejected with 400."""
        client = Client()
        response = client.get("/api/v1/records/fa/resolve?refs=article:\u06f1\u06f2\u06f3")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert "\u06f1\u06f2\u06f3" not in data["message"]
        assert "request_id" in data

    def test_regression_leading_zeros_rejected_with_400(self):
        """Noncanonical leading zeros must be rejected with 400, even when record exists."""
        client = Client()
        art = Article.objects.create(
            locale=Locale.FA,
            slug="test-leading-zero",
            title="Test Leading Zero",
            status=LifecycleStatus.PUBLISHED,
            published_at=past(),
        )
        response = client.get(f"/api/v1/records/fa/resolve?refs=article:000{art.pk}")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert "request_id" in data

        response_01 = client.get("/api/v1/records/fa/resolve?refs=article:01")
        assert response_01.status_code == 400
        assert response_01.json()["code"] == "INVALID_INPUT"

    def test_regression_out_of_range_id_returns_400(self):
        """ID exceeding signed 64-bit integer range must return controlled 400."""
        client = Client()
        # 2**63 - 1 = 9223372036854775807 (19 digits); 9223372036854775808 is out of range
        response = client.get("/api/v1/records/fa/resolve?refs=article:9223372036854775808")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_INPUT"
        assert "request_id" in data

        # 20 digits ID
        response_20 = client.get("/api/v1/records/fa/resolve?refs=article:10000000000000000000")
        assert response_20.status_code == 400
        assert response_20.json()["code"] == "INVALID_INPUT"

    def test_error_envelope_structure_on_400_and_404(self):
        """Error responses must conform to PRODUCT-INTERFACES-V2 §I08 / ERROR-CONTRACT."""
        client = Client()
        # 400 error envelope
        res_400 = client.get("/api/v1/records/fa/resolve?refs=article:bad")
        assert res_400.status_code == 400
        data_400 = res_400.json()
        assert data_400["code"] == "INVALID_INPUT"
        assert isinstance(data_400["message"], str) and len(data_400["message"]) > 0
        assert isinstance(data_400["field_errors"], dict)
        assert isinstance(data_400["request_id"], str) and len(data_400["request_id"]) > 0
        assert "article:bad" not in data_400["message"]

        # 404 error envelope
        res_404 = client.get("/api/v1/records/fr/resolve?refs=article:1")
        assert res_404.status_code == 404
        data_404 = res_404.json()
        assert data_404["code"] == "NOT_FOUND"
        assert isinstance(data_404["message"], str) and len(data_404["message"]) > 0
        assert isinstance(data_404["field_errors"], dict)
        assert isinstance(data_404["request_id"], str) and len(data_404["request_id"]) > 0

    def test_resolve_schema_in_openapi(self):
        """The resolve endpoint is registered in OpenAPI with 200, 400, and 404 responses."""
        from apps.api.api import api

        schema = api.get_openapi_schema()
        paths = schema.get("paths", {})
        assert "/api/v1/records/{locale}/resolve" in paths
        get_op = paths["/api/v1/records/{locale}/resolve"].get("get")
        assert get_op is not None
        responses = get_op.get("responses", {})
        assert 200 in responses or "200" in responses
        assert 400 in responses or "400" in responses
        assert 404 in responses or "404" in responses
