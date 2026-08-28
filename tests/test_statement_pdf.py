"""Research statement PDF projection — active Media only (DEFER-0019)."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.content.models import LifecycleStatus, Locale, ResearchStatement
from apps.media.models import Media

# Minimal PDF magic bytes so sniff accepts application/pdf when used.
_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def api_client():
    return Client()


def past():
    return timezone.now() - timedelta(days=1)


@pytest.fixture
def statement_with_pdf(db, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    active = Media.objects.create(
        file=SimpleUploadedFile("agenda.pdf", _PDF_BYTES, content_type="application/pdf"),
        title="Agenda PDF",
        alt_text="Research agenda PDF",
        is_active=True,
    )
    inactive = Media.objects.create(
        file=SimpleUploadedFile("secret.pdf", _PDF_BYTES, content_type="application/pdf"),
        title="Secret PDF",
        alt_text="Should not leak",
        is_active=False,
    )
    statement = ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="agenda",
        title="Agenda",
        body="<p>Statement</p>",
        statement_pdf=active,
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    return {"statement": statement, "active": active, "inactive": inactive}


def test_statement_pdf_projects_active_media(api_client, statement_with_pdf):
    data = api_client.get("/api/research/statements/en/agenda").json()
    assert data["statement_pdf"] is not None
    pdf = data["statement_pdf"]
    assert pdf["title"] == "Agenda PDF"
    assert pdf["mime"] == "application/pdf"
    assert int(pdf["size"]) > 0
    assert "/media/" in pdf["url"]
    assert "Secret PDF" not in str(data)


def test_statement_pdf_omits_inactive_media(api_client, statement_with_pdf):
    statement = statement_with_pdf["statement"]
    statement.statement_pdf = statement_with_pdf["inactive"]
    statement.save(update_fields=["statement_pdf"])
    data = api_client.get("/api/research/statements/en/agenda").json()
    assert data["statement_pdf"] is None
    assert "Secret PDF" not in str(data)
    assert "secret.pdf" not in str(data)


def test_statement_pdf_null_when_unset(api_client, db):
    ResearchStatement.objects.create(
        locale=Locale.EN,
        slug="plain",
        title="Plain",
        body="<p>No PDF</p>",
        status=LifecycleStatus.PUBLISHED,
        published_at=past(),
    )
    data = api_client.get("/api/research/statements/en/plain").json()
    assert data.get("statement_pdf") is None
