import json

import pytest
from django.core.management import call_command

from apps.content.models import Profile, ResearchTopic

pytestmark = pytest.mark.django_db


def _make_pair(model, translation_key, **extra):
    rows = []
    for locale in ("en", "fa"):
        rows.append(
            model.objects.create(
                locale=locale,
                slug=f"{model._meta.model_name}-{locale}",
                title=f"{model._meta.model_name} {locale}",
                status="published",
                published_at="2026-01-01T00:00:00Z",
                translation_key=translation_key,
                **extra,
            )
        )
    return rows


def test_preflight_reports_unpaired_record_as_blocking(tmp_path, capsys):
    from uuid import uuid4

    _make_pair(ResearchTopic, uuid4())
    # An unpaired published topic: no translation_key at all.
    ResearchTopic.objects.create(
        locale="en", slug="lonely", title="Lonely", status="published",
        published_at="2026-01-01T00:00:00Z",
    )

    with pytest.raises(SystemExit) as exc:
        call_command("atlas_preflight", "--json", str(tmp_path / "r.json"))

    assert exc.value.code == 1
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["blocking"], "an unpaired published record must be reported"
    assert any("lonely" in str(entry) for entry in report["blocking"])


def test_preflight_passes_when_every_candidate_is_paired(tmp_path):
    from uuid import uuid4

    _make_pair(ResearchTopic, uuid4())
    _make_pair(Profile, uuid4())

    call_command("atlas_preflight", "--json", str(tmp_path / "r.json"))
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["blocking"] == []
