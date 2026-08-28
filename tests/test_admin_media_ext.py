"""Admin media presentation API tests (Track AB-04).

Covers PATCH /api/v1/admin/media/{id}/presentation and GET
/api/v1/admin/media/licenses: the auth matrix (anonymous 401, non-staff
403, OTP guard, CSRF), If-Match preconditions (428 missing / 409 stale),
the frozen body subset (UNKNOWN_FIELD), focal range + 2dp rounding
(OUT_OF_RANGE), license linkage (UNKNOWN_LICENSE + explicit-null clear),
per-field happy patches, the audit row per success, inactive media still
patchable by staff while publicly invisible, and the public media
projection staying rights-free.
"""

import json
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.media.models import Media, MediaLicense
from apps.media.public_urls import public_media_ref
from apps.security.models import AuditLog

BASE = "/api/v1/admin/media"
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PUBLIC_REF_KEYS = {"url", "alt", "mime", "title", "size"}
STALE = "2020-01-01T00:00:00.000Z"


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


@pytest.fixture
def staff_api_client(csrf_client, user):
    """Authenticated NON-staff client (forbidden per the admin auth matrix)."""
    csrf_client.force_login(user)
    return csrf_client


def make_media(**kwargs):
    defaults = {
        "file": SimpleUploadedFile("photo.png", PNG_1X1, content_type="image/png"),
        "title": "Photo",
    }
    defaults.update(kwargs)
    return Media.objects.create(**defaults)


def _patch(client, media_id, payload, *, if_match=None):
    headers = {}
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = if_match
    return client.patch(
        f"{BASE}/{media_id}/presentation",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _revision(client, media_id):
    """Round-trip revision from the existing media detail endpoint."""
    return client.get(f"{BASE}/{media_id}").json()["updatedAt"]


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


# --- PATCH happy paths ------------------------------------------------------


def test_patch_happy_updates_every_field_and_audits(admin_api_client):
    license = MediaLicense.objects.create(name="CC BY 4.0")
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(
            admin_api_client,
            media.id,
            {
                "focal_x": 33.333,
                "focal_y": 66.666,
                "rights_statement_fa": "Copyright FA",
                "rights_statement_en": "Owner: Taha",
                "license_id": license.id,
                "caption_fa": "caption-fa",
                "caption_en": "Caption",
            },
            if_match=revision,
        ),
        200,
    )
    assert set(data) == {"id", "updatedAt"}
    assert data["id"] == media.id
    assert data["updatedAt"] != revision
    media.refresh_from_db()
    assert media.focal_x == Decimal("33.33")
    assert media.focal_y == Decimal("66.67")
    assert media.rights_statement_fa == "Copyright FA"
    assert media.rights_statement_en == "Owner: Taha"
    assert media.license_id == license.id
    assert media.caption_fa == "caption-fa"
    assert media.caption_en == "Caption"
    assert AuditLog.objects.filter(
        action="media.presentation_update",
        model_name="media",
        object_id=str(media.id),
    ).count() == 1


def test_patch_single_field_leaves_others_untouched(admin_api_client):
    license = MediaLicense.objects.create(name="CC0")
    media = make_media(
        license=license,
        focal_x=Decimal("10.00"),
        caption_en="keep-me",
    )
    revision = _revision(admin_api_client, media.id)
    assert_json(
        _patch(admin_api_client, media.id, {"caption_en": "changed"}, if_match=revision),
        200,
    )
    media.refresh_from_db()
    assert media.caption_en == "changed"
    assert media.focal_x == Decimal("10.00")
    assert media.license_id == license.id


def test_patch_focal_bounds_zero_and_hundred_accepted(admin_api_client):
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    assert_json(
        _patch(
            admin_api_client,
            media.id,
            {"focal_x": 0, "focal_y": 100},
            if_match=revision,
        ),
        200,
    )
    media.refresh_from_db()
    assert media.focal_x == Decimal("0.00")
    assert media.focal_y == Decimal("100.00")


def test_patch_empty_subset_is_200_noop(admin_api_client):
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(admin_api_client, media.id, {}, if_match=revision), 200
    )
    assert data["updatedAt"] == revision
    assert AuditLog.objects.filter(action="media.presentation_update").count() == 1


# --- explicit null clears ----------------------------------------------------


def test_patch_explicit_null_clears_all_fields(admin_api_client):
    license = MediaLicense.objects.create(name="CC BY 4.0")
    media = make_media(
        license=license,
        focal_x=Decimal("10.00"),
        focal_y=Decimal("20.00"),
        rights_statement_fa="fa-text",
        rights_statement_en="en-text",
        caption_fa="cap-fa",
        caption_en="cap-en",
    )
    revision = _revision(admin_api_client, media.id)
    assert_json(
        _patch(
            admin_api_client,
            media.id,
            {
                "focal_x": None,
                "focal_y": None,
                "license_id": None,
                "rights_statement_fa": None,
                "rights_statement_en": None,
                "caption_fa": None,
                "caption_en": None,
            },
            if_match=revision,
        ),
        200,
    )
    media.refresh_from_db()
    assert media.focal_x is None
    assert media.focal_y is None
    assert media.license_id is None
    assert media.rights_statement_fa == ""
    assert media.rights_statement_en == ""
    assert media.caption_fa == ""
    assert media.caption_en == ""


# --- validation tokens --------------------------------------------------------


@pytest.mark.parametrize("axis", ["focal_x", "focal_y"])
@pytest.mark.parametrize("value", [150, -1])
def test_patch_focal_out_of_range_is_400_out_of_range(admin_api_client, axis, value):
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(admin_api_client, media.id, {axis: value}, if_match=revision), 400
    )
    assert data["fields"] == {axis: ["OUT_OF_RANGE"]}
    media.refresh_from_db()
    assert getattr(media, axis) is None


def test_patch_unknown_license_is_400_unknown_license(admin_api_client):
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(admin_api_client, media.id, {"license_id": 99999}, if_match=revision),
        400,
    )
    assert data["fields"] == {"license_id": ["UNKNOWN_LICENSE"]}
    media.refresh_from_db()
    assert media.license_id is None


def test_patch_extra_keys_are_400_unknown_field_and_ignored(admin_api_client):
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(
            admin_api_client,
            media.id,
            {"title": "nope", "isActive": True},
            if_match=revision,
        ),
        400,
    )
    assert data["fields"] == {
        "isActive": ["UNKNOWN_FIELD"],
        "title": ["UNKNOWN_FIELD"],
    }
    media.refresh_from_db()
    assert media.title == "Photo"
    assert media.is_active is False


def test_patch_caption_over_column_bound_is_400_too_long(admin_api_client):
    media = make_media()
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(admin_api_client, media.id, {"caption_en": "x" * 301}, if_match=revision),
        400,
    )
    assert data["fields"] == {"caption_en": ["TOO_LONG"]}


# --- If-Match preconditions ----------------------------------------------------


def test_patch_stale_if_match_is_409_stale_revision(admin_api_client):
    media = make_media()
    data = assert_json(
        _patch(admin_api_client, media.id, {"caption_en": "x"}, if_match=STALE),
        409,
    )
    assert data["code"] == "STALE_REVISION"
    media.refresh_from_db()
    assert media.caption_en == ""


def test_patch_without_if_match_is_428_precondition_required(admin_api_client):
    media = make_media()
    data = assert_json(
        _patch(admin_api_client, media.id, {"caption_en": "x"}), 428
    )
    assert data["code"] == "PRECONDITION_REQUIRED"


def test_patch_unknown_media_is_404_not_found(admin_api_client):
    data = assert_json(
        _patch(admin_api_client, 999999, {"caption_en": "x"}, if_match=STALE), 404
    )
    assert data["code"] == "NOT_FOUND"


# --- GET /licenses --------------------------------------------------------------


def test_get_licenses_ordered_by_name(admin_api_client):
    MediaLicense.objects.create(name="CC BY-SA 4.0")
    MediaLicense.objects.create(name="CC BY 4.0")
    data = assert_json(admin_api_client.get(f"{BASE}/licenses"), 200)
    assert [item["name"] for item in data] == ["CC BY 4.0", "CC BY-SA 4.0"]
    for item in data:
        assert set(item) == {"id", "name"}
        assert MediaLicense.objects.filter(pk=item["id"], name=item["name"]).exists()


# --- auth matrix -----------------------------------------------------------------


def test_anonymous_patch_is_401_auth_required(db, media_root):
    media = make_media()
    data = assert_json(
        Client().patch(
            f"{BASE}/{media.id}/presentation",
            data=json.dumps({"caption_en": "x"}),
            content_type="application/json",
        ),
        401,
    )
    assert data["code"] == "AUTH_REQUIRED"


def test_anonymous_licenses_get_is_401_auth_required():
    data = assert_json(Client().get(f"{BASE}/licenses"), 401)
    assert data["code"] == "AUTH_REQUIRED"


def test_non_staff_user_is_403_forbidden(staff_api_client):
    data = assert_json(staff_api_client.get(f"{BASE}/licenses"), 403)
    assert data["code"] == "FORBIDDEN"


def test_staff_without_verified_otp_is_403_otp_required(csrf_client, admin_user, media_root):
    csrf_client.force_login(admin_user)
    media = make_media()
    data = assert_json(
        csrf_client.patch(
            f"{BASE}/{media.id}/presentation",
            data=json.dumps({"caption_en": "x"}),
            content_type="application/json",
        ),
        403,
    )
    assert data["code"] == "OTP_REQUIRED"


def test_patch_without_csrf_token_is_403_csrf_failed(
    csrf_client, admin_user, totp_device, media_root
):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    media = make_media()
    data = assert_json(
        csrf_client.patch(
            f"{BASE}/{media.id}/presentation",
            data=json.dumps({"caption_en": "x"}),
            content_type="application/json",
        ),
        403,
    )
    assert data["code"] == "CSRF_FAILED"


# --- inactive media + public projection -------------------------------------------


def test_inactive_media_still_patchable_but_publicly_invisible(admin_api_client):
    media = make_media(is_active=False)
    revision = _revision(admin_api_client, media.id)
    data = assert_json(
        _patch(admin_api_client, media.id, {"caption_en": "internal"}, if_match=revision),
        200,
    )
    assert data["id"] == media.id
    media.refresh_from_db()
    assert media.caption_en == "internal"
    assert public_media_ref(media) is None


def test_public_projection_unchanged_after_presentation_patch(admin_api_client):
    license = MediaLicense.objects.create(name="CC BY 4.0")
    media = make_media(is_active=True)
    revision = _revision(admin_api_client, media.id)
    assert_json(
        _patch(
            admin_api_client,
            media.id,
            {
                "focal_x": 25,
                "focal_y": 75,
                "license_id": license.id,
                "rights_statement_fa": "owner-fa",
                "rights_statement_en": "Owner: Taha",
                "caption_fa": "cap-fa",
                "caption_en": "Caption",
            },
            if_match=revision,
        ),
        200,
    )
    media.refresh_from_db()
    ref = public_media_ref(media)
    assert ref is not None
    # BK-04 follow-up: focal is public design metadata (omitted when unset);
    # rights/licence/captions stay admin-only.
    assert set(ref) == PUBLIC_REF_KEYS | {"focal"}
    assert ref["focal"] == {"x": 25.0, "y": 75.0}
    admin_only = {
        "rights_statement_fa",
        "rights_statement_en",
        "license",
        "license_id",
        "caption_fa",
        "caption_en",
    }
    assert not (admin_only & set(ref))
