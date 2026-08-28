"""Admin timeline records API tests (Track AB-03).

Covers GET/POST/PATCH/DELETE /api/v1/admin/timeline/{locale}[/...] and the
reorder op: auth matrix (anonymous 401, non-staff 403, OTP guard, CSRF),
If-Match preconditions (428 missing / 409 stale), append + after_id shifting,
the stable validation tokens (BAD_TYPE, INVALID_DETAIL_URL, BAD_WEIGHT,
UNKNOWN_ID, DUPLICATE_ORDER), full-permutation reorder, fa/en isolation, and
the audit row per successful mutation.
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Locale,
    Profile,
    TimelineRecord,
    TimelineRecordType,
)
from apps.security.models import AuditLog

BASE = "/api/v1/admin/timeline"
FIELD_SET = {
    "id",
    "type",
    "label",
    "period_label",
    "body",
    "role",
    "weight",
    "detail_url",
    "order",
    "attach",
    "updatedAt",
}


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
def staff_api_client(csrf_client, user):
    """Authenticated NON-staff client (forbidden per the admin auth matrix)."""
    csrf_client.force_login(user)
    return csrf_client


def _row(
    locale,
    order,
    *,
    label=None,
    type_=TimelineRecordType.EXPERIENCE,
    attach=None,
    period_label="2020-2024",
    body="did things",
    role="engineer",
    weight=1,
    detail_url="/en/writing",
):
    return TimelineRecord.objects.create(
        locale=locale,
        type=type_,
        label=label or f"row-{locale}-{order}",
        period_label=period_label,
        body=body,
        role=role,
        weight=weight,
        detail_url=detail_url,
        order=order,
        attach=attach,
    )


def _create_payload(**overrides):
    payload = {
        "type": "education",
        "label": "MSc Computer Science",
        "period_label": "2018-2022",
        "body": "Thesis on compilers.",
        "role": "Student",
        "weight": 2,
        "detail_url": "/en/research",
        "attach": None,
    }
    payload.update(overrides)
    return payload


def _post(client, locale, payload):
    return client.post(
        f"{BASE}/{locale}",
        data=json.dumps(payload),
        content_type="application/json",
    )


def _patch(client, locale, record_id, payload, *, if_match=None):
    headers = {}
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = if_match
    return client.patch(
        f"{BASE}/{locale}/{record_id}",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _delete(client, locale, record_id, *, if_match=None):
    headers = {}
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = if_match
    return client.delete(f"{BASE}/{locale}/{record_id}", **headers)


def _reorder(client, locale, ids):
    return client.post(
        f"{BASE}/{locale}/reorder",
        data=json.dumps({"ids": ids}),
        content_type="application/json",
    )


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


# --- GET ------------------------------------------------------------------


def test_get_returns_all_rows_ordered_including_drafts(admin_api_client):
    late = _row(Locale.FA, 3)
    early = _row(Locale.FA, 1, type_=TimelineRecordType.EDUCATION)
    middle = _row(Locale.FA, 2, type_=TimelineRecordType.MILESTONE)
    data = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [r["id"] for r in data] == [early.id, middle.id, late.id]
    assert set(data[0]) == FIELD_SET
    assert data[0]["type"] == "education"
    assert data[0]["order"] == 1
    assert data[0]["attach"] is None
    assert data[0]["updatedAt"].endswith("Z")


def test_get_filters_by_attach_profile(admin_api_client):
    profile_a = Profile.objects.create(locale=Locale.FA, slug="cv-fa", title="CV fa")
    profile_b = Profile.objects.create(locale=Locale.FA, slug="cv2-fa", title="CV2 fa")
    attached = _row(Locale.FA, 1, attach=profile_a)
    _row(Locale.FA, 2, attach=profile_b)
    standalone = _row(Locale.FA, 3)
    data = assert_json(
        admin_api_client.get(f"{BASE}/fa?profile={profile_a.id}"), 200
    )
    assert [r["id"] for r in data] == [attached.id]
    assert data[0]["attach"] == profile_a.id
    assert admin_api_client.get(f"{BASE}/fa?profile=999999").json() == []
    assert len(assert_json(admin_api_client.get(f"{BASE}/fa"), 200)) == 3
    assert standalone.attach is None


def test_get_unknown_locale_404(admin_api_client):
    data = assert_json(admin_api_client.get(f"{BASE}/xx"), 404)
    assert data["code"] == "NOT_FOUND"


# --- POST (create) ---------------------------------------------------------


def test_post_append_assigns_max_plus_one(admin_api_client):
    _row(Locale.FA, 5)
    data = assert_json(_post(admin_api_client, "fa", _create_payload()), 200)
    assert data["order"] == 6
    assert data["label"] == "MSc Computer Science"
    assert data["id"] == TimelineRecord.objects.get(pk=data["id"]).id
    assert AuditLog.objects.filter(
        action="timeline.create",
        model_name="timeline",
        object_id=str(data["id"]),
    ).count() == 1


def test_post_append_on_empty_locale_starts_at_one(admin_api_client):
    data = assert_json(_post(admin_api_client, "fa", _create_payload()), 200)
    assert data["order"] == 1


def test_post_after_id_middle_insert_shifts_subsequent(admin_api_client):
    first = _row(Locale.FA, 1, label="first")
    second = _row(Locale.FA, 2, label="second")
    third = _row(Locale.FA, 3, label="third")
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(after_id=first.id, label="new")),
        200,
    )
    assert data["order"] == 2
    rows = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [r["label"] for r in rows] == ["first", "new", "second", "third"]
    assert [r["order"] for r in rows] == [1, 2, 3, 4]
    second.refresh_from_db()
    third.refresh_from_db()
    assert (second.order, third.order) == (3, 4)


def test_post_after_id_unknown_is_400_unknown_id_and_atomic(admin_api_client):
    _row(Locale.FA, 1)
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(after_id=999999)), 400
    )
    assert data["fields"] == {"after_id": ["UNKNOWN_ID"]}
    assert TimelineRecord.objects.filter(locale=Locale.FA).count() == 1


def test_post_after_id_cross_locale_is_unknown(admin_api_client):
    en_anchor = _row(Locale.EN, 1)
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(after_id=en_anchor.id)), 400
    )
    assert data["fields"] == {"after_id": ["UNKNOWN_ID"]}


def test_post_bad_type_is_400_bad_type(admin_api_client):
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(type="hobby")), 400
    )
    assert data["fields"] == {"type": ["BAD_TYPE"]}
    assert TimelineRecord.objects.filter(locale=Locale.FA).count() == 0


def test_post_out_of_range_weight_is_400_bad_weight(admin_api_client):
    for bad_weight in (-1, 32768):
        data = assert_json(
            _post(admin_api_client, "fa", _create_payload(weight=bad_weight)), 400
        )
        assert data["fields"] == {"weight": ["BAD_WEIGHT"]}


def test_post_bad_detail_url_is_400_invalid_detail_url(admin_api_client):
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(detail_url="javascript:alert(1)")),
        400,
    )
    assert data["fields"] == {"detail_url": ["INVALID_DETAIL_URL"]}


def test_post_unknown_attach_is_400_unknown_id(admin_api_client):
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(attach=999999)), 400
    )
    assert data["fields"] == {"attach": ["UNKNOWN_ID"]}


def test_post_cross_locale_attach_is_unknown_id(admin_api_client):
    en_profile = Profile.objects.create(locale=Locale.EN, slug="cv-en", title="CV en")
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(attach=en_profile.id)), 400
    )
    assert data["fields"] == {"attach": ["UNKNOWN_ID"]}


def test_post_attach_happy_links_profile(admin_api_client):
    profile = Profile.objects.create(locale=Locale.FA, slug="cv-fa", title="CV fa")
    data = assert_json(
        _post(admin_api_client, "fa", _create_payload(attach=profile.id)), 200
    )
    assert data["attach"] == profile.id


def test_post_unknown_locale_404(admin_api_client):
    data = assert_json(_post(admin_api_client, "xx", _create_payload()), 404)
    assert data["code"] == "NOT_FOUND"


# --- PATCH -----------------------------------------------------------------


def _get_row_revision(client, locale, record_id):
    rows = assert_json(client.get(f"{BASE}/{locale}"), 200)
    return next(row for row in rows if row["id"] == record_id)["updatedAt"]


def test_patch_happy_updates_fields_and_revision(admin_api_client):
    row = _row(Locale.FA, 1)
    before_revision = _get_row_revision(admin_api_client, "fa", row.id)
    data = assert_json(
        _patch(
            admin_api_client,
            "fa",
            row.id,
            {"label": "renamed", "weight": 9, "body": "new body"},
            if_match=before_revision,
        ),
        200,
    )
    assert data["label"] == "renamed"
    assert data["weight"] == 9
    assert data["body"] == "new body"
    assert data["type"] == "experience"
    assert data["updatedAt"] != before_revision
    row.refresh_from_db()
    assert (row.label, row.weight, row.period_label) == ("renamed", 9, "2020-2024")
    assert AuditLog.objects.filter(
        action="timeline.update", model_name="timeline", object_id=str(row.id)
    ).count() == 1


def test_patch_stale_if_match_is_409_stale_revision(admin_api_client):
    row = _row(Locale.FA, 1)
    data = assert_json(
        _patch(
            admin_api_client,
            "fa",
            row.id,
            {"label": "renamed"},
            if_match="2020-01-01T00:00:00.000Z",
        ),
        409,
    )
    assert data["code"] == "STALE_REVISION"


def test_patch_without_if_match_is_428_precondition_required(admin_api_client):
    row = _row(Locale.FA, 1)
    data = assert_json(
        _patch(admin_api_client, "fa", row.id, {"label": "renamed"}), 428
    )
    assert data["code"] == "PRECONDITION_REQUIRED"


def test_patch_unknown_record_404(admin_api_client):
    data = assert_json(
        _patch(
            admin_api_client,
            "fa",
            999999,
            {"label": "renamed"},
            if_match="2020-01-01T00:00:00.000Z",
        ),
        404,
    )
    assert data["code"] == "NOT_FOUND"


def test_patch_bad_type_is_400_bad_type(admin_api_client):
    row = _row(Locale.FA, 1)
    revision = _get_row_revision(admin_api_client, "fa", row.id)
    data = assert_json(
        _patch(admin_api_client, "fa", row.id, {"type": "hobby"}, if_match=revision),
        400,
    )
    assert data["fields"] == {"type": ["BAD_TYPE"]}


def test_patch_bad_weight_is_400_bad_weight(admin_api_client):
    row = _row(Locale.FA, 1)
    revision = _get_row_revision(admin_api_client, "fa", row.id)
    data = assert_json(
        _patch(admin_api_client, "fa", row.id, {"weight": -3}, if_match=revision), 400
    )
    assert data["fields"] == {"weight": ["BAD_WEIGHT"]}


def test_patch_bad_detail_url_is_400_invalid_detail_url(admin_api_client):
    row = _row(Locale.FA, 1)
    revision = _get_row_revision(admin_api_client, "fa", row.id)
    data = assert_json(
        _patch(
            admin_api_client,
            "fa",
            row.id,
            {"detail_url": "ftp://example.com/x"},
            if_match=revision,
        ),
        400,
    )
    assert data["fields"] == {"detail_url": ["INVALID_DETAIL_URL"]}


def test_patch_explicit_null_attach_clears(admin_api_client):
    profile = Profile.objects.create(locale=Locale.FA, slug="cv-fa", title="CV fa")
    row = _row(Locale.FA, 1, attach=profile)
    revision = _get_row_revision(admin_api_client, "fa", row.id)
    data = assert_json(
        _patch(admin_api_client, "fa", row.id, {"attach": None}, if_match=revision),
        200,
    )
    assert data["attach"] is None
    row.refresh_from_db()
    assert row.attach_id is None


def test_patch_cross_locale_record_is_404(admin_api_client):
    en_row = _row(Locale.EN, 1)
    revision = _get_row_revision(admin_api_client, "en", en_row.id)
    data = assert_json(
        _patch(
            admin_api_client, "fa", en_row.id, {"label": "x"}, if_match=revision
        ),
        404,
    )
    assert data["code"] == "NOT_FOUND"


def test_patch_unknown_locale_404(admin_api_client):
    row = _row(Locale.FA, 1)
    data = assert_json(
        _patch(
            admin_api_client,
            "xx",
            row.id,
            {"label": "x"},
            if_match="2020-01-01T00:00:00.000Z",
        ),
        404,
    )
    assert data["code"] == "NOT_FOUND"


# --- DELETE ----------------------------------------------------------------


def test_delete_204_and_gone(admin_api_client):
    row = _row(Locale.FA, 1)
    revision = _get_row_revision(admin_api_client, "fa", row.id)
    response = _delete(admin_api_client, "fa", row.id, if_match=revision)
    assert response.status_code == 204
    assert not response.content
    assert not TimelineRecord.objects.filter(pk=row.id).exists()
    assert AuditLog.objects.filter(
        action="timeline.delete", model_name="timeline", object_id=str(row.id)
    ).count() == 1


def test_delete_without_if_match_is_428(admin_api_client):
    row = _row(Locale.FA, 1)
    assert _delete(admin_api_client, "fa", row.id).status_code == 428
    assert TimelineRecord.objects.filter(pk=row.id).exists()


def test_delete_stale_if_match_is_409(admin_api_client):
    row = _row(Locale.FA, 1)
    data = assert_json(
        _delete(admin_api_client, "fa", row.id, if_match="2020-01-01T00:00:00.000Z"),
        409,
    )
    assert data["code"] == "STALE_REVISION"


def test_delete_unknown_record_404(admin_api_client):
    data = assert_json(
        _delete(admin_api_client, "fa", 999999, if_match="2020-01-01T00:00:00.000Z"),
        404,
    )
    assert data["code"] == "NOT_FOUND"


# --- reorder ---------------------------------------------------------------


def test_reorder_full_permutation(admin_api_client):
    one = _row(Locale.FA, 1, label="one")
    two = _row(Locale.FA, 2, label="two")
    three = _row(Locale.FA, 3, label="three")
    data = assert_json(
        _reorder(admin_api_client, "fa", [three.id, one.id, two.id]), 200
    )
    assert [r["id"] for r in data] == [three.id, one.id, two.id]
    assert [r["order"] for r in data] == [1, 2, 3]
    listing = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [r["label"] for r in listing] == ["three", "one", "two"]
    assert AuditLog.objects.filter(
        action="timeline.reorder", model_name="timeline", object_id="fa"
    ).count() == 1


def test_reorder_duplicate_ids_400_and_atomic(admin_api_client):
    one = _row(Locale.FA, 1)
    two = _row(Locale.FA, 2)
    data = assert_json(
        _reorder(admin_api_client, "fa", [one.id, one.id, two.id]), 400
    )
    assert data["fields"] == {"ids": ["DUPLICATE_ORDER"]}
    one.refresh_from_db()
    two.refresh_from_db()
    assert (one.order, two.order) == (1, 2)


def test_reorder_unknown_id_400(admin_api_client):
    one = _row(Locale.FA, 1)
    data = assert_json(_reorder(admin_api_client, "fa", [one.id, 999999]), 400)
    assert data["fields"] == {"ids": ["UNKNOWN_ID"]}


def test_reorder_missing_id_400(admin_api_client):
    one = _row(Locale.FA, 1)
    two = _row(Locale.FA, 2)
    data = assert_json(_reorder(admin_api_client, "fa", [one.id]), 400)
    assert data["fields"] == {"ids": ["UNKNOWN_ID"]}
    assert {one.id, two.id} == set(
        TimelineRecord.objects.filter(locale=Locale.FA).values_list("id", flat=True)
    )


def test_reorder_cross_locale_ids_rejected(admin_api_client):
    _row(Locale.FA, 1)
    en_row = _row(Locale.EN, 1)
    data = assert_json(_reorder(admin_api_client, "fa", [en_row.id]), 400)
    assert data["fields"] == {"ids": ["UNKNOWN_ID"]}
    assert AuditLog.objects.filter(action="timeline.reorder").count() == 0


def test_reorder_across_locales_is_isolated(admin_api_client):
    fa_one = _row(Locale.FA, 1, label="fa-one")
    fa_two = _row(Locale.FA, 2, label="fa-two")
    en_one = _row(Locale.EN, 1, label="en-one")
    en_two = _row(Locale.EN, 2, label="en-two")
    assert_json(_reorder(admin_api_client, "en", [en_two.id, en_one.id]), 200)
    fa_rows = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [r["label"] for r in fa_rows] == ["fa-one", "fa-two"]
    en_rows = assert_json(admin_api_client.get(f"{BASE}/en"), 200)
    assert [r["label"] for r in en_rows] == ["en-two", "en-one"]
    assert (fa_one.order, fa_two.order) == (1, 2)


# --- auth matrix + locale validation ---------------------------------------


def test_anonymous_get_is_401_auth_required():
    data = assert_json(Client().get(f"{BASE}/fa"), 401)
    assert data["code"] == "AUTH_REQUIRED"


def test_anonymous_post_is_401_auth_required():
    data = assert_json(
        Client().post(
            f"{BASE}/fa",
            data=json.dumps(_create_payload()),
            content_type="application/json",
        ),
        401,
    )
    assert data["code"] == "AUTH_REQUIRED"


def test_non_staff_user_is_403_forbidden(staff_api_client):
    data = assert_json(staff_api_client.get(f"{BASE}/fa"), 403)
    assert data["code"] == "FORBIDDEN"


def test_staff_without_verified_otp_is_403_otp_required(csrf_client, admin_user):
    csrf_client.force_login(admin_user)
    data = assert_json(csrf_client.get(f"{BASE}/fa"), 403)
    assert data["code"] == "OTP_REQUIRED"


def test_post_without_csrf_token_is_403_csrf_failed(csrf_client, admin_user, totp_device):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    response = csrf_client.post(
        f"{BASE}/fa",
        data=json.dumps(_create_payload()),
        content_type="application/json",
    )
    data = assert_json(response, 403)
    assert data["code"] == "CSRF_FAILED"


def test_fa_and_en_are_isolated(admin_api_client):
    _row(Locale.FA, 1, label="fa-only")
    before_en = assert_json(admin_api_client.get(f"{BASE}/en"), 200)
    assert before_en == []
    created = assert_json(
        _post(admin_api_client, "en", _create_payload(label="en-only")), 200
    )
    fa_rows = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    en_rows = assert_json(admin_api_client.get(f"{BASE}/en"), 200)
    assert [r["label"] for r in fa_rows] == ["fa-only"]
    assert [r["label"] for r in en_rows] == ["en-only"]
    assert en_rows[0]["id"] == created["id"]
    assert TimelineRecord.objects.filter(locale=Locale.EN).count() == 1


def test_get_drafts_and_published_both_visible(admin_api_client):
    _row(Locale.FA, 1, label="draft-row")
    published = _row(Locale.FA, 2, label="published-row")
    published.status = "published"
    published.published_at = timezone.now()
    published.save()
    data = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [r["label"] for r in data] == ["draft-row", "published-row"]
