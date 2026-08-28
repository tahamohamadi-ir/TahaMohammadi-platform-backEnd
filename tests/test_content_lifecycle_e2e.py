"""ADM-6 local JSON lifecycle: create → edit → publish → public fa/en."""

import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client


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
def admin_api_client(admin_user, totp_device):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _article_fields():
    return {
        "excerpt": "Lifecycle excerpt.",
        "body": " ".join(["word"] * 401),
        "readingTimeMinutes": "3",
    }


@pytest.mark.django_db
def test_article_create_edit_publish_public_json_fa_en(admin_api_client):
    anonymous = Client()
    created = {}
    for locale in ("en", "fa"):
        response = _post_json(
            admin_api_client,
            "/api/v1/admin/content/article",
            {
                "locale": locale,
                "slug": "lifecycle-e2e",
                "title": f"Draft {locale}",
                "fields": _article_fields(),
            },
        )
        assert response.status_code == 201, response.content
        created[locale] = response.json()
        hidden = anonymous.get(f"/api/articles/{locale}/lifecycle-e2e")
        assert hidden.status_code == 404

    for locale, detail in created.items():
        updated = admin_api_client.put(
            f"/api/v1/admin/content/article/{detail['id']}",
            data=json.dumps({"title": f"Published {locale}"}),
            content_type="application/json",
            HTTP_IF_MATCH=f'"{detail["updatedAt"]}"',
        )
        assert updated.status_code == 200
        with patch("apps.api.admin_content.invoke_static_rebuild") as mocked:
            published = _post_json(
                admin_api_client,
                f"/api/v1/admin/content/article/{detail['id']}/transition",
                {"to": "published", "reason": "lifecycle e2e"},
            )
            assert published.status_code == 200
            mocked.assert_called_once()
        public = anonymous.get(f"/api/articles/{locale}/lifecycle-e2e")
        assert public.status_code == 200
        body = public.json()
        assert body["title"] == f"Published {locale}"
        assert body["slug"] == "lifecycle-e2e"
