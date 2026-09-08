"""Public career-timeline projection from the published about profile.

Only the live-published ``about`` profile exposes its experience/education
entries; entry rows carry no independent publication state, so a draft
profile (or another locale) must stay fail-closed.
"""

import pytest
from django.test import Client
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _make_profile(locale="en", slug="about", status="published"):
    from apps.content.models import Profile

    return Profile.objects.create(
        locale=locale,
        slug=slug,
        title="Owner",
        status=status,
        published_at=timezone.now() if status == "published" else None,
    )


def _add_experience(profile, role, organization, period, ordering=0):
    from apps.content.models import ProfileExperience

    return ProfileExperience.objects.create(
        profile=profile,
        role=role,
        organization=organization,
        period=period,
        ordering=ordering,
    )


def _add_education(profile, degree, field, institution, period, ordering=0):
    from apps.content.models import ProfileEducation

    return ProfileEducation.objects.create(
        profile=profile,
        degree=degree,
        field=field,
        institution=institution,
        period=period,
        ordering=ordering,
    )


def test_journey_projects_published_about_entries_in_order():
    profile = _make_profile()
    _add_experience(profile, "Backend Engineer", "MCI", "2020–now", ordering=2)
    _add_experience(profile, "Product Designer", "Studio", "2018–2020", ordering=1)
    _add_education(profile, "MSc", "Visual Communication", "Shahed", "2016–2018")
    response = Client().get("/api/v1/site/en/journey")
    assert response.status_code == 200
    body = response.json()
    assert body["locale"] == "en"
    assert body["milestones"] == [
        {
            "kind": "experience",
            "title": "Product Designer",
            "subtitle": "Studio",
            "period": "2018–2020",
        },
        {
            "kind": "experience",
            "title": "Backend Engineer",
            "subtitle": "MCI",
            "period": "2020–now",
        },
        {
            "kind": "education",
            "title": "MSc, Visual Communication",
            "subtitle": "Shahed",
            "period": "2016–2018",
        },
    ]


def test_journey_is_fail_closed_for_draft_and_other_locales():
    profile = _make_profile()
    _add_experience(profile, "Draft Role", "Draft Org", "2024")
    profile.status = "draft"
    profile.published_at = None
    profile.save()
    assert Client().get("/api/v1/site/en/journey").status_code == 404
    assert Client().get("/api/v1/site/fa/journey").status_code == 404
    assert Client().get("/api/v1/site/xx/journey").status_code == 404


def test_journey_returns_empty_milestones_without_entries():
    _make_profile()
    response = Client().get("/api/v1/site/en/journey")
    assert response.status_code == 200
    assert response.json()["milestones"] == []
