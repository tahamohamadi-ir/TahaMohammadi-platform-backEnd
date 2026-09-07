"""Managed UI copy is seeded only into absent locale dictionary keys."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.siteconfig.models import LocalizedSiteSettings

pytestmark = pytest.mark.django_db


def write_copy(tmp_path, payload):
    path = tmp_path / "copy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_copy_seed_creates_drafts_and_only_fills_absent_keys(tmp_path):
    path = write_copy(
        tmp_path, {"en": {"title": "Seed title", "label": "Seed label"}, "fa": {"title": "عنوان"}}
    )
    stamp = timezone.now()
    existing = LocalizedSiteSettings.objects.create(
        locale="en",
        managed_copy={"title": "", "owner": "Keep"},
        status="published",
        published_payload={"contentCopy": {"title": "Published owner title"}},
        published_at=stamp,
    )
    call_command("seed_managed_copy", file=path)
    existing.refresh_from_db()
    assert existing.managed_copy == {"title": "", "label": "Seed label", "owner": "Keep"}
    assert existing.status == "published"
    assert existing.published_payload == {"contentCopy": {"title": "Published owner title"}}
    assert existing.published_at == stamp
    fa = LocalizedSiteSettings.objects.get(locale="fa")
    assert fa.managed_copy == {"title": "عنوان"}
    assert fa.status == "draft"
    assert fa.published_payload is None
    assert fa.published_at is None
    modified = existing.updated_at
    call_command("seed_managed_copy", file=path)
    existing.refresh_from_db()
    assert existing.updated_at == modified


def test_copy_seed_dry_run_has_report_and_no_persisted_changes(tmp_path):
    path = write_copy(tmp_path, {"en": {"label": "Label"}, "fa": {"label": "برچسب"}})
    out = StringIO()
    call_command("seed_managed_copy", file=path, dry_run=True, stdout=out)
    assert not LocalizedSiteSettings.objects.exists()
    assert "en" in out.getvalue() and "label" in out.getvalue()


@pytest.mark.parametrize(
    "payload",
    [
        {"en": {"label": 1}, "fa": {}},
        {"en": {}, "de": {}},
        {"en": {}, "fa": []},
        {"en": {"bad key": "value"}, "fa": {}},
    ],
)
def test_copy_seed_validates_entire_source_before_writing(tmp_path, payload):
    with pytest.raises(CommandError):
        call_command("seed_managed_copy", file=write_copy(tmp_path, payload))
    assert not LocalizedSiteSettings.objects.exists()
