"""Admin profile API tests — session/CSRF/TOTP boundary and optimistic locking."""

import json
from datetime import timedelta

import pytest
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client
from django.utils import timezone

from apps.content.models import LifecycleStatus, Locale, Profile, ProfileSkill
from apps.security.models import AuditLog


@pytest.fixture
def admin_api_client(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    token = _get_new_csrf_string()
    client.cookies["csrftoken"] = token
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


@pytest.fixture
def seeded_profile(db):
    profile = Profile.objects.create(
        locale=Locale.EN,
        slug="about",
        title="About",
        short_bio="Short bio",
        long_bio="Long bio",
        availability="Available",
        body="Long bio",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )
    ProfileSkill.objects.create(
        profile=profile,
        ordering=0,
        category="Programming",
        name="Python",
        source="Seed",
    )
    return profile


def _payload():
    return {
        "title": "About",
        "slug": "about",
        "seoTitle": "About",
        "seoDescription": "Profile page",
        "shortBio": "Updated short bio",
        "longBio": "Updated long bio",
        "availability": "Open to collaboration",
        "skills": [{"category": "Programming", "name": "Python", "source": "Seed"}],
        "experience": [
            {
                "organization": "Org",
                "role": "Engineer",
                "period": "2020-Present",
                "location": "Tehran",
                "website": "https://example.com",
                "bullets": ["Did the work"],
            }
        ],
        "education": [
            {
                "institution": "Uni",
                "degree": "MS",
                "field": "Visual Communication",
                "period": "2018-2022",
                "gpa": "17.5/20",
                "thesis": "A thesis",
            }
        ],
        "publications": [{"title": "Paper", "status": "Draft"}],
        "researchProjects": [
            {
                "title": "Project",
                "summary": "Summary",
                "url": "https://example.com/project",
                "linkLabel": "Project link",
            }
        ],
        "certificates": [{"name": "Cert", "detail": "2026"}],
        "socials": [{"platform": "GitHub", "url": "https://github.com/example"}],
    }


@pytest.mark.django_db
def test_admin_profile_get_requires_authenticated_session(seeded_profile):
    response = Client().get("/api/admin/profiles/en/about")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.django_db
def test_admin_profile_get_requires_verified_totp(db, admin_user, seeded_profile):
    client = Client()
    client.force_login(admin_user)
    response = client.get("/api/admin/profiles/en/about")
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"


@pytest.mark.django_db
def test_admin_profile_get_returns_revision_and_translation_status(
    admin_api_client,
    seeded_profile,
):
    response = admin_api_client.get("/api/admin/profiles/en/about")
    assert response.status_code == 200
    data = response.json()
    assert data["revision"] == 1
    assert data["translationStatus"]["status"] == "MISSING"


@pytest.mark.django_db
def test_admin_profile_put_requires_csrf(db, admin_user, seeded_profile):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    response = client.put(
        "/api/admin/profiles/en/about",
        data=json.dumps(_payload()),
        content_type="application/json",
        HTTP_IF_MATCH="1",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_profile_put_requires_if_match(admin_api_client, seeded_profile):
    response = admin_api_client.put(
        "/api/admin/profiles/en/about",
        data=json.dumps(_payload()),
        content_type="application/json",
    )
    assert response.status_code == 428
    assert response.json()["code"] == "PRECONDITION_REQUIRED"


@pytest.mark.django_db
def test_admin_profile_put_detects_revision_conflict(admin_api_client, seeded_profile, admin_user):
    response = admin_api_client.put(
        "/api/admin/profiles/en/about",
        data=json.dumps(_payload()),
        content_type="application/json",
        HTTP_IF_MATCH="0",
        REMOTE_ADDR="203.0.113.10",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"
    assert AuditLog.objects.filter(action="admin.profile.conflict").count() == 1


@pytest.mark.django_db
def test_admin_profile_put_replaces_typed_content_and_logs(
    admin_api_client,
    seeded_profile,
    admin_user,
):
    response = admin_api_client.put(
        "/api/admin/profiles/en/about",
        data=json.dumps(_payload()),
        content_type="application/json",
        HTTP_IF_MATCH="1",
        REMOTE_ADDR="203.0.113.11",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["revision"] == 2
    assert data["shortBio"] == "Updated short bio"
    assert data["experience"][0]["organization"] == "Org"
    assert data["socials"][0]["platform"] == "GitHub"

    seeded_profile.refresh_from_db()
    assert seeded_profile.revision == 2
    assert seeded_profile.short_bio == "Updated short bio"
    assert seeded_profile.experience_entries.count() == 1

    row = AuditLog.objects.get(action="admin.profile.updated")
    assert row.user == admin_user
    assert row.model_name == "profile"
    assert row.ip == "203.0.113.11"


@pytest.mark.django_db
def test_admin_profile_put_skills_preserves_other_children(
    admin_api_client,
    seeded_profile,
):
    created = admin_api_client.put(
        "/api/admin/profiles/en/about",
        data=json.dumps(_payload()),
        content_type="application/json",
        HTTP_IF_MATCH="1",
    )
    assert created.status_code == 200
    document = admin_api_client.get("/api/admin/profiles/en/about").json()
    document["skills"] = [
        {"category": "Design", "name": "Figma", "source": "Work"},
    ]
    revision = document["revision"]
    response = admin_api_client.put(
        "/api/admin/profiles/en/about",
        data=json.dumps(document),
        content_type="application/json",
        HTTP_IF_MATCH=str(revision),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["skills"] == [
        {"category": "Design", "name": "Figma", "source": "Work"},
    ]
    assert data["experience"][0]["organization"] == "Org"
    seeded_profile.refresh_from_db()
    assert seeded_profile.skills.get().name == "Figma"
    assert seeded_profile.experience_entries.count() == 1


@pytest.mark.django_db
def test_admin_profile_create_sibling_requires_verified_session(seeded_profile):
    response = Client().post("/api/admin/profiles/en/about/siblings/fa")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.django_db
def test_admin_profile_create_sibling_creates_missing_locale(
    admin_api_client,
    seeded_profile,
    admin_user,
):
    response = admin_api_client.post(
        "/api/admin/profiles/en/about/siblings/fa",
        data=json.dumps({}),
        content_type="application/json",
        REMOTE_ADDR="203.0.113.12",
    )
    assert response.status_code == 201
    data = response.json()
    created = Profile.objects.get(locale=Locale.FA, slug="about")
    assert data["editorUrl"] == f"/admin/content/profile/{created.pk}"
    assert data["profile"]["locale"] == "fa"
    assert data["profile"]["slug"] == "about"
    assert data["profile"]["status"] == "draft"
    assert data["profile"]["revision"] == 1
    assert data["profile"]["title"] == ""
    assert data["profile"]["translationStatus"]["status"] == "COMPLETE"
    assert created.translation_key == seeded_profile.translation_key
    assert created.status == LifecycleStatus.DRAFT

    row = AuditLog.objects.get(action="admin.profile.sibling_created")
    assert row.user == admin_user
    assert row.model_name == "profile"
    assert row.ip == "203.0.113.12"


@pytest.mark.django_db
def test_admin_profile_create_sibling_rejects_existing_locale(admin_api_client, seeded_profile):
    Profile.objects.create(
        locale=Locale.FA,
        slug="about",
        title="درباره",
        status=LifecycleStatus.DRAFT,
        translation_key=seeded_profile.translation_key,
    )

    response = admin_api_client.post(
        "/api/admin/profiles/en/about/siblings/fa",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PROFILE_LOCALE_EXISTS"
