"""Admin-only seed supplement records stay out of every public surface (BACKEND-082).

The 13 ``admin.*`` records of the owner seed v1.1 package must live only in the
admin-only ``ContentSeedRecord`` table, must never be typed-mapped onto public
CMS models, and must never appear in any public serializer response.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import Client

from apps.content.models import (
    Article,
    ContentSeedRecord,
    CreativeWork,
    Download,
    Landing,
    Profile,
    ProfileCertificate,
    Project,
    ResearchStatement,
    ResearchTopic,
)
from apps.content.services.content_seed_import import default_seed_path

pytestmark = pytest.mark.skipif(
    not default_seed_path().exists(),
    reason="Workspace seed package not available",
)

ADMIN_SEED_IDS = [
    "admin.academic.ma-diploma",
    "admin.academic.ma-transcript",
    "admin.academic.ba-diploma",
    "admin.academic.ba-transcript",
    "admin.academic.thesis",
    "admin.credential.stanford",
    "admin.project.behavior-ip",
    "admin.project.dashboard-rights",
    "admin.legal.stack",
    "admin.writing.visual-discourse",
    "admin.writing.vtd-edge",
    "admin.writing.dashboard-book",
    "admin.settings.publication-defaults",
]

ADMIN_SEED_MARKERS = [
    "admin-academic-ma-diploma",
    "admin-academic-ma-transcript",
    "admin-academic-ba-diploma",
    "admin-academic-ba-transcript",
    "admin-academic-thesis",
    "admin-credential-stanford",
    "admin-project-behavior-ip",
    "admin-project-dashboard-rights",
    "admin-legal-stack",
    "admin-writing-visual-discourse",
    "admin-writing-vtd-edge",
    "admin-writing-dashboard-book",
    "M.A. diploma verification",
    "M.A. transcript verification",
    "B.A. diploma verification",
    "B.A. transcript verification",
    "Thesis-title verification",
    "Stanford credential verification",
    "Behavioral platform publication clearance",
    "Dashboard media/publication rights",
    "Legal copy dependency",
    "Metadata checklist",
    "Seed publication defaults",
]

PUBLIC_ENDPOINTS = [
    "/api/site",
    "/api/landings/en",
    "/api/landings/fa",
    "/api/profiles/en",
    "/api/profiles/fa",
    "/api/profiles/en/about",
    "/api/profiles/fa/about",
    "/api/articles/en",
    "/api/articles/fa",
    "/api/series/en",
    "/api/series/fa",
    "/api/tags/en",
    "/api/tags/fa",
    "/api/article-redirects/en",
    "/api/research/topics/en",
    "/api/research/topics/fa",
    "/api/research/statements/en",
    "/api/research/statements/fa",
    "/api/research/projects/en",
    "/api/research/projects/fa",
    "/api/research/publications/en",
    "/api/research/publications/fa",
    "/api/projects/en",
    "/api/projects/fa",
    "/api/publications/en",
    "/api/publications/fa",
    "/api/books/en",
    "/api/books/fa",
    "/api/talks/en",
    "/api/talks/fa",
    "/api/downloads/en",
    "/api/downloads/fa",
    "/api/courses/en",
    "/api/courses/fa",
    "/api/teaching/en",
    "/api/teaching/fa",
    "/api/creative-works/en",
    "/api/creative-works/fa",
    "/api/creative/en",
    "/api/creative/fa",
    "/api/home-composition/en",
    "/api/home-composition/fa",
    "/api/graph/en",
    "/api/graph/fa",
]


@pytest.fixture
def imported_seed_db(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        from apps.siteconfig.models import SiteSettings

        settings = SiteSettings.get_singleton()
        before = {
            field: getattr(settings, field)
            for field in (
                "contact_phone",
                "contact_phone_intl",
                "contact_location",
                "current_cv_media_id",
                "current_resume_media_id",
            )
        }
        call_command("import_content_seed")
        yield before


@pytest.mark.django_db
def test_admin_seed_records_stay_in_admin_only_model(imported_seed_db):
    for content_id in ADMIN_SEED_IDS:
        row = ContentSeedRecord.objects.get(content_id=content_id)
        assert row.visibility == "admin-only", content_id
        assert row.publication_state == "not-public", content_id
        assert row.approval_state == "private", content_id

    assert ContentSeedRecord.objects.filter(visibility="admin-only").count() == len(ADMIN_SEED_IDS)


@pytest.mark.django_db
def test_admin_seed_records_never_map_to_public_typed_models(imported_seed_db):
    for content_id in ADMIN_SEED_IDS:
        row = ContentSeedRecord.objects.get(content_id=content_id)
        assert row.mapped_model_label == "", content_id
        assert row.mapped_object_id is None, content_id

    assert Landing.objects.filter(slug__startswith="admin-").count() == 0
    assert Article.objects.filter(slug__startswith="admin-").count() == 0
    assert Download.objects.filter(slug__startswith="admin-").count() == 0
    assert Project.objects.filter(slug__startswith="admin-").count() == 0
    assert ResearchTopic.objects.filter(slug__startswith="admin-").count() == 0
    assert ResearchStatement.objects.filter(slug__startswith="admin-").count() == 0
    assert ProfileCertificate.objects.filter(slug="admin-credential-stanford").count() == 0
    assert Profile.objects.filter(slug__startswith="admin-").count() == 0
    assert CreativeWork.objects.filter(slug__startswith="admin-").count() == 0

    admin_titles = [
        "M.A. diploma verification",
        "Stanford credential verification",
        "Legal copy dependency",
    ]
    assert not Article.objects.filter(title__in=admin_titles).exists()
    assert not Landing.objects.filter(title__in=admin_titles).exists()
    assert not Download.objects.filter(title__in=admin_titles).exists()


@pytest.mark.django_db
def test_public_api_omits_admin_seed_records(imported_seed_db):
    client = Client()
    for endpoint in PUBLIC_ENDPOINTS:
        response = client.get(endpoint)
        if response.status_code == 404:
            continue
        assert response.status_code == 200, endpoint
        body = response.content.decode("utf-8")
        for marker in ADMIN_SEED_MARKERS:
            assert marker not in body, f"{marker!r} leaked via {endpoint}"


@pytest.mark.django_db
def test_seed_preserves_existing_settings_without_public_admin_record_leak(imported_seed_db):
    from apps.siteconfig.models import SiteSettings

    row = ContentSeedRecord.objects.get(content_id="admin.settings.publication-defaults")
    assert row.mapped_model_label == ""

    settings_row = SiteSettings.get_singleton()
    for field, original in imported_seed_db.items():
        assert getattr(settings_row, field) == original

    client = Client()
    body = client.get("/api/site").json()
    assert "phone" not in body["contact"]
