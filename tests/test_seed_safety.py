"""Owner edits survive every normal seed entry point (PU-26 / CM-06)."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.content.models import (
    ContentSeedRecord,
    Landing,
    LifecycleStatus,
    Profile,
    ProfileSocialLink,
    Project,
)
from apps.content.services.content_seed_import import apply_seed_settings
from apps.siteconfig.models import SiteSettings

pytestmark = pytest.mark.django_db


def package(tmp_path):
    records = [
        {
            "content_id": "profile.identity.en",
            "content_type": "profile_identity",
            "locale": "en",
            "title": "Seed identity",
            "data": {"name": "Seed name"},
        },
        {
            "content_id": "profile.bio.en",
            "content_type": "profile_bio",
            "locale": "en",
            "title": "Seed bio",
            "body_markdown": "Seed body",
        },
        {
            "content_id": "availability.en",
            "content_type": "availability",
            "locale": "en",
            "body_markdown": "Seed availability",
        },
        {
            "content_id": "contact.github",
            "content_type": "contact_link",
            "data": {"url": "https://github.com/seed-fixture"},
        },
    ]
    path = tmp_path / "seed.json"
    path.write_text(json.dumps({"records": records}), encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"show_phone": False}), encoding="utf-8")
    return {"file": str(path), "settings_file": str(settings)}


def test_content_rerun_preserves_edited_identity_profile_relations_and_publication(tmp_path):
    args = package(tmp_path)
    call_command("import_content_seed", **args)
    profile = Profile.objects.get(locale="en")
    stamp = timezone.now()
    Profile.objects.filter(pk=profile.pk).update(
        slug="owner-about",
        body="Owner body",
        availability="Owner availability",
        status=LifecycleStatus.PUBLISHED,
        published_at=stamp,
    )
    profile.social_links.all().delete()
    owner_link = ProfileSocialLink.objects.create(
        profile=profile, platform="Owner", url="https://example.org/owner"
    )
    settings = SiteSettings.get_singleton()
    settings.brand_name = "Owner brand"
    settings.contact_phone = "owner phone"
    settings.contact_form_enabled = True
    settings.save()
    before = ContentSeedRecord.objects.get(content_id="profile.bio.en").updated_at
    call_command("import_content_seed", **args)
    profile.refresh_from_db()
    settings.refresh_from_db()
    assert Profile.objects.filter(locale="en").count() == 1
    assert (profile.body, profile.availability) == ("Owner body", "Owner availability")
    assert (profile.status, profile.published_at) == (LifecycleStatus.PUBLISHED, stamp)
    assert list(profile.social_links.values_list("pk", flat=True)) == [owner_link.pk]
    assert (settings.brand_name, settings.contact_phone) == ("Owner brand", "owner phone")
    assert settings.contact_form_enabled
    assert ContentSeedRecord.objects.get(content_id="profile.bio.en").updated_at == before


def test_first_import_preserves_existing_singleton_and_profile(tmp_path):
    settings = SiteSettings.get_singleton()
    settings.brand_name = "Owner brand"
    settings.contact_phone = "owner phone"
    settings.save()
    profile = Profile.objects.create(locale="en", slug="about", title="Owner", body="Owner body")
    call_command("import_content_seed", **package(tmp_path))
    settings.refresh_from_db()
    profile.refresh_from_db()
    assert (settings.brand_name, settings.contact_phone) == ("Owner brand", "owner phone")
    assert profile.body == "Owner body"
    assert profile.availability == ""


def test_selective_overwrite_has_dry_run_report_and_preserves_other_rows(tmp_path):
    args = package(tmp_path)
    call_command("import_content_seed", **args)
    profile = Profile.objects.get(locale="en")
    Profile.objects.filter(pk=profile.pk).update(body="Owner body", status=LifecycleStatus.ARCHIVED)
    Landing.objects.update(title="Owner home")
    report = StringIO()
    call_command(
        "import_content_seed", **args, overwrite_id=["profile.bio.en"], dry_run=True, stdout=report
    )
    profile.refresh_from_db()
    assert profile.body == "Owner body"
    assert "profile.bio.en" in report.getvalue()
    assert "body" in report.getvalue()
    call_command("import_content_seed", **args, overwrite_id=["profile.bio.en"])
    profile.refresh_from_db()
    assert profile.body == "Seed body"
    assert profile.status == LifecycleStatus.ARCHIVED
    assert Landing.objects.get(locale="en").title == "Owner home"


def test_profile_seed_preserves_existing_profile_and_child_ids(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"profiles": {"en": {}, "fa": {}}}), encoding="utf-8")
    call_command("import_profile_seed", path=str(path))
    profile = Profile.objects.get(locale="en")
    Profile.objects.filter(pk=profile.pk).update(
        body="Owner body", revision=9, status=LifecycleStatus.ARCHIVED
    )
    child = ProfileSocialLink.objects.create(
        profile=profile, platform="Owner", url="https://example.org/owner"
    )
    call_command("import_profile_seed", path=str(path))
    profile.refresh_from_db()
    assert (profile.body, profile.revision, profile.status) == (
        "Owner body",
        9,
        LifecycleStatus.ARCHIVED,
    )
    assert list(profile.social_links.values_list("pk", flat=True)) == [child.pk]


def test_site_seed_rerun_preserves_owner_removed_relations_and_status():
    call_command("seed_site_content")
    project = Project.objects.filter(locale="en").first()
    project.topics.clear()
    project.publications.clear()
    Project.objects.filter(pk=project.pk).update(
        title="Owner project", status=LifecycleStatus.DRAFT
    )
    call_command("seed_site_content")
    project.refresh_from_db()
    assert project.title == "Owner project"
    assert project.status == LifecycleStatus.DRAFT
    assert not project.topics.exists()
    assert not project.publications.exists()


def test_site_seed_preserves_renamed_slugs():
    call_command("seed_site_content")
    landing = Landing.objects.get(locale="en", slug="home")
    project = Project.objects.filter(locale="en").first()
    Landing.objects.filter(pk=landing.pk).update(slug="owner-home")
    Project.objects.filter(pk=project.pk).update(slug="owner-project")
    counts = (Landing.objects.count(), Project.objects.count())
    call_command("seed_site_content")
    assert (Landing.objects.count(), Project.objects.count()) == counts
    landing.refresh_from_db()
    project.refresh_from_db()
    assert (landing.slug, project.slug) == ("owner-home", "owner-project")


def test_selective_availability_refresh_uses_renamed_profile(tmp_path):
    args = package(tmp_path)
    call_command("import_content_seed", **args)
    profile = Profile.objects.get(locale="en")
    Profile.objects.filter(pk=profile.pk).update(slug="owner-about", availability="Owner")
    call_command("import_content_seed", **args, overwrite_id=["availability.en"])
    profile.refresh_from_db()
    assert Profile.objects.filter(locale="en").count() == 1
    assert profile.availability == "Seed availability"


def test_seed_settings_preserves_existing_values_without_guessing_owner_intent(tmp_path):
    row = SiteSettings.get_singleton()
    row.brand_name = "Taha Mohammadi"
    row.contact_phone = ""
    row.contact_location = "Owner place"
    row.seed_policy = {"owner-policy": True}
    row.save()
    args = package(tmp_path)
    from pathlib import Path

    apply_seed_settings(Path(args["settings_file"]))
    row.refresh_from_db()
    assert row.contact_location == "Owner place"
    assert row.seed_policy == {"owner-policy": True}
