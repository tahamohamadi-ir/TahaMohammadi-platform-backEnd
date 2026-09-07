"""Tests for PU-04-catalog: typed code, table, file, references, and related story blocks.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-catalog.md
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.composition.blocks import (
    KIND_STORY,
    BlockValidationError,
    validate_block_settings,
)
from apps.composition.models import (
    CompositionBlock,
    CompositionPage,
    CompositionSection,
)
from apps.composition.projection import public_story_document
from apps.content.models import Article, Download, PublicationSnapshot
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
def sample_download(db, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    media = Media.objects.create(
        file=SimpleUploadedFile("doc.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
        title="Sample Document",
        mime="application/pdf",
        is_active=True,
    )
    download = Download.objects.create(
        title="Sample Download",
        slug="sample-download",
        locale="fa",
        media=media,
        status="published",
        published_at=timezone.now() - timedelta(hours=1),
        access_state="public",
    )
    return download


class TestProductStoryBlocks:
    """Test suite for PU-04-catalog additions to the story composition catalog."""

    def test_story_schema_contains_new_block_types(self, admin_api_client):
        """Initial failing gap: /schema?kind=story must expose
        code, table, file, references, related.
        """
        res = admin_api_client.get("/api/v1/admin/composition/schema?kind=story")
        assert res.status_code == 200
        types = {b["type"]: b for b in res.json()["blockTypes"]}
        assert "code" in types, "code block missing from story catalog"
        assert "table" in types, "table block missing from story catalog"
        assert "file" in types, "file block missing from story catalog"
        assert "references" in types, "references block missing from story catalog"
        assert "related" in types, "related block missing from story catalog"

    def test_code_block_validation(self):
        """code block: requires code and language; optional caption;
        100k char limit; rejects unknown keys.
        """
        # Valid code block
        validate_block_settings(
            "code",
            {"code": "print('hello')", "language": "python", "caption": "Greeting"},
            kind=KIND_STORY,
        )

        # Missing required language
        with pytest.raises(BlockValidationError, match="Missing required setting 'language'"):
            validate_block_settings("code", {"code": "print(1)"}, kind=KIND_STORY)

        # Missing required code
        with pytest.raises(BlockValidationError, match="Missing required setting 'code'"):
            validate_block_settings("code", {"language": "python"}, kind=KIND_STORY)

        # Rejects unknown key
        with pytest.raises(BlockValidationError, match="Unknown setting key"):
            validate_block_settings(
                "code",
                {"code": "x = 1", "language": "python", "unknownKey": "val"},
                kind=KIND_STORY,
            )

        # Rejects code exceeding 100k chars
        too_long_code = "a" * 100_001
        with pytest.raises(BlockValidationError, match="100000"):
            validate_block_settings(
                "code",
                {"code": too_long_code, "language": "python"},
                kind=KIND_STORY,
            )

    def test_table_block_validation(self):
        """table block: caption, columns, rows; max 20 columns, max 500 rows; unique column keys."""
        valid_table = {
            "caption": "مقایسه عملکرد",
            "columns": [
                {"key": "model", "label": "مدل"},
                {"key": "acc", "label": "دقت"},
            ],
            "rows": [
                {"model": "BERT", "acc": "92.4%"},
                {"model": "GPT", "acc": "95.1%"},
            ],
        }
        validate_block_settings("table", valid_table, kind=KIND_STORY)

        # Reject > 20 columns
        many_cols = [{"key": f"c{i}", "label": f"L{i}"} for i in range(21)]
        with pytest.raises(BlockValidationError, match="at most 20 columns"):
            validate_block_settings(
                "table",
                {"columns": many_cols, "rows": []},
                kind=KIND_STORY,
            )

        # Reject duplicate column keys
        dup_cols = [
            {"key": "col1", "label": "L1"},
            {"key": "col1", "label": "L2"},
        ]
        with pytest.raises(BlockValidationError, match="Duplicate column key"):
            validate_block_settings(
                "table",
                {"columns": dup_cols, "rows": []},
                kind=KIND_STORY,
            )

        # Reject row with unknown column key
        bad_row_table = {
            "columns": [{"key": "c1", "label": "L1"}],
            "rows": [{"c1": "v1", "unknownCol": "v2"}],
        }
        with pytest.raises(BlockValidationError, match="Unknown column key"):
            validate_block_settings("table", bad_row_table, kind=KIND_STORY)

        # Reject > 500 rows
        many_rows = [{"c1": f"v{i}"} for i in range(501)]
        with pytest.raises(BlockValidationError, match="at most 500 rows"):
            validate_block_settings(
                "table",
                {"columns": [{"key": "c1", "label": "L1"}], "rows": many_rows},
                kind=KIND_STORY,
            )

        # Reject unknown setting keys
        with pytest.raises(BlockValidationError, match="Unknown setting key"):
            validate_block_settings(
                "table",
                {
                    "columns": [{"key": "c1", "label": "L1"}],
                    "rows": [],
                    "extra": "bad",
                },
                kind=KIND_STORY,
            )

    def test_file_block_validation(self, sample_download):
        """file block: requires downloadId, optional label; validates existence of download."""
        validate_block_settings(
            "file",
            {"downloadId": sample_download.pk, "label": "دانلود مقاله PDF"},
            kind=KIND_STORY,
        )

        # Non-existent downloadId rejected
        with pytest.raises(BlockValidationError, match="download"):
            validate_block_settings("file", {"downloadId": 999999}, kind=KIND_STORY)

        # Reject unknown keys
        with pytest.raises(BlockValidationError, match="Unknown setting key"):
            validate_block_settings(
                "file",
                {"downloadId": sample_download.pk, "extra": "x"},
                kind=KIND_STORY,
            )

    def test_references_block_validation(self):
        """references block: list of {label, url?}, max 100 items; validates safe URLs."""
        valid_refs = {
            "items": [
                {"label": "Vaswani et al., 2017", "url": "https://arxiv.org/abs/1706.03762"},
                {"label": "Standard Reference without URL"},
            ]
        }
        validate_block_settings("references", valid_refs, kind=KIND_STORY)

        # Reject > 100 items
        many_items = [{"label": f"Ref {i}"} for i in range(101)]
        with pytest.raises(BlockValidationError, match="at most 100"):
            validate_block_settings("references", {"items": many_items}, kind=KIND_STORY)

        # Reject javascript: in URL
        bad_url_refs = {
            "items": [{"label": "Exploit", "url": "javascript:alert(1)"}]
        }
        with pytest.raises(BlockValidationError, match="invalid scheme"):
            validate_block_settings("references", bad_url_refs, kind=KIND_STORY)

        # Reject unknown keys in settings
        with pytest.raises(BlockValidationError, match="Unknown setting key"):
            validate_block_settings("references", {"items": [], "extra": 1}, kind=KIND_STORY)

    def test_related_block_validation(self, db):
        """related block: list of {family, id}, max 24 items; rejects unknown
        families and malformed IDs.
        """
        article = Article.objects.create(
            title="پست تستی",
            slug="test-post",
            locale="fa",
            status="published",
        )
        valid_related = {
            "records": [
                {"family": "article", "id": str(article.pk)},
                {"family": "project", "id": "1"},
            ]
        }
        validate_block_settings("related", valid_related, kind=KIND_STORY)

        # Reject > 24 records
        many_records = [{"family": "article", "id": str(i + 1)} for i in range(25)]
        with pytest.raises(BlockValidationError, match="at most 24"):
            validate_block_settings("related", {"records": many_records}, kind=KIND_STORY)

        # Reject unknown family
        with pytest.raises(BlockValidationError, match="Unknown family"):
            validate_block_settings(
                "related",
                {"records": [{"family": "unknown_model", "id": "1"}]},
                kind=KIND_STORY,
            )

        # Reject malformed ID (e.g. leading zeros or non-ASCII)
        with pytest.raises(BlockValidationError, match="canonical"):
            validate_block_settings(
                "related",
                {"records": [{"family": "article", "id": "001"}]},
                kind=KIND_STORY,
            )

    def test_public_story_projection_includes_new_blocks_and_heading_ids(
        self, sample_download
    ):
        """Public projection renders code, table, file, references, related,
        and generates deterministic heading IDs.
        """
        page = CompositionPage.objects.create(
            key="story-test-all-blocks",
            kind=KIND_STORY,
            locale="fa",
            title="صفحه داستان جامع",
            status="published",
        )
        section = CompositionSection.objects.create(
            page=page, position=0, layout="1col"
        )
        CompositionBlock.objects.create(
            section=section,
            position=0,
            block_type="heading",
            settings={"text": "مقدمه و رویکرد", "level": "h2"},
        )
        CompositionBlock.objects.create(
            section=section,
            position=1,
            block_type="heading",
            settings={"text": "مقدمه و رویکرد", "level": "h3"},  # Duplicate title collision
        )
        CompositionBlock.objects.create(
            section=section,
            position=2,
            block_type="code",
            settings={"code": "def solve(): return 42", "language": "python"},
        )
        CompositionBlock.objects.create(
            section=section,
            position=3,
            block_type="table",
            settings={
                "caption": "جدول آزمون",
                "columns": [{"key": "k", "label": "کلید"}],
                "rows": [{"k": "مقدار"}],
            },
        )
        CompositionBlock.objects.create(
            section=section,
            position=4,
            block_type="file",
            settings={"downloadId": sample_download.pk, "label": "دانلود گزارش"},
        )
        CompositionBlock.objects.create(
            section=section,
            position=5,
            block_type="references",
            settings={"items": [{"label": "مرجع ۱", "url": "https://example.com"}]},
        )
        CompositionBlock.objects.create(
            section=section,
            position=6,
            block_type="related",
            settings={"records": [{"family": "download", "id": str(sample_download.pk)}]},
        )

        doc = public_story_document(page, "fa")
        assert doc is not None
        blocks = doc["sections"][0]["blocks"]
        assert len(blocks) == 7

        # Headings have derived deterministic IDs with collision counter
        h1 = blocks[0]["settings"]
        h2 = blocks[1]["settings"]
        assert "id" in h1
        assert "id" in h2
        assert h1["id"] != h2["id"]

        # Code block
        code_b = blocks[2]["settings"]
        assert code_b["language"] == "python"
        assert code_b["code"] == "def solve(): return 42"

        # Table block
        table_b = blocks[3]["settings"]
        assert table_b["columns"] == [{"key": "k", "label": "کلید"}]
        assert table_b["rows"] == [{"k": "مقدار"}]

        # File block
        file_b = blocks[4]["settings"]
        assert file_b["downloadId"] == sample_download.pk
        assert file_b["label"] == "دانلود گزارش"
        assert "download" in file_b or "file" in file_b

        # References block
        ref_b = blocks[5]["settings"]
        assert len(ref_b["items"]) == 1
        assert ref_b["items"][0]["label"] == "مرجع ۱"

        # Related block
        rel_b = blocks[6]["settings"]
        assert len(rel_b["records"]) == 1
        assert rel_b["records"][0]["family"] == "download"


def _make_download(db, tmp_path, settings, **overrides):
    """Create a Download variant for C1 gating tests (synthetic, no real file)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = str(tmp_path / "media")
    defaults = {
        "title": "Synthetic",
        "slug": "synthetic",
        "locale": "fa",
        "status": "published",
        "published_at": timezone.now() - timedelta(hours=1),
        "access_state": "public",
    }
    defaults.update(overrides)
    media_kwargs = defaults.pop("media_kwargs", {})
    media = Media.objects.create(
        file=SimpleUploadedFile("a.pdf", b"%PDF-1.4 x", content_type="application/pdf"),
        title="M",
        mime="application/pdf",
        is_active=media_kwargs.get("is_active", True),
    )
    return Download.objects.create(media=media, **defaults)


def _story_with_file_block(locale, download_pk, key="c1-story"):
    page = CompositionPage.objects.create(
        key=f"{key}-{download_pk}-{locale}",
        kind=KIND_STORY,
        locale=locale,
        title="C1",
        status="published",
    )
    section = CompositionSection.objects.create(page=page, position=0, layout="1col")
    CompositionBlock.objects.create(
        section=section,
        position=0,
        block_type="file",
        settings={"downloadId": download_pk, "label": "L"},
    )
    return page


def _file_settings_for(page, locale):
    doc = public_story_document(page, locale)
    assert doc is not None
    return doc["sections"][0]["blocks"][0]["settings"]


class TestCatalogGraphReviewC1:
    """C1 — restricted download URL must not be exposed by file block (P1)."""

    def test_public_download_enriched(self, db, tmp_path, settings):
        dl = _make_download(db, tmp_path, settings, slug="c1-public")
        page = _story_with_file_block("fa", dl.pk, key="c1-public")
        fb = _file_settings_for(page, "fa")
        assert "download" in fb and "file" in fb
        assert fb["download"]["slug"] == "c1-public"
        assert fb["download"]["url"]
        assert "/media/" in fb["download"]["url"]

    def test_restricted_download_omitted(self, db, tmp_path, settings):
        dl = _make_download(
            db, tmp_path, settings, slug="c1-restricted", access_state="restricted"
        )
        page = _story_with_file_block("fa", dl.pk, key="c1-restricted")
        fb = _file_settings_for(page, "fa")
        assert "download" not in fb and "file" not in fb
        assert "synthetic" not in str(fb.get("download", ""))

    def test_metadata_only_omitted(self, db, tmp_path, settings):
        dl = _make_download(
            db, tmp_path, settings, slug="c1-meta", access_state="metadata_only"
        )
        page = _story_with_file_block("fa", dl.pk, key="c1-meta")
        fb = _file_settings_for(page, "fa")
        assert "download" not in fb and "file" not in fb

    def test_draft_omitted(self, db, tmp_path, settings):
        dl = _make_download(
            db, tmp_path, settings, slug="c1-draft", status="draft", published_at=None
        )
        page = _story_with_file_block("fa", dl.pk, key="c1-draft")
        fb = _file_settings_for(page, "fa")
        assert "download" not in fb and "file" not in fb

    def test_future_omitted(self, db, tmp_path, settings):
        dl = _make_download(
            db,
            tmp_path,
            settings,
            slug="c1-future",
            published_at=timezone.now() + timedelta(days=1),
        )
        page = _story_with_file_block("fa", dl.pk, key="c1-future")
        fb = _file_settings_for(page, "fa")
        assert "download" not in fb and "file" not in fb

    def test_inactive_media_omitted(self, db, tmp_path, settings):
        dl = _make_download(
            db, tmp_path, settings, slug="c1-inactive", media_kwargs={"is_active": False}
        )
        page = _story_with_file_block("fa", dl.pk, key="c1-inactive")
        fb = _file_settings_for(page, "fa")
        assert "download" not in fb and "file" not in fb

    def test_wrong_locale_omitted(self, db, tmp_path, settings):
        dl = _make_download(db, tmp_path, settings, slug="c1-en", locale="en")
        page = _story_with_file_block("fa", dl.pk, key="c1-wrong-locale")
        fb = _file_settings_for(page, "fa")
        assert "download" not in fb and "file" not in fb

    def test_snapshot_backed_filters(self, db, tmp_path, settings):
        dl_public = _make_download(db, tmp_path, settings, slug="c1-snap-public")
        dl_restricted = _make_download(
            db, tmp_path, settings, slug="c1-snap-restricted", access_state="restricted"
        )
        for dl, should_have, key in [
            (dl_public, True, "c1-snap-ok"),
            (dl_restricted, False, "c1-snap-no"),
        ]:
            page = CompositionPage.objects.create(
                key=key, kind=KIND_STORY, locale="fa", title="S", status="published"
            )
            PublicationSnapshot.objects.create(
                entity_key="composition",
                object_id=page.pk,
                locale="fa",
                slug=page.key,
                snapshot={
                    "locale": "fa",
                    "title": "S",
                    "sections": [
                        {
                            "layout": "1col",
                            "ratio": "",
                            "enabled": True,
                            "blocks": [
                                {
                                    "blockType": "file",
                                    "settings": {
                                        "downloadId": dl.pk,
                                        "label": "L",
                                    },
                                    "enabled": True,
                                }
                            ],
                        }
                    ],
                },
                published_at=timezone.now(),
            )
            fb = _file_settings_for(page, "fa")
            if should_have:
                assert "download" in fb
            else:
                assert "download" not in fb and "file" not in fb


class TestCatalogGraphReviewC2:
    """C2 — related block must resolve through published policy, omit ineligible."""

    def _make_article(self, slug, locale, status, hours_ago=1):
        kwargs = {"title": slug, "slug": slug, "locale": locale, "status": status}
        if status == "published":
            kwargs["published_at"] = timezone.now() - timedelta(hours=hours_ago)
        else:
            kwargs["published_at"] = None
        if status == "archived":
            kwargs["published_at"] = timezone.now() - timedelta(hours=hours_ago)
        return Article.objects.create(**kwargs)

    def test_filters_missing_draft_archived_other_locale(self, db):
        valid = self._make_article("c2-valid", "fa", "published")
        draft = self._make_article("c2-draft", "fa", "draft")
        archived = self._make_article("c2-arch", "fa", "archived")
        other = self._make_article("c2-other", "en", "published")
        page = CompositionPage.objects.create(
            key="c2-story", kind=KIND_STORY, locale="fa", title="C2", status="published"
        )
        section = CompositionSection.objects.create(page=page, position=0, layout="1col")
        CompositionBlock.objects.create(
            section=section,
            position=0,
            block_type="related",
            settings={
                "records": [
                    {"family": "article", "id": str(valid.pk)},
                    {"family": "article", "id": "999999"},
                    {"family": "article", "id": str(draft.pk)},
                    {"family": "article", "id": str(archived.pk)},
                    {"family": "article", "id": str(other.pk)},
                ]
            },
        )
        doc = public_story_document(page, "fa")
        assert doc is not None
        records = doc["sections"][0]["blocks"][0]["settings"]["records"]
        assert records == [{"family": "article", "id": str(valid.pk)}]

    def test_preserves_order_and_string_ids(self, db):
        a1 = self._make_article("c2-a1", "fa", "published")
        a2 = self._make_article("c2-a2", "fa", "published")
        page = CompositionPage.objects.create(
            key="c2-order", kind=KIND_STORY, locale="fa", title="C2", status="published"
        )
        section = CompositionSection.objects.create(page=page, position=0, layout="1col")
        CompositionBlock.objects.create(
            section=section,
            position=0,
            block_type="related",
            settings={
                "records": [
                    {"family": "article", "id": str(a2.pk)},
                    {"family": "article", "id": str(a1.pk)},
                ]
            },
        )
        doc = public_story_document(page, "fa")
        records = doc["sections"][0]["blocks"][0]["settings"]["records"]
        assert [r["id"] for r in records] == [str(a2.pk), str(a1.pk)]
        assert all(isinstance(r["id"], str) for r in records)


class TestCatalogGraphReviewC3:
    """C3 — bounded canonical ASCII ID validation, never ValueError."""

    def test_unicode_file_id_raises_validation_not_valueerror(self):
        with pytest.raises(BlockValidationError):
            validate_block_settings("file", {"downloadId": "²"}, kind=KIND_STORY)

    def test_malformed_ids_rejected(self):
        bad = [
            "", "0", "00", "001", "-3", "7.0", " 7",
            "１２３", "²", "1" * 20, "9223372036854775808",
        ]
        for val in bad:
            with pytest.raises(BlockValidationError, match="download"):
                validate_block_settings("file", {"downloadId": val}, kind=KIND_STORY)

    def test_signed64_boundary_accepted(self, db, tmp_path, settings):
        # Syntax acceptance at validation boundary (existence checked separately).
        validate_block_settings(
            "related",
            {"records": [{"family": "article", "id": "2147483648"}]},
            kind=KIND_STORY,
        )
        validate_block_settings(
            "related",
            {"records": [{"family": "article", "id": "9223372036854775807"}]},
            kind=KIND_STORY,
        )
        with pytest.raises(BlockValidationError, match="canonical"):
            validate_block_settings(
                "related",
                {"records": [{"family": "article", "id": "9223372036854775808"}]},
                kind=KIND_STORY,
            )

    def test_media_unicode_rejected(self):
        with pytest.raises(BlockValidationError):
            validate_block_settings(
                "figure", {"mediaId": "²", "caption": "c"}, kind=KIND_STORY
            )

    def test_projection_never_raises_on_malformed_stored_id(self, db):
        page = CompositionPage.objects.create(
            key="c3-malformed", kind=KIND_STORY, locale="fa", title="C3", status="published"
        )
        section = CompositionSection.objects.create(page=page, position=0, layout="1col")
        # Bypass validation via direct create (legacy malformed row).
        CompositionBlock.objects.create(
            section=section,
            position=0,
            block_type="file",
            settings={"downloadId": "²", "label": "L"},
        )
        CompositionBlock.objects.create(
            section=section,
            position=1,
            block_type="related",
            settings={"records": [{"family": "article", "id": "²"}]},
        )
        doc = public_story_document(page, "fa")
        assert doc is not None
        file_settings = doc["sections"][0]["blocks"][0]["settings"]
        assert "download" not in file_settings and "file" not in file_settings
        related_settings = doc["sections"][0]["blocks"][1]["settings"]
        assert related_settings["records"] == []

    def test_http_boundary_malformed_file_id_400(self, admin_api_client):
        import json

        created = admin_api_client.post(
            "/api/v1/admin/composition",
            data=json.dumps({"key": "c3-http", "locale": "fa", "title": "C3"}),
            content_type="application/json",
        )
        assert created.status_code == 201
        page_id = created.json()["id"]
        updated_at = created.json()["updatedAt"]
        payload = {
            "sections": [
                {
                    "layout": "1col",
                    "ratio": "",
                    "enabled": True,
                    "blocks": [
                        {
                            "blockType": "file",
                            "settings": {"downloadId": "²", "label": "L"},
                            "enabled": True,
                        }
                    ],
                }
            ]
        }
        put = admin_api_client.put(
            f"/api/v1/admin/composition/{page_id}",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IF_MATCH=f'"{updated_at}"',
        )
        assert put.status_code == 400
