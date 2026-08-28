"""Staff-gated admin OpenAPI docs (ADR-0026, ADM-1 §14 S7).

Anonymous and unverified sessions must receive 404 (not redirect). Verified
staff+OTP sessions may read Swagger UI and the OpenAPI schema.
"""

import pytest
from django.test import Client

DOCS_PATH = "/api/v1/admin/docs"
OPENAPI_PATH = "/api/v1/admin/openapi.json"


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def admin_openapi_client(admin_user, totp_device):
    client = Client()
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    return client


@pytest.mark.parametrize("path", [DOCS_PATH, OPENAPI_PATH])
def test_anonymous_openapi_paths_return_404(db, path):
    response = Client().get(path)
    assert response.status_code == 404


@pytest.mark.parametrize("path", [DOCS_PATH, OPENAPI_PATH])
def test_staff_without_otp_openapi_paths_return_404(db, admin_user, path):
    client = Client()
    client.force_login(admin_user)
    response = client.get(path)
    assert response.status_code == 404


@pytest.mark.parametrize("path", [DOCS_PATH, OPENAPI_PATH])
def test_staff_with_otp_openapi_paths_return_200(admin_openapi_client, path):
    response = admin_openapi_client.get(path)
    assert response.status_code == 200


def test_openapi_schema_is_valid_json(admin_openapi_client):
    response = admin_openapi_client.get(OPENAPI_PATH)
    body = response.json()
    assert body["info"]["title"] == "Taha Custom Admin API"
    assert "paths" in body


def test_openapi_docs_responses_include_noindex_and_no_store(admin_openapi_client):
    for path in (DOCS_PATH, OPENAPI_PATH):
        response = admin_openapi_client.get(path)
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert response.headers["Cache-Control"] == "no-store"
