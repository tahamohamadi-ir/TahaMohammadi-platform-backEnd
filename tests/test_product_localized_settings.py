"""Tests for PU-03-settings: localized draft/published site settings endpoints.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I04
Public endpoint: GET /api/v1/site/{locale}
Admin endpoints: GET|PUT /api/v1/admin/site/{locale}, POST /api/v1/admin/site/{locale}/publish
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django_otp.plugins.otp_totp.models import TOTPDevice


def test_managed_copy_is_published_as_an_isolated_locale_snapshot(admin_client):
    import json

    base = "/api/v1/admin/site/en"
    first = admin_client.get(base).json()
    response = admin_client.put(
        base,
        data=json.dumps(
            {"contentCopy": {"home.intro": "Owner introduction", "books.summary": "Selected books"}}
        ),
        content_type="application/json",
        HTTP_IF_MATCH=first["updatedAt"],
    )
    assert response.status_code == 200
    assert response.json()["contentCopy"]["home.intro"] == "Owner introduction"
    assert Client().get("/api/v1/site/en").status_code == 404
    assert admin_client.post(base + "/publish").status_code == 200
    published = Client().get("/api/v1/site/en").json()
    assert published["contentCopy"]["home.intro"] == "Owner introduction"
    assert Client().get("/api/v1/site/fa").status_code == 404
    edited = admin_client.put(
        base, data=json.dumps({"contentCopy": {}}), content_type="application/json",
        HTTP_IF_MATCH=admin_client.get(base).json()["updatedAt"],
    )
    assert edited.status_code == 200
    assert Client().get("/api/v1/site/en").json()["contentCopy"] == published["contentCopy"]
    assert admin_client.post(base + "/publish").status_code == 200
    assert Client().get("/api/v1/site/en").json()["contentCopy"] == {}


@pytest.mark.parametrize(
    "copy",
    [{"bad key": "x"}, {"home.intro": ["not text"]}, {"home.intro": "x" * 10001}],
)
def test_managed_copy_rejects_invalid_values_without_partial_update(admin_client, copy):
    import json

    base = "/api/v1/admin/site/en"
    before = admin_client.get(base).json()
    response = admin_client.put(
        base,
        data=json.dumps({"brandName": "must not save", "contentCopy": copy}),
        content_type="application/json",
        HTTP_IF_MATCH=before["updatedAt"],
    )
    assert response.status_code == 400
    assert admin_client.get(base).json()["brandName"] == before["brandName"]


def serialize_dt(dt: datetime) -> str:
    r = dt.isoformat()
    if dt.microsecond:
        r = r[:23] + r[26:]
    if r.endswith("+00:00"):
        r = r.removesuffix("+00:00") + "Z"
    return r


@pytest.fixture(autouse=True)
def db_access(db):
    """Ensure database access for all tests."""
    pass


@pytest.fixture
def admin_client():
    """Client with staff session and verified OTP device."""
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        username="admin-settings",
        email="admin-settings@example.com",
        password="ValidPassword123!",
    )
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    client = Client()
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session["django_otp_device_id"] = device.persistent_id
    session.save()
    csrf_token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token
    return client


class TestProductLocalizedSettings:
    """Test suite for public and admin localized site settings."""

    def test_public_endpoint_initial_gap_fails(self):
        """Focused failing test before implementation: route exists and answers."""
        client = Client()
        response = client.get("/api/v1/site/fa")
        # Before implementation, returns 404 (route not found in ninja)
        # After implementation with no published settings, returns 404 with error envelope
        assert response.status_code == 404
        data = response.json()
        assert "code" in data
        assert data["code"] == "NOT_FOUND"

    def test_public_returns_404_when_draft_only(self):
        """Public endpoint returns 404 when localized settings exist only as draft."""
        from apps.siteconfig.models import LocalizedSiteSettings

        LocalizedSiteSettings.objects.create(
            locale="fa",
            brand_name="طه محمدی",
            tagline="پژوهشگر هوش مصنوعی",
            status="draft",
            published_payload=None,
        )
        client = Client()
        response = client.get("/api/v1/site/fa")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"

    def test_public_returns_200_when_published(self):
        """Public endpoint returns 200 and exact schema when settings are published."""
        from apps.siteconfig.models import LocalizedSiteSettings

        now_str = serialize_dt(datetime.now())
        published_payload = {
            "locale": "fa",
            "revision": "rev-test-123",
            "brandName": "طه محمدی",
            "tagline": "پژوهشگر هوش مصنوعی",
            "footerText": "تمام حقوق محفوظ است.",
            "seo": {
                "title": "وبگاه طه محمدی",
                "description": "پورتفولیوی پژوهشی",
            },
            "navLinks": [{"label": "درباره", "href": "/about"}],
            "audienceLinks": [
                {"kind": "research", "label": "پژوهشگران", "href": "/research"}
            ],
            "scene": {
                "graphPreset": "atlas-v2",
                "portalPreset": "arch-v2",
                "motion": "full",
                "density": "standard",
            },
            "updatedAt": now_str,
        }
        LocalizedSiteSettings.objects.create(
            locale="fa",
            brand_name="طه محمدی",
            tagline="پژوهشگر هوش مصنوعی",
            status="published",
            published_payload=published_payload,
        )
        client = Client()
        response = client.get("/api/v1/site/fa")
        assert response.status_code == 200
        data = response.json()
        assert data["locale"] == "fa"
        assert data["revision"] == "rev-test-123"
        assert data["brandName"] == "طه محمدی"
        assert data["tagline"] == "پژوهشگر هوش مصنوعی"
        assert data["seo"]["title"] == "وبگاه طه محمدی"
        assert data["navLinks"] == [{"label": "درباره", "href": "/about"}]
        assert data["audienceLinks"] == [
            {"kind": "research", "label": "پژوهشگران", "href": "/research"}
        ]
        assert data["scene"]["graphPreset"] == "atlas-v2"
        assert data["scene"]["portalPreset"] == "arch-v2"

    def test_public_exact_locale_isolation(self):
        """Published FA settings do not make EN settings available."""
        from apps.siteconfig.models import LocalizedSiteSettings

        LocalizedSiteSettings.objects.create(
            locale="fa",
            brand_name="طه محمدی",
            status="published",
            published_payload={
                "locale": "fa",
                "revision": "rev-fa",
                "brandName": "طه محمدی",
                "tagline": "",
                "footerText": "",
                "seo": {"title": "", "description": ""},
                "navLinks": [],
                "audienceLinks": [],
                "scene": {
                    "graphPreset": "atlas-v2",
                    "portalPreset": "arch-v2",
                    "motion": "full",
                    "density": "standard",
                },
                "updatedAt": serialize_dt(datetime.now()),
            },
        )
        client = Client()
        # FA returns 200
        assert client.get("/api/v1/site/fa").status_code == 200
        # EN returns 404 with no fallback
        res_en = client.get("/api/v1/site/en")
        assert res_en.status_code == 404
        assert res_en.json()["code"] == "NOT_FOUND"

    def test_public_unsupported_locale_returns_404(self):
        """Unsupported locale returns 404."""
        client = Client()
        res = client.get("/api/v1/site/fr")
        assert res.status_code == 404
        assert res.json()["code"] == "NOT_FOUND"

    def test_admin_get_requires_auth(self):
        """Unauthenticated call to admin site settings returns 401/403."""
        anon = Client()
        res = anon.get("/api/v1/admin/site/fa")
        assert res.status_code in (401, 403)

    def test_admin_get_returns_draft_state(self, admin_client):
        """Admin GET returns draft settings and If-Match compatible updatedAt."""
        from apps.siteconfig.models import LocalizedSiteSettings

        LocalizedSiteSettings.objects.create(
            locale="fa",
            brand_name="طه محمدی (پیش‌نویس)",
            tagline="تگ‌لاین پیش‌نویس",
            status="draft",
        )
        res = admin_client.get("/api/v1/admin/site/fa")
        assert res.status_code == 200
        data = res.json()
        assert data["brandName"] == "طه محمدی (پیش‌نویس)"
        assert data["tagline"] == "تگ‌لاین پیش‌نویس"
        assert data["status"] == "draft"
        assert "updatedAt" in data

    def test_admin_put_requires_if_match_and_checks_conflict(self, admin_client):
        """Admin PUT enforces If-Match header and returns 409 on timestamp mismatch."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(
            locale="fa",
            brand_name="عنوان اولیه",
            status="draft",
        )
        payload = {"brandName": "عنوان ویرایش‌شده"}

        # Missing If-Match -> 409
        res_no_header = admin_client.put(
            "/api/v1/admin/site/fa",
            data=payload,
            content_type="application/json",
        )
        assert res_no_header.status_code == 409
        assert res_no_header.json()["code"] == "CONFLICT"

        # Stale If-Match -> 409
        res_stale = admin_client.put(
            "/api/v1/admin/site/fa",
            data=payload,
            content_type="application/json",
            HTTP_IF_MATCH='"2020-01-01T00:00:00.000Z"',
        )
        assert res_stale.status_code == 409
        assert res_stale.json()["code"] == "CONFLICT"

        # Correct If-Match -> 200
        correct_header = f'"{serialize_dt(item.updated_at)}"'
        res_ok = admin_client.put(
            "/api/v1/admin/site/fa",
            data=payload,
            content_type="application/json",
            HTTP_IF_MATCH=correct_header,
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["brandName"] == "عنوان ویرایش‌شده"

    def test_admin_put_validates_nav_links(self, admin_client):
        """Admin PUT rejects malformed links, javascript: and unknown routes."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(locale="fa")
        if_match = f'"{serialize_dt(item.updated_at)}"'

        # javascript: URL rejected
        res_js = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"navLinks": [{"label": "XSS", "href": "javascript:alert(1)"}]},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_js.status_code == 400
        assert res_js.json()["code"] == "VALIDATION"

        # unknown local route rejected
        res_unk = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"navLinks": [{"label": "Unknown", "href": "/unknown-random-path"}]},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_unk.status_code == 400

        # more than 20 nav links rejected
        too_many = [{"label": f"L{i}", "href": "/about"} for i in range(21)]
        res_many = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"navLinks": too_many},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_many.status_code == 400

        # valid site-relative and https link accepted
        valid_links = [
            {"label": "درباره", "href": "/about"},
            {"label": "گیت‌هاب", "href": "https://github.com/tahamohammadi"},
        ]
        res_ok = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"navLinks": valid_links},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_ok.status_code == 200
        assert len(res_ok.json()["navLinks"]) == 2

    def test_admin_put_validates_audience_links(self, admin_client):
        """Admin PUT enforces max 2 audience links and valid kind enum."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(locale="fa")
        if_match = f'"{serialize_dt(item.updated_at)}"'

        # Invalid kind
        res_bad_kind = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"audienceLinks": [{"kind": "invalid", "label": "L", "href": "/research"}]},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_bad_kind.status_code == 400

        # Max 2 exceeded
        res_3 = admin_client.put(
            "/api/v1/admin/site/fa",
            data={
                "audienceLinks": [
                    {"kind": "research", "label": "R1", "href": "/research"},
                    {"kind": "employment", "label": "E1", "href": "/about"},
                    {"kind": "research", "label": "R2", "href": "/research"},
                ]
            },
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_3.status_code == 400

        # Valid 2 entries accepted
        res_ok = admin_client.put(
            "/api/v1/admin/site/fa",
            data={
                "audienceLinks": [
                    {"kind": "research", "label": "برای پژوهشگران", "href": "/research"},
                    {"kind": "employment", "label": "برای کارفرمایان", "href": "/about"},
                ]
            },
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_ok.status_code == 200
        assert len(res_ok.json()["audienceLinks"]) == 2

    def test_admin_put_validates_scene_presets(self, admin_client):
        """Admin PUT enforces V1 preset choices: atlas-v2 and arch-v2."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(locale="fa")
        if_match = f'"{serialize_dt(item.updated_at)}"'

        # Unknown graphPreset rejected
        res_bad_graph = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"scene": {"graphPreset": "unknown-preset"}},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_bad_graph.status_code == 400

        # Unknown portalPreset rejected
        res_bad_portal = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"scene": {"portalPreset": "unknown-portal"}},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_bad_portal.status_code == 400

        # Unknown motion rejected
        res_bad_motion = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"scene": {"motion": "fast"}},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_bad_motion.status_code == 400

        # Valid presets accepted
        res_ok = admin_client.put(
            "/api/v1/admin/site/fa",
            data={
                "scene": {
                    "graphPreset": "atlas-v2",
                    "portalPreset": "arch-v2",
                    "motion": "reduced",
                    "density": "low",
                }
            },
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["scene"]["motion"] == "reduced"
        assert res_ok.json()["scene"]["density"] == "low"

    def test_admin_publish_publishes_draft_snapshot_to_public(self, admin_client):
        """Publishing draft snapshot makes it available on the public endpoint."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(
            locale="fa",
            brand_name="طه محمدی (پیش‌نویس)",
            tagline="تگ‌لاین جدید",
            status="draft",
        )
        client = Client()
        # Public is 404 prior to publish
        assert client.get("/api/v1/site/fa").status_code == 404

        # Call admin publish
        res_pub = admin_client.post("/api/v1/admin/site/fa/publish")
        assert res_pub.status_code == 200
        pub_data = res_pub.json()
        assert pub_data["ok"] is True
        assert pub_data["locale"] == "fa"
        assert "revision" in pub_data

        # Public now returns 200 with the published draft snapshot
        res_public = client.get("/api/v1/site/fa")
        assert res_public.status_code == 200
        data = res_public.json()
        assert data["brandName"] == "طه محمدی (پیش‌نویس)"
        assert data["tagline"] == "تگ‌لاین جدید"

        # Further draft edit does not change public until published again
        admin_client.put(
            "/api/v1/admin/site/fa",
            data={"brandName": "ویرایش بعدی هنوز منتشر نشده"},
            content_type="application/json",
            HTTP_IF_MATCH=f'"{serialize_dt(LocalizedSiteSettings.objects.get(pk=item.pk).updated_at)}"',
        )
        # Public still returns the published snapshot
        res_public2 = client.get("/api/v1/site/fa")
        assert res_public2.status_code == 200
        assert res_public2.json()["brandName"] == "طه محمدی (پیش‌نویس)"

    def test_admin_put_requires_auth(self):
        """Unauthenticated PUT to localized settings returns 401/403."""
        anon = Client(enforce_csrf_checks=True)
        res = anon.put(
            "/api/v1/admin/site/fa",
            data={"brandName": "X"},
            content_type="application/json",
        )
        assert res.status_code in (401, 403)

    def test_admin_publish_requires_auth(self):
        """Unauthenticated POST publish returns 401/403."""
        anon = Client(enforce_csrf_checks=True)
        res = anon.post("/api/v1/admin/site/fa/publish")
        assert res.status_code in (401, 403)

    def test_admin_put_requires_csrf(self, admin_client):
        """PUT without CSRF header is rejected even with valid session+OTP."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(locale="fa")
        if_match = f'"{serialize_dt(item.updated_at)}"'
        # Strip CSRF header to prove explicit CSRF enforcement on mutation.
        admin_client.defaults.pop("HTTP_X_CSRFTOKEN", None)
        try:
            res = admin_client.put(
                "/api/v1/admin/site/fa",
                data={"brandName": "بدون CSRF"},
                content_type="application/json",
                HTTP_IF_MATCH=if_match,
            )
            assert res.status_code == 403
            assert res.json()["code"] == "CSRF_FAILED"
        finally:
            csrf_token = admin_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
            admin_client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token

    def test_admin_publish_requires_csrf(self, admin_client):
        """POST publish without CSRF header is rejected."""
        from apps.siteconfig.models import LocalizedSiteSettings

        LocalizedSiteSettings.objects.create(locale="fa", status="draft")
        admin_client.defaults.pop("HTTP_X_CSRFTOKEN", None)
        try:
            res = admin_client.post("/api/v1/admin/site/fa/publish")
            assert res.status_code == 403
            assert res.json()["code"] == "CSRF_FAILED"
        finally:
            csrf_token = admin_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
            admin_client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token

    def test_admin_put_rejects_data_http_and_unknown_hrefs(self, admin_client):
        """data:/http: hrefs and unknown local routes are rejected; locale prefix allowed."""
        from apps.siteconfig.models import LocalizedSiteSettings

        item = LocalizedSiteSettings.objects.create(locale="fa")
        if_match = f'"{serialize_dt(item.updated_at)}"'
        for bad_href in (
            "data:text/html,<p>x</p>",
            "http://example.com/x",
            "/unknown-random-path-xyz",
        ):
            res = admin_client.put(
                "/api/v1/admin/site/fa",
                data={"navLinks": [{"label": "بد", "href": bad_href}]},
                content_type="application/json",
                HTTP_IF_MATCH=if_match,
            )
            assert res.status_code == 400, bad_href
            assert res.json()["code"] == "VALIDATION"
        # Locale-prefixed registered path is accepted.
        res_ok = admin_client.put(
            "/api/v1/admin/site/fa",
            data={"navLinks": [{"label": "درباره", "href": "/fa/about"}]},
            content_type="application/json",
            HTTP_IF_MATCH=if_match,
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["navLinks"][0]["href"] == "/fa/about"
