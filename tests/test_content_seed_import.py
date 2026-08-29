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
