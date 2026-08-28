"""Custom admin media API tests (ADR-0026, ADM-2).

Covers the /api/v1/admin/media contract: OTP guard, multipart upload (accept /
reject / missing file), list filters + pagination, optimistically-locked update
(If-Match), orphan listing (rows with zero FK usage) and detail 404.
"""

import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.media.models import Media

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

PDF_MIN = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def csrf_client():
    """CSRF-enforcing client used for the enforcement tests (no token set)."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device, media_root):
    """Authenticated staff client with a verified OTP session and CSRF token."""
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


def _upload(
    client,
    *,
    name="photo.png",
    content=PNG_1X1,
    content_type="image/png",
    title=None,
    extra=None,
):
    data = {}
    if title is not None:
        data["title"] = title
    if extra:
        data.update(extra)
    data["file"] = SimpleUploadedFile(name, content, content_type=content_type)
    return client.post("/api/v1/admin/media", data)


def _make_media(*, title, is_active=False, mime="image/png", content=PNG_1X1):
    return Media.objects.create(
        file=SimpleUploadedFile(f"{title}.png", content, content_type=mime),
        title=title,
        is_active=is_active,
    )


def test_list_requires_otp(csrf_client, admin_user):
    csrf_client.force_login(admin_user)  # staff but NO otp session
    response = csrf_client.get("/api/v1/admin/media")
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"


def test_upload_image_201(admin_api_client):
    response = _upload(admin_api_client, title="Hero image")
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Hero image"
    assert data["mime"] == "image/png"
    assert data["isActive"] is False
    assert data["url"] is not None
    assert data["usageCount"] == 0

    media = Media.objects.get(pk=data["id"])
    assert media.title == "Hero image"
    assert media.mime == "image/png"
    assert media.is_active is False


def test_upload_title_falls_back_to_filename(admin_api_client):
    response = _upload(admin_api_client, title="")
    assert response.status_code == 201
    assert response.json()["title"] == "photo.png"


def test_upload_rejects_bad_type_400(admin_api_client):
    response = _upload(
        admin_api_client,
        name="notes.txt",
        content=b"plain text",
        content_type="text/plain",
        title="Notes",
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION"
    assert "file" in body["fields"]


def test_upload_missing_file_400(admin_api_client):
    response = admin_api_client.post("/api/v1/admin/media", {"title": "No file"})
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_list_filters_and_pagination(admin_api_client):
    _make_media(title="Alpha image", is_active=True)
    _make_media(title="Beta image", is_active=True)
    _make_media(title="Gamma image", is_active=False)
    _make_media(title="Document pdf", is_active=False, mime="application/pdf", content=PDF_MIN)

    all_response = admin_api_client.get("/api/v1/admin/media")
    assert all_response.status_code == 200
    body = all_response.json()
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["pageSize"] == 20
    assert len(body["items"]) == 4

    search = admin_api_client.get("/api/v1/admin/media", {"q": "Beta"})
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["title"] == "Beta image"

    images = admin_api_client.get("/api/v1/admin/media", {"type": "image"})
    assert images.json()["total"] == 3

    active = admin_api_client.get("/api/v1/admin/media", {"active": "true"})
    assert active.json()["total"] == 2
    assert all(item["isActive"] for item in active.json()["items"])

    inactive = admin_api_client.get("/api/v1/admin/media", {"active": "false"})
    assert inactive.json()["total"] == 2
    assert all(not item["isActive"] for item in inactive.json()["items"])

    paged = admin_api_client.get("/api/v1/admin/media", {"pageSize": 2})
    paged_body = paged.json()
    assert paged_body["total"] == 4
    assert len(paged_body["items"]) == 2


def test_list_invalid_active_400(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/media", {"active": "bogus"})
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_list_invalid_pagination_400(admin_api_client):
    bad_page = admin_api_client.get("/api/v1/admin/media?page=abc")
    assert bad_page.status_code == 400
    assert bad_page.json()["code"] == "VALIDATION"
    bad_size = admin_api_client.get("/api/v1/admin/media?pageSize=0")
    assert bad_size.status_code == 400
    assert bad_size.json()["code"] == "VALIDATION"


def test_update_metadata_and_active(admin_api_client):
    created = _upload(admin_api_client, title="Original")
    assert created.status_code == 201
    media_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/media/{media_id}",
        data=json.dumps(
            {"title": "Renamed", "altTextFa": "\u062a\u0648\u0636\u06cc\u062d", "isActive": True}
        ),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Renamed"
    assert data["altTextFa"] == "\u062a\u0648\u0636\u06cc\u062d"
    assert data["isActive"] is True

    media = Media.objects.get(pk=media_id)
    assert media.title == "Renamed"
    assert media.alt_text_fa == "\u062a\u0648\u0636\u06cc\u062d"
    assert media.is_active is True


def test_update_conflict_409(admin_api_client):
    created = _upload(admin_api_client, title="Conflict")
    media_id = created.json()["id"]
    current = created.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/media/{media_id}",
        data=json.dumps({"title": "Stale update"}),
        content_type="application/json",
        HTTP_IF_MATCH='"2000-01-01T00:00:00+00:00"',
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CONFLICT"
    assert body["currentUpdatedAt"] == current


def test_orphans_list(admin_api_client):
    active = _make_media(title="Active orphan", is_active=True)
    inactive = _make_media(title="Inactive orphan", is_active=False)
    _make_media(title="Another inactive", is_active=False)

    response = admin_api_client.get("/api/v1/admin/media/orphans")
    assert response.status_code == 200
    body = response.json()
    # Unreferenced rows remain orphans (active or not).
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert all(item["usageCount"] == 0 for item in body["items"])
    ids = {item["id"] for item in body["items"]}
    assert {active.pk, inactive.pk} <= ids


def test_detail_404(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/media/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"

def test_list_invalid_type_400(admin_api_client):
    r = admin_api_client.get("/api/v1/admin/media?type=bogus")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_list_page_size_101_400(admin_api_client):
    r = admin_api_client.get("/api/v1/admin/media?pageSize=101")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_upload_oversized_400(admin_api_client):
    r = _upload(
        admin_api_client,
        name="big.png",
        content=b"x" * (5 * 1024 * 1024 + 1),
        content_type="image/png",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_upload_extension_content_mismatch_400(admin_api_client):
    r = _upload(admin_api_client, name="photo.txt", content=PNG_1X1, content_type="text/plain")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_replace_compatible_200(admin_api_client):
    created = _upload(admin_api_client, name="a.png", title="A")
    assert created.status_code == 201
    mid = created.json()["id"]
    r = admin_api_client.post(
        f"/api/v1/admin/media/{mid}/replace",
        data={"file": SimpleUploadedFile("b.png", PNG_1X1, content_type="image/png")},
    )
    assert r.status_code == 200
    assert r.json()["id"] == mid
    assert r.json()["mime"] == "image/png"


def test_replace_incompatible_family_400(admin_api_client):
    created = _upload(admin_api_client, name="a.png", title="A")
    assert created.status_code == 201
    mid = created.json()["id"]
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF"
    r = admin_api_client.post(
        f"/api/v1/admin/media/{mid}/replace",
        data={"file": SimpleUploadedFile("b.pdf", pdf, content_type="application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_replace_missing_file_400(admin_api_client):
    created = _upload(admin_api_client, name="a.png", title="A")
    mid = created.json()["id"]
    r = admin_api_client.post(f"/api/v1/admin/media/{mid}/replace", data={})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION"


def test_orphans_pagination(admin_api_client):
    for i in range(3):
        _make_media(title=f"orphan-{i}")
    r = admin_api_client.get("/api/v1/admin/media/orphans?pageSize=1&page=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1

