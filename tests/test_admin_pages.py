"""Legacy Wagtail profile admin page tests — now under /staff/."""

from datetime import timedelta

import pytest
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client
from django.utils import timezone

from apps.content.models import LifecycleStatus, Locale, Profile, ProfileSkill


@pytest.fixture
def admin_page_client(db, admin_user):
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
def bilingual_profiles(db):
    translation_key = Profile.objects.create(
        locale=Locale.EN,
        slug="about",
        title="About",
        short_bio="Short bio",
        long_bio="Long bio",
        availability="Available",
        body="Long bio",
        seo_title="About",
        seo_description="Profile page",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    ).translation_key

    en = Profile.objects.get(locale=Locale.EN, slug="about")
    ProfileSkill.objects.create(
        profile=en,
        ordering=0,
        category="Programming",
        name="Python",
        source="Seed",
    )

    fa = Profile.objects.create(
        locale=Locale.FA,
        slug="about",
        title="درباره",
        short_bio="بیوی کوتاه",
        long_bio="بیوی بلند",
        availability="در دسترس",
        body="بیوی بلند",
        seo_title="درباره",
        seo_description="پروفایل",
        status=LifecycleStatus.DRAFT,
        translation_key=translation_key,
    )
    ProfileSkill.objects.create(
        profile=fa,
        ordering=0,
        category="Programming",
        name="Python",
        source="Seed",
    )
    return {"en": en, "fa": fa}


@pytest.fixture
def single_locale_profile(db):
    profile = Profile.objects.create(
        locale=Locale.EN,
        slug="about",
        title="About",
        short_bio="Short bio",
        long_bio="Long bio",
        availability="Available",
        body="Long bio",
        seo_title="About",
        seo_description="Profile page",
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


@pytest.mark.django_db
def test_profile_index_requires_admin_session():
    response = Client().get("/staff/profiles/")
    assert response.status_code == 302
    assert "/staff/login/" in response["Location"]


@pytest.mark.django_db
def test_profile_index_lists_bilingual_records(admin_page_client, bilingual_profiles):
    response = admin_page_client.get("/staff/profiles/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Professional admin" in content
    assert "/staff/profiles/en/about/" in content
    assert "/staff/profiles/fa/about/" in content
    assert "Same-origin records editable in the staff session." in content


@pytest.mark.django_db
def test_profile_detail_bootstraps_editor_with_locale_tabs(admin_page_client, bilingual_profiles):
    response = admin_page_client.get("/staff/profiles/fa/about/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'data-profile-editor-root' in content
    assert "profile-editor-bootstrap" in content
    assert '"apiUrl": "/api/admin/profiles/fa/about"' in content
    assert '"pageDir": "rtl"' in content
    assert '"locale": "en"' in content
    assert "/staff/profiles/en/about/" in content


@pytest.mark.django_db
def test_profile_detail_bootstraps_create_url_for_missing_locale(
    admin_page_client,
    single_locale_profile,
):
    response = admin_page_client.get("/staff/profiles/en/about/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert '"createUrl": "/api/admin/profiles/en/about/siblings/fa"' in content


@pytest.mark.django_db
def test_profile_detail_returns_404_for_unknown_profile(admin_page_client):
    response = admin_page_client.get("/staff/profiles/en/missing/")
    assert response.status_code == 404
