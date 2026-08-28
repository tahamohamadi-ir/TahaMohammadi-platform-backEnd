"""P8 public projection ACL + download safety + citation honesty."""

from datetime import date, timedelta
from pathlib import PurePosixPath

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    AccessState,
    Book,
    Download,
    DownloadType,
    LifecycleStatus,
    Locale,
    Publication,
    Talk,
)
from apps.media.models import Media

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

P8_FORBIDDEN = {
    "status",
    "created_at",
    "citation_source",
    "citation_last_verified",
    "citation_visibility",
}


@pytest.fixture
def api_client():
    return Client()


def past():
    return timezone.now() - timedelta(days=1)


def make_media(*, name: str = "file.pdf", active: bool = True) -> Media:
    return Media.objects.create(
        file=SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf"),
        title=name,
        is_active=active,
    )


@pytest.mark.django_db
def test_publication_draft_404_and_citation_gate(api_client):
    Publication.objects.create(
        locale=Locale.EN,
        slug="live-paper",
        title="Live Paper",
        authors="Taha Mohammadi",
        doi="10.1000/live",
        citation_text="Mohammadi, T. (2024). Live Paper.",
        citation_count=10,
        citation_source="Scholar",
        citation_last_verified=date(2024, 6, 1),
        citation_visibility="public",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Publication.objects.create(
        locale=Locale.EN,
        slug="draft-paper",
        title="Draft",
        authors="Hidden",
        citation_text="should not leak",
        status=LifecycleStatus.DRAFT,
    )

    live = api_client.get("/api/publications/en/live-paper")
    assert live.status_code == 200
    body = live.json()
    assert body["citation_text"] == "Mohammadi, T. (2024). Live Paper."
    assert body["citation_count"] == 10
    assert P8_FORBIDDEN.isdisjoint(body.keys())

    draft = api_client.get("/api/publications/en/draft-paper")
    assert draft.status_code == 404

    # Legacy research path still works for published rows.
    legacy = api_client.get("/api/research/publications/en/live-paper")
    assert legacy.status_code == 200
    assert legacy.json()["slug"] == "live-paper"


@pytest.mark.django_db
def test_publication_citation_requires_authors_and_title(api_client):
    Publication.objects.create(
        locale=Locale.EN,
        slug="incomplete",
        title="Has Title",
        authors="",
        citation_text="Incomplete citation should not export",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    response = api_client.get("/api/publications/en/incomplete")
    assert response.status_code == 200
    assert response.json()["citation_text"] is None


@pytest.mark.django_db
def test_publication_restricted_hides_pdf(api_client):
    media = make_media(name="paper.pdf", active=True)
    Publication.objects.create(
        locale=Locale.EN,
        slug="restricted-paper",
        title="Restricted",
        authors="Taha",
        pdf_url="https://example.com/secret.pdf",
        pdf_media=media,
        access_state=AccessState.RESTRICTED,
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    body = api_client.get("/api/publications/en/restricted-paper").json()
    assert body["pdf_url"] == ""
    assert body["pdf"] is None


@pytest.mark.django_db
def test_download_inactive_media_never_leaks(api_client):
    active = make_media(name="open.pdf", active=True)
    inactive = make_media(name="secret.pdf", active=False)

    Download.objects.create(
        locale=Locale.EN,
        slug="open-file",
        title="Open File",
        media=active,
        download_type=DownloadType.PDF,
        access_state=AccessState.PUBLIC,
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Download.objects.create(
        locale=Locale.EN,
        slug="inactive-file",
        title="Inactive File",
        media=inactive,
        download_type=DownloadType.PDF,
        access_state=AccessState.PUBLIC,
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Download.objects.create(
        locale=Locale.EN,
        slug="restricted-file",
        title="Restricted File",
        media=active,
        download_type=DownloadType.PDF,
        access_state=AccessState.RESTRICTED,
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Download.objects.create(
        locale=Locale.EN,
        slug="draft-file",
        title="Draft File",
        media=active,
        status=LifecycleStatus.DRAFT,
    )

    open_body = api_client.get("/api/downloads/en/open-file").json()
    assert open_body["file"] is not None
    assert "/media/" in open_body["file"]["url"]
    assert PurePosixPath(active.file.name).name in open_body["file"]["url"]

    inactive_body = api_client.get("/api/downloads/en/inactive-file").json()
    assert inactive_body["file"] is None
    assert inactive_body["mime"] is None
    assert inactive_body["size_bytes"] is None
    assert "secret.pdf" not in str(inactive_body)

    restricted_body = api_client.get("/api/downloads/en/restricted-file").json()
    assert restricted_body["file"] is None

    assert api_client.get("/api/downloads/en/draft-file").status_code == 404

    file_ok = api_client.get("/api/downloads/en/open-file/file")
    assert file_ok.status_code == 200
    assert file_ok["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in file_ok["Cache-Control"]
    assert "attachment" in file_ok["Content-Disposition"]

    assert api_client.get("/api/downloads/en/inactive-file/file").status_code == 404
    assert api_client.get("/api/downloads/en/restricted-file/file").status_code == 404
    assert api_client.get("/api/downloads/en/draft-file/file").status_code == 404


@pytest.mark.django_db
def test_book_and_talk_public_list_excludes_drafts(api_client):
    Book.objects.create(
        locale=Locale.EN,
        slug="live-book",
        title="Live Book",
        authors="Taha",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Book.objects.create(
        locale=Locale.EN,
        slug="draft-book",
        title="Draft Book",
        status=LifecycleStatus.DRAFT,
    )
    Talk.objects.create(
        locale=Locale.EN,
        slug="live-talk",
        title="Live Talk",
        speakers="Taha",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    Talk.objects.create(
        locale=Locale.EN,
        slug="draft-talk",
        title="Draft Talk",
        status=LifecycleStatus.DRAFT,
    )

    books = api_client.get("/api/books/en").json()
    book_items = books["items"] if isinstance(books, dict) else books
    assert [b["slug"] for b in book_items] == ["live-book"]

    talks = api_client.get("/api/talks/en").json()
    talk_items = talks["items"] if isinstance(talks, dict) else talks
    assert [t["slug"] for t in talk_items] == ["live-talk"]

    assert api_client.get("/api/books/en/draft-book").status_code == 404
    assert api_client.get("/api/talks/en/draft-talk").status_code == 404
