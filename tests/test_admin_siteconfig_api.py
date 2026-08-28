"""Site-configuration admin API tests (ADR-0026, ADM-5).

Covers the /api/v1/admin/site, /tags and /featured contracts: settings
singleton read/write with If-Match optimistic locking, topic tag CRUD
(including the article-reference IN_USE guard), and featured time-window
spotlights (create/list-current/update/delete) plus the shared security
guards.
"""

import json
from datetime import UTC, timedelta

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import Article, Landing, TopicTag
from apps.siteconfig.models import FeaturedItem


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def csrf_client():
    """CSRF-enforcing client used for the enforcement tests (no token set)."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device):
    """Authenticated staff client with a verified OTP session and CSRF token."""
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def _post_json(client, path, payload):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _put_json(client, path, payload, updated_at):
    return client.put(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )


def _make_landing(*, locale, slug, title, status="draft"):
    return Landing.objects.create(
        locale=locale,
        slug=slug,
        title=title,
        status=status,
    )


def _iso(dt):
    return dt.isoformat()


def _create_tag(client, name, locale, slug=None):
    payload = {"name": name, "locale": locale}
    if slug is not None:
        payload["slug"] = slug
    return _post_json(client, "/api/v1/admin/tags", payload)


# --- site settings ----------------------------------------------------------


def test_settings_get_defaults(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/site")
    assert response.status_code == 200
    body = response.json()
    assert body["brandName"] == "Taha Mohammadi"
    assert body["tagline"] == ""
    assert body["primaryColor"] == "#1f2937"
    assert body["navLinks"] == []
    assert body["seoDefaultTitle"] == ""
    assert body["seoDefaultDescription"] == ""
    assert body["currentCvMediaId"] is None
    assert body["currentResumeMediaId"] is None
    assert body["currentCv"] is None
    assert body["currentResume"] is None
    assert "updatedAt" in body


def test_settings_put_updates_fields(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    assert got.status_code == 200

    payload = {
        "brandName": "Taha Mohammadi Lab",
        "tagline": "Research & writing",
        "footerText": "All rights reserved.",
        "primaryColor": "#0f172a",
        "navLinks": [
            {"label": "Home", "href": "/", "locale": "en"},
            {"label": "Blog", "href": "/en/blog/", "locale": "en"},
        ],
        "seoDefaultTitle": "Default title",
        "seoDefaultDescription": "Default description",
    }
    put = _put_json(admin_api_client, "/api/v1/admin/site", payload, got.json()["updatedAt"])
    assert put.status_code == 200
    body = put.json()
    assert body["brandName"] == "Taha Mohammadi Lab"
    assert body["tagline"] == "Research & writing"
    assert body["primaryColor"] == "#0f172a"
    assert len(body["navLinks"]) == 2
    assert body["navLinks"][0] == {"label": "Home", "href": "/", "locale": "en"}
    assert body["seoDefaultTitle"] == "Default title"


def test_settings_put_stale_if_match_409(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    stale = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"brandName": "X"},
        "2000-01-01T00:00:00+00:00",
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "CONFLICT"
    assert body["currentUpdatedAt"] == got.json()["updatedAt"]


def test_settings_put_invalid_primary_color_400(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    r = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"primaryColor": "red"},
        got.json()["updatedAt"],
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_settings_put_invalid_nav_link_400(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    r = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"navLinks": [{"label": "X", "href": "ftp://x", "locale": "en"}]},
        got.json()["updatedAt"],
    )
    assert r.status_code == 400
    assert "navLinks[0].href" in r.json()["fields"]


def test_settings_guards(csrf_client, admin_user):
    anonymous = csrf_client.get("/api/v1/admin/site")
    assert anonymous.status_code == 401

    csrf_client.force_login(admin_user)
    no_otp = csrf_client.get("/api/v1/admin/site")
    assert no_otp.status_code == 403
    assert no_otp.json()["code"] == "OTP_REQUIRED"


# --- topic tags -------------------------------------------------------------


def test_tag_create_201(admin_api_client):
    response = _create_tag(admin_api_client, "Python", "en")
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Python"
    assert body["slug"] == "python"
    assert body["locale"] == "en"
    assert body["articleCount"] == 0


def test_tag_create_duplicate_409(admin_api_client):
    assert _create_tag(admin_api_client, "Python", "en").status_code == 201
    duplicate = _create_tag(admin_api_client, "Python", "en")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE"


def test_tag_list_filters_and_pagination(admin_api_client):
    _create_tag(admin_api_client, "Python", "en")
    _create_tag(admin_api_client, "Django", "en")
    _create_tag(admin_api_client, "جانگو", "fa", slug="django-fa")

    by_q = admin_api_client.get("/api/v1/admin/tags?q=django")
    assert by_q.status_code == 200
    assert by_q.json()["total"] == 2

    by_locale = admin_api_client.get("/api/v1/admin/tags?locale=fa")
    assert by_locale.status_code == 200
    assert by_locale.json()["total"] == 1
    assert by_locale.json()["items"][0]["slug"] == "django-fa"

    paged = admin_api_client.get("/api/v1/admin/tags?pageSize=1&page=2")
    assert paged.status_code == 200
    assert paged.json()["total"] == 3
    assert len(paged.json()["items"]) == 1

    bad_locale = admin_api_client.get("/api/v1/admin/tags?locale=xx")
    assert bad_locale.status_code == 400


def test_tag_update_200(admin_api_client):
    created = _create_tag(admin_api_client, "Python", "en")
    tag_id = created.json()["id"]
    updated = _put_json(
        admin_api_client,
        f"/api/v1/admin/tags/{tag_id}",
        {"name": "Python 3"},
        "ignored",
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Python 3"


def test_tag_delete_204(admin_api_client):
    created = _create_tag(admin_api_client, "Python", "en")
    tag_id = created.json()["id"]
    response = admin_api_client.delete(f"/api/v1/admin/tags/{tag_id}")
    # Django's test client strips 204 bodies (RFC 9112); assert the status and
    # the side effect. The view still returns {"ok": true} at the WSGI level.
    assert response.status_code == 204
    assert not TopicTag.objects.filter(pk=tag_id).exists()


def test_tag_delete_in_use_409(admin_api_client):
    created = _create_tag(admin_api_client, "Django", "en")
    tag_id = created.json()["id"]
    article = Article.objects.create(
        locale="en",
        slug="art",
        title="Art",
        status="draft",
        body="<p>hi</p>",
    )
    article.topic_tags.add(TopicTag.objects.get(pk=tag_id))

    response = admin_api_client.delete(f"/api/v1/admin/tags/{tag_id}")
    assert response.status_code == 409
    assert response.json()["code"] == "IN_USE"


def test_tag_delete_missing_404(admin_api_client):
    response = admin_api_client.delete("/api/v1/admin/tags/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_tag_guards(csrf_client, admin_user):
    payload = json.dumps({"name": "X", "locale": "en"})

    anonymous = csrf_client.post(
        "/api/v1/admin/tags",
        data=payload,
        content_type="application/json",
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTH_REQUIRED"

    csrf_client.force_login(admin_user)
    no_otp = csrf_client.post(
        "/api/v1/admin/tags",
        data=payload,
        content_type="application/json",
    )
    assert no_otp.status_code == 403
    assert no_otp.json()["code"] == "OTP_REQUIRED"


# --- featured items ---------------------------------------------------------


def test_featured_create_201(admin_api_client):
    _make_landing(locale="en", slug="home", title="Home")
    now = timezone.now()
    response = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Spotlight",
            "targetEntity": "landing",
            "targetSlug": "home",
            "locale": "en",
            "startAt": _iso(now),
            "endAt": _iso(now + timedelta(days=7)),
            "isActive": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Spotlight"
    assert body["targetEntity"] == "landing"
    assert body["targetSlug"] == "home"
    assert body["locale"] == "en"
    assert body["isActive"] is True
    assert body["startAt"] is not None


def test_featured_list_current_filter(admin_api_client):
    _make_landing(locale="en", slug="a", title="A")
    _make_landing(locale="en", slug="b", title="B")
    _make_landing(locale="en", slug="c", title="C")
    now = timezone.now()
    _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Active",
            "targetEntity": "landing",
            "targetSlug": "a",
            "locale": "en",
            "startAt": _iso(now - timedelta(days=1)),
        },
    )
    _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Expired",
            "targetEntity": "landing",
            "targetSlug": "b",
            "locale": "en",
            "startAt": _iso(now - timedelta(days=2)),
            "endAt": _iso(now - timedelta(days=1)),
        },
    )
    _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Future",
            "targetEntity": "landing",
            "targetSlug": "c",
            "locale": "en",
            "startAt": _iso(now + timedelta(days=1)),
        },
    )

    all_items = admin_api_client.get("/api/v1/admin/featured")
    assert all_items.status_code == 200
    assert all_items.json()["total"] == 3

    current = admin_api_client.get("/api/v1/admin/featured?current=true")
    assert current.status_code == 200
    assert current.json()["total"] == 1
    assert current.json()["items"][0]["title"] == "Active"


def test_featured_invalid_target_400(admin_api_client):
    _make_landing(locale="en", slug="home", title="Home")
    bad_entity = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Bad",
            "targetEntity": "widget",
            "targetSlug": "home",
            "locale": "en",
            "startAt": _iso(timezone.now()),
        },
    )
    assert bad_entity.status_code == 400
    assert bad_entity.json()["code"] == "VALIDATION"

    missing_slug = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Missing",
            "targetEntity": "landing",
            "targetSlug": "nope",
            "locale": "en",
            "startAt": _iso(timezone.now()),
        },
    )
    assert missing_slug.status_code == 400
    assert missing_slug.json()["code"] == "VALIDATION"


def test_featured_end_before_start_400(admin_api_client):
    _make_landing(locale="en", slug="home", title="Home")
    now = timezone.now()
    response = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "X",
            "targetEntity": "landing",
            "targetSlug": "home",
            "locale": "en",
            "startAt": _iso(now),
            "endAt": _iso(now - timedelta(days=1)),
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_featured_update_200(admin_api_client):
    _make_landing(locale="en", slug="home", title="Home")
    created = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Spot",
            "targetEntity": "landing",
            "targetSlug": "home",
            "locale": "en",
            "startAt": _iso(timezone.now()),
        },
    )
    assert created.status_code == 201
    item = FeaturedItem.objects.get(pk=created.json()["id"])

    updated = _put_json(
        admin_api_client,
        f"/api/v1/admin/featured/{item.pk}",
        {"title": "Updated", "isActive": False},
        item.updated_at.isoformat(),
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Updated"
    assert body["isActive"] is False
    assert body["targetEntity"] == "landing"


def test_featured_delete_204(admin_api_client):
    _make_landing(locale="en", slug="home", title="Home")
    created = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Spot",
            "targetEntity": "landing",
            "targetSlug": "home",
            "locale": "en",
            "startAt": _iso(timezone.now()),
        },
    )
    item_id = created.json()["id"]
    response = admin_api_client.delete(f"/api/v1/admin/featured/{item_id}")
    # Django's test client strips 204 bodies (RFC 9112); assert the status and
    # the side effect. The view still returns {"ok": true} at the WSGI level.
    assert response.status_code == 204
    assert not FeaturedItem.objects.filter(pk=item_id).exists()


def test_featured_guards(csrf_client, admin_user):
    anonymous = csrf_client.get("/api/v1/admin/featured")
    assert anonymous.status_code == 401

    csrf_client.force_login(admin_user)
    no_otp = csrf_client.get("/api/v1/admin/featured")
    assert no_otp.status_code == 403
    assert no_otp.json()["code"] == "OTP_REQUIRED"

def test_settings_put_rejects_protocol_relative_href(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    bad = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"navLinks": [{"label": "Evil", "href": "//attacker.example", "locale": "en"}]},
        got.json()["updatedAt"],
    )
    assert bad.status_code == 400


def test_settings_put_rejects_overlong_href(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    href = "/" + "x" * 600
    bad = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"navLinks": [{"label": "Long", "href": href, "locale": "en"}]},
        got.json()["updatedAt"],
    )
    assert bad.status_code == 400


def test_settings_put_rejects_over_20_nav_links(admin_api_client):
    got = admin_api_client.get("/api/v1/admin/site")
    links = [{"label": f"L{i}", "href": f"/p{i}", "locale": "en"} for i in range(21)]
    bad = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"navLinks": links},
        got.json()["updatedAt"],
    )
    assert bad.status_code == 400


def test_settings_put_current_cv_pdf_and_public_site(admin_api_client, media_root):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.media.models import Media

    PDF_MIN = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    PNG_1X1 = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    pdf = Media(
        title="Academic CV",
        alt_text="Full career profile",
        is_active=True,
        file=SimpleUploadedFile("cv.pdf", PDF_MIN, content_type="application/pdf"),
    )
    pdf.save()
    inactive = Media(
        title="Draft resume",
        is_active=False,
        file=SimpleUploadedFile("draft.pdf", PDF_MIN, content_type="application/pdf"),
    )
    inactive.save()
    png = Media(
        title="Photo",
        is_active=True,
        file=SimpleUploadedFile("photo.png", PNG_1X1, content_type="image/png"),
    )
    png.save()

    got = admin_api_client.get("/api/v1/admin/site")
    bad_png = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"currentCvMediaId": png.pk},
        got.json()["updatedAt"],
    )
    assert bad_png.status_code == 400
    assert "currentCvMediaId" in bad_png.json()["fields"]

    got = admin_api_client.get("/api/v1/admin/site")
    put = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {
            "primaryColor": "#087c73",
            "currentCvMediaId": pdf.pk,
            "currentResumeMediaId": inactive.pk,
        },
        got.json()["updatedAt"],
    )
    assert put.status_code == 200
    body = put.json()
    assert body["currentCvMediaId"] == pdf.pk
    assert body["currentResumeMediaId"] == inactive.pk
    assert body["currentCv"]["title"] == "Academic CV"
    assert body["currentCv"]["url"].startswith("/media/")

    public = admin_api_client.get("/api/site")
    assert public.status_code == 200
    pub = public.json()
    assert pub["primaryColor"] == "#087c73"
    assert len(pub["downloads"]) == 1
    assert pub["downloads"][0]["kind"] == "academic_cv"
    assert pub["downloads"][0]["href"].startswith("/media/")
    assert pub["downloads"][0]["title"] == "Academic CV"

    got = admin_api_client.get("/api/v1/admin/site")
    cleared = _put_json(
        admin_api_client,
        "/api/v1/admin/site",
        {"currentCvMediaId": None, "currentResumeMediaId": None},
        got.json()["updatedAt"],
    )
    assert cleared.status_code == 200
    assert cleared.json()["currentCvMediaId"] is None
    assert cleared.json()["currentResumeMediaId"] is None

    public_empty = admin_api_client.get("/api/site")
    assert public_empty.json()["downloads"] == []


def test_featured_singleton_created_on_get(admin_api_client):
    from apps.siteconfig.models import SiteSettings

    # Migration 0003 seeds a default row (owner contact data); remove it to
    # exercise the create-on-first-GET path itself.
    SiteSettings.objects.all().delete()
    assert SiteSettings.objects.count() == 0
    admin_api_client.get("/api/v1/admin/site")
    assert SiteSettings.objects.count() == 1
    admin_api_client.get("/api/v1/admin/site")
    assert SiteSettings.objects.count() == 1


def test_featured_delete_404(admin_api_client):
    resp = admin_api_client.delete("/api/v1/admin/featured/999999")
    assert resp.status_code == 404


def test_featured_exactly_one_active(admin_api_client):
    from datetime import datetime

    from apps.siteconfig.models import FeaturedItem

    _make_landing(locale="en", slug="home", title="Home", status="published")
    _make_landing(locale="fa", slug="خانه", title="خانه", status="published")
    first = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "First",
            "targetEntity": "landing",
            "targetSlug": "home",
            "locale": "en",
            "startAt": _iso(datetime(2026, 1, 1, tzinfo=UTC)),
            "isActive": True,
        },
    )
    assert first.status_code == 201
    second = _post_json(
        admin_api_client,
        "/api/v1/admin/featured",
        {
            "title": "Second",
            "targetEntity": "landing",
            "targetSlug": "خانه",
            "locale": "fa",
            "startAt": _iso(datetime(2026, 2, 1, tzinfo=UTC)),
            "isActive": True,
        },
    )
    assert second.status_code == 201
    assert FeaturedItem.objects.filter(is_active=True).count() == 1
    assert FeaturedItem.objects.get(pk=first.json()["id"]).is_active is False

