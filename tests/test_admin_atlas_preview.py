"""Admin Atlas preview minting (Plan B Task 6).

The mint endpoint's obligations (plan Task 6, verbatim):

* staff + OTP + CSRF: an anonymous attempt answers 401/403;
* the capability is minted ONLY for a non-active version (409
  ``IMMUTABLE_ACTIVE`` for an active row);
* ``preview_url`` carries the capability in the URL FRAGMENT only — never a
  ``?`` query parameter;
* the round-tripped payload carries the exact scope asked for
  (``version_id, locale, purpose = atlas-preview``) and its remaining TTL is
  in ``(0, 600]``;
* the signing secret and draft content never appear in a response.

Endpoint-side verification cases belong to Plan A task 15 and are
deliberately not duplicated here.
"""

from __future__ import annotations

import time

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client

from apps.atlas.admin_preview import SECRET_STAND_IN
from apps.atlas.preview_tokens import build_atlas_preview_token, parse_atlas_preview_token
from apps.atlas.tests.factories import (
    _apply_placeholder_layout,
    _default_node_type,
    _node,
    _version,
)

BASE = "/api/v1/admin/atlas"


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
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def anon_client():
    """A logged-out client with CSRF enforcement (401/403 expected)."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_client(csrf_client, admin_user, totp_device):
    """Staff session + verified OTP + CSRF token (the minting surface)."""
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def draft_version(db):
    version = _version(status="draft", label="plan-b-task6-draft")
    _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, list(version.nodes.all()))
    version.refresh_from_db()
    return version


@pytest.fixture
def active_version(db):
    version = _version(status="active", label="plan-b-task6-active")
    _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, list(version.nodes.all()))
    version.refresh_from_db()
    return version


def _mint(client, version, payload):
    return client.post(
        f"{BASE}/versions/{version.pk}/preview-token",
        payload,
        HTTP_X_CSRFTOKEN=client.defaults.get("HTTP_X_CSRFTOKEN"),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_preview_token_is_staff_only_scoped_and_short_lived(
    admin_client, draft_version
):
    body = _mint(admin_client, draft_version, {"locale": "fa"}).json()
    assert body["version_id"] == draft_version.pk
    assert body["preview_url"].startswith("/fa/atlas/preview/#token=")
    assert "?" not in body["preview_url"]  # the credential is never a query param
    capability = body["preview_url"].split("#token=")[1]
    payload = parse_atlas_preview_token(capability)
    assert (payload.version_id, payload.locale, payload.purpose) == (
        draft_version.pk, "fa", "atlas-preview",
    )
    assert 0 < payload.exp - int(time.time()) <= 600
    assert body["expires_at"]


@pytest.mark.django_db
def test_preview_token_is_refused_for_an_active_version(
    admin_client, active_version
):
    from apps.api.admin_common import IMMUTABLE_ACTIVE

    response = _mint(admin_client, active_version, {"locale": "en"})
    assert response.status_code == 409
    assert response.json()["code"] == IMMUTABLE_ACTIVE


@pytest.mark.django_db
def test_preview_token_leaks_neither_the_secret_nor_draft_content(
    admin_client, anon_client, draft_version
):
    response = _mint(admin_client, draft_version, {"locale": "en"})
    raw = response.content.decode()
    assert (SECRET_STAND_IN not in raw)  # the never-echoed stand-in

    assert (getattr(settings, "PREVIEW_SHARE_SECRET", "") or SECRET_STAND_IN) not in raw
    assert "draft_only" not in raw  # a capability is not content
    anon_response = _mint(anon_client, draft_version, {"locale": "en"})
    assert anon_response.status_code in (401, 403)


def test_the_secret_never_lands_in_any_forged_url():
    built = build_atlas_preview_token(version_id=1, locale="en")
    assert SECRET_STAND_IN not in built


@pytest.mark.django_db
def test_preview_token_targets_the_locale_asked_for(admin_client, draft_version):
    for locale, expected in (
        ("en", "/en/atlas/preview/#token="),
        ("fa", "/fa/atlas/preview/#token="),
    ):
        response = _mint(admin_client, draft_version, {"locale": locale})
        assert expected in response.json()["preview_url"]
