"""Tests for import_content_seed management command (BACKEND-080)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

from apps.content.models import (
    Article,
    ContentSeedRecord,
    Landing,
    LifecycleStatus,
    Profile,
    Project,
    ResearchStatement,
    ResearchTopic,
)
from apps.content.services.content_seed_import import default_seed_path

MINIMAL_SEED = {
    "package_version": "1.1.0-seed-test",
    "records": [
        {
            "content_id": "profile.identity.en",
            "content_type": "profile_identity",
            "slug": "identity",
            "locale": "en",
            "title": "Taha Mohammadi",
            "excerpt": "Software Engineer",
            "body_markdown": "Building reliable systems.",
            "route": "/",
            "status": {
                "approval_state": "needs-owner-input",
                "publication_state": "draft",
                "translation_state": "approved",
                "visibility": "public",
            },
            "dates": {
                "start": None,
                "end": None,
                "published_at": None,
                "retired_at": None,
                "record_updated_at": "2026-08-29T02:03:00-07:00",
            },
            "sort_order": 0,
            "tags": [],
            "links": [],
            "data": {
                "name": "Taha Mohammadi",
                "headline": "Building reliable systems.",
            },
            "evidence": [],
            "restrictions": [],
            "seo": {},
            "relations": [],
        },
        {
            "content_id": "admin.academic.ma-diploma",
            "content_type": "document",
            "slug": "admin-academic-ma-diploma",
            "locale": "und",
            "title": "M.A. diploma verification",
            "excerpt": "Admin checklist",
            "body_markdown": "Admin checklist",
            "route": "/admin",
            "status": {
                "approval_state": "private",
                "publication_state": "not-public",
                "translation_state": "not-needed",
                "visibility": "admin-only",
            },
            "dates": {
                "start": None,
                "end": None,
                "published_at": None,
                "retired_at": None,
                "record_updated_at": "2026-08-29T02:30:00-07:00",
            },
            "sort_order": 0,
            "tags": [],
            "links": [],
            "data": {"checklist_item": True},
            "evidence": [],
            "restrictions": ["Admin only."],
            "seo": {},
            "relations": [],
        },
    ],
}

MINIMAL_SETTINGS = {
    "public_email": True,
    "contact_form_only": False,
    "show_orcid_in_primary_contact": False,
    "show_phone": False,
    "show_public_city_country": False,
    "show_phd_2027_availability": True,
    "public_cv_download": False,
    "public_resume_download": False,
    "default_media_policy": "withhold-until-rights-confirmed",
    "locale_fallback": "none",
}


def _write_seed_files(tmp_path: Path) -> tuple[Path, Path]:
    seed_path = tmp_path / "content-records.v1.1-seed.json"
    settings_path = tmp_path / "seed-settings.json"
    seed_path.write_text(json.dumps(MINIMAL_SEED), encoding="utf-8")
    settings_path.write_text(json.dumps(MINIMAL_SETTINGS), encoding="utf-8")
    return seed_path, settings_path


@pytest.mark.django_db
def test_import_content_seed_is_idempotent(tmp_path):
    seed_path, settings_path = _write_seed_files(tmp_path)

    call_command(
        "import_content_seed",
        file=str(seed_path),
        settings_file=str(settings_path),
    )
    assert ContentSeedRecord.objects.count() == 2
    first_updated = ContentSeedRecord.objects.get(content_id="profile.identity.en").updated_at

    call_command(
        "import_content_seed",
        file=str(seed_path),
        settings_file=str(settings_path),
    )
    assert ContentSeedRecord.objects.count() == 2
    row = ContentSeedRecord.objects.get(content_id="profile.identity.en")
    assert row.publication_state == "draft"
    assert row.visibility == "public"
    assert row.approval_state == "needs-owner-input"
    assert row.updated_at >= first_updated


@pytest.mark.django_db
def test_import_content_seed_does_not_publish_typed_models(tmp_path):
    seed_path, settings_path = _write_seed_files(tmp_path)
    call_command(
        "import_content_seed",
        file=str(seed_path),
        settings_file=str(settings_path),
    )

    assert Landing.objects.filter(status=LifecycleStatus.PUBLISHED).count() == 0
    assert Profile.objects.filter(status=LifecycleStatus.PUBLISHED).count() == 0
    assert Landing.objects.get(locale="en", slug="home").status == LifecycleStatus.DRAFT

    admin_row = ContentSeedRecord.objects.get(content_id="admin.academic.ma-diploma")
    assert admin_row.visibility == "admin-only"
    assert admin_row.mapped_model_label == ""
    assert admin_row.mapped_object_id is None


@pytest.mark.django_db
def test_import_content_seed_public_api_non_leak(tmp_path):
    seed_path, settings_path = _write_seed_files(tmp_path)
    call_command(
        "import_content_seed",
        file=str(seed_path),
        settings_file=str(settings_path),
    )

    assert Landing.objects.public().count() == 0
    assert Profile.objects.public().count() == 0
    assert ResearchStatement.objects.public().count() == 0
    assert ResearchTopic.objects.public().count() == 0
    assert Project.objects.public().count() == 0
    assert Article.objects.public().count() == 0

    client = Client()
    assert client.get("/api/landings/en/home").status_code == 404
    assert client.get("/api/profiles/en/about").status_code == 404
    assert client.get("/api/articles/en").json()["items"] == []
    assert client.get("/api/research/topics/en").json()["items"] == []


@pytest.mark.django_db
@pytest.mark.skipif(
    not default_seed_path().exists(),
    reason="Workspace seed package not available",
)
def test_import_full_seed_package_has_85_records():
    call_command("import_content_seed")
    assert ContentSeedRecord.objects.count() == 85
    assert ContentSeedRecord.objects.filter(visibility="admin-only").count() == 13
    assert Landing.objects.public().count() == 0
    assert Profile.objects.public().count() == 0


# --- BACKEND-081: seed.empty.* → route copy / unavailable surfaces ----------

EMPTY_STATE_SEED = {
    "package_version": "1.1.0-seed-test",
    "records": [
        {
            "content_id": "seed.empty.creative.en",
            "content_type": "route_copy",
            "slug": "creative-empty",
            "locale": "en",
            "title": "Creative work",
            "excerpt": (
                "Selected visual and design work will be added after authorship, "
                "credits, and publication rights are confirmed."
            ),
            "body_markdown": (
                "Selected visual and design work will be added here after authorship, "
                "collaborators, credits, and publication rights are confirmed."
            ),
            "route": "/creative",
            "status": {
                "approval_state": "approved",
                "publication_state": "draft",
                "translation_state": "approved",
                "visibility": "public",
            },
            "dates": {"start": None, "end": None, "published_at": None, "retired_at": None},
            "sort_order": 0,
            "tags": [],
            "links": [],
            "data": {"empty_state": True, "reason": "media-rights-and-records-pending"},
            "evidence": [],
            "restrictions": [],
            "seo": {},
            "relations": [],
        },
        {
            "content_id": "seed.empty.teaching.en",
            "content_type": "route_copy",
            "slug": "teaching-empty",
            "locale": "en",
            "title": "Teaching",
            "excerpt": "No public teaching record is available yet.",
            "body_markdown": "No public teaching record is available yet.",
            "route": "/about/teaching",
            "status": {
                "approval_state": "approved",
                "publication_state": "draft",
                "translation_state": "approved",
                "visibility": "public",
            },
            "dates": {"start": None, "end": None, "published_at": None, "retired_at": None},
            "sort_order": 0,
            "tags": [],
            "links": [],
            "data": {"empty_state": True, "reason": "owner-verification-required"},
            "evidence": [],
            "restrictions": [],
            "seo": {},
            "relations": [],
        },
        {
            "content_id": "seed.empty.cv.en",
            "content_type": "document",
            "slug": "cv-unavailable",
            "locale": "en",
            "title": "CV",
            "excerpt": "The current public CV is not yet available for download.",
            "body_markdown": "The current public CV is not yet available for download.",
            "route": "/cv",
            "status": {
                "approval_state": "approved",
                "publication_state": "draft",
                "translation_state": "approved",
                "visibility": "public",
            },
            "dates": {"start": None, "end": None, "published_at": None, "retired_at": None},
            "sort_order": 0,
            "tags": [],
            "links": [],
            "data": {"empty_state": True, "download_available": False},
            "evidence": [],
            "restrictions": [],
            "seo": {},
            "relations": [],
        },
    ],
}


def _write_empty_state_seed(tmp_path: Path) -> tuple[Path, Path]:
    seed_path = tmp_path / "content-records.v1.1-seed.json"
    settings_path = tmp_path / "seed-settings.json"
    seed_path.write_text(json.dumps(EMPTY_STATE_SEED), encoding="utf-8")
    settings_path.write_text(json.dumps(MINIMAL_SETTINGS), encoding="utf-8")
    return seed_path, settings_path


def _import_empty_state_seed(tmp_path):
    seed_path, settings_path = _write_empty_state_seed(tmp_path)
    call_command(
        "import_content_seed",
        file=str(seed_path),
        settings_file=str(settings_path),
    )


@pytest.mark.django_db
def test_seed_empty_route_copy_maps_to_draft_landing(tmp_path):
    _import_empty_state_seed(tmp_path)

    for slug, title, body in (
        ("creative-empty", "Creative work", "Selected visual and design work"),
        ("teaching-empty", "Teaching", "No public teaching record is available yet."),
    ):
        landing = Landing.objects.get(locale="en", slug=slug)
        assert landing.status == LifecycleStatus.DRAFT
        assert landing.title == title
        assert body in landing.body
        seed_row = ContentSeedRecord.objects.get(
            content_id=f"seed.empty.{slug.replace('-empty', '')}.en"
        )
        assert seed_row.mapped_model_label == "content.Landing"
        assert seed_row.mapped_object_id == landing.pk


@pytest.mark.django_db
def test_seed_empty_cv_maps_to_unavailable_route_copy_surface(tmp_path):
    _import_empty_state_seed(tmp_path)

    landing = Landing.objects.get(locale="en", slug="cv-unavailable")
    assert landing.status == LifecycleStatus.DRAFT
    assert landing.title == "CV"
    assert "not yet available for download" in landing.body

    seed_row = ContentSeedRecord.objects.get(content_id="seed.empty.cv.en")
    assert seed_row.mapped_model_label == "content.Landing"
    assert seed_row.mapped_object_id == landing.pk


@pytest.mark.django_db
def test_seed_empty_cv_unavailable_surface_invents_no_download(tmp_path):
    _import_empty_state_seed(tmp_path)

    from apps.siteconfig.models import SiteSettings

    settings_row = SiteSettings.get_singleton()
    assert settings_row.current_cv_media is None
    assert settings_row.current_resume_media is None

    client = Client()
    response = client.get("/api/site")
    assert response.status_code == 200
    assert response.json()["downloads"] == []
    assert client.get("/api/landings/en/cv-unavailable").status_code == 404


@pytest.mark.django_db
def test_seed_empty_records_never_publish(tmp_path):
    _import_empty_state_seed(tmp_path)

    assert Landing.objects.public().count() == 0
    assert (
        Landing.objects.filter(
            slug__in=["creative-empty", "teaching-empty", "cv-unavailable"],
            status=LifecycleStatus.PUBLISHED,
        ).count()
        == 0
    )
    client = Client()
    assert client.get("/api/landings/en").json() == []
    assert client.get("/api/landings/en/creative-empty").status_code == 404
    assert client.get("/api/landings/en/teaching-empty").status_code == 404
    assert client.get("/api/landings/en/cv-unavailable").status_code == 404


@pytest.mark.django_db
@pytest.mark.skipif(
    not default_seed_path().exists(),
    reason="Workspace seed package not available",
)
def test_full_seed_package_maps_all_seed_empty_records_to_route_copy():
    call_command("import_content_seed")

    empty_rows = ContentSeedRecord.objects.filter(content_id__startswith="seed.empty.")
    assert empty_rows.count() == 6
    for row in empty_rows:
        assert row.mapped_model_label == "content.Landing", row.content_id
        assert row.mapped_object_id is not None

    assert Landing.objects.filter(slug="creative-empty").count() == 2
    assert Landing.objects.filter(slug="teaching-empty").count() == 2
    assert Landing.objects.filter(slug="cv-unavailable").count() == 2
    assert (
        Landing.objects.filter(
            slug__in=["creative-empty", "teaching-empty", "cv-unavailable"]
        )
        .exclude(status=LifecycleStatus.DRAFT)
        .count()
        == 0
    )
