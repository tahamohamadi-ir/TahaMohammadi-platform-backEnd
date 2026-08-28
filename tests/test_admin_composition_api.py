"""Composition admin API tests (ADR-0026, ADM-3).

Covers the /api/v1/admin/composition/* contract: guards, create/list/detail,
the /schema metadata, the full-document PUT with If-Match optimistic locking,
fail-closed block/section validation, publish semantics and media reference
strictness.
"""

import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.media.models import Media

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03"
    b"\x00\x01\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device, media_root):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _put_json(client, path, payload, updated_at):
    return client.put(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )


def _create_page(client, *, key, locale="en", title="Page", status=None):
    payload = {"key": key, "locale": locale, "title": title}
    if status is not None:
        payload["status"] = status
    return _post_json(client, "/api/v1/admin/composition", payload)


def _make_media(*, title="photo", content=PNG_1X1):
    return Media.objects.create(
        file=SimpleUploadedFile("photo.png", content, content_type="image/png"),
        title=title,
    )


def _hero_block(**overrides):
    settings = {"titleFa": "ت", "titleEn": "t", "leadFa": "l", "leadEn": "e"}
    settings.update(overrides)
    return {"blockType": "hero", "settings": settings, "enabled": True}


def _text_block(body_fa="متن", body_en="text"):
    return {
        "blockType": "text",
        "settings": {"bodyFa": body_fa, "bodyEn": body_en},
        "enabled": True,
    }


def test_anonymous_list_401(csrf_client):
    response = csrf_client.get("/api/v1/admin/composition")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_list_requires_otp(csrf_client, admin_user):
    csrf_client.force_login(admin_user)
    response = csrf_client.get("/api/v1/admin/composition")
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"


def test_create_201(admin_api_client):
    response = _create_page(admin_api_client, key="home", title="Home")
    assert response.status_code == 201
    data = response.json()
    assert data["key"] == "home"
    assert data["locale"] == "en"
    assert data["title"] == "Home"
    assert data["status"] == "draft"
    assert data["sections"] == []


def test_create_published_sets_published_at(admin_api_client):
    response = _create_page(admin_api_client, key="live", title="Live", status="published")
    assert response.status_code == 201
    assert response.json()["publishedAt"] is not None


def test_create_duplicate_key_409(admin_api_client):
    assert _create_page(admin_api_client, key="dup", title="First").status_code == 201
    second = _create_page(admin_api_client, key="dup", title="Second")
    assert second.status_code == 409
    assert second.json()["code"] == "DUPLICATE"


def test_create_invalid_locale_400(admin_api_client):
    response = _create_page(admin_api_client, key="bad", locale="xx")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_create_bad_key_400(admin_api_client):
    response = _create_page(admin_api_client, key="Bad Key!")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_list_filters_and_pagination(admin_api_client):
    _create_page(admin_api_client, key="alpha", title="Alpha page")
    _create_page(admin_api_client, key="beta", title="Beta page", locale="fa")
    _create_page(admin_api_client, key="gamma", title="Gamma", status="published")

    by_locale = admin_api_client.get("/api/v1/admin/composition?locale=fa")
    assert by_locale.status_code == 200
    assert by_locale.json()["total"] == 1
    assert by_locale.json()["items"][0]["key"] == "beta"

    by_q = admin_api_client.get("/api/v1/admin/composition?q=alpha")
    assert by_q.status_code == 200
    assert by_q.json()["total"] == 1
    assert by_q.json()["items"][0]["key"] == "alpha"

    by_status = admin_api_client.get("/api/v1/admin/composition?status=published")
    assert by_status.status_code == 200
    assert by_status.json()["total"] == 1

    paged = admin_api_client.get("/api/v1/admin/composition?pageSize=1&page=2")
    assert paged.status_code == 200
    assert paged.json()["total"] == 3
    assert len(paged.json()["items"]) == 1


def test_list_invalid_filters_400(admin_api_client):
    assert admin_api_client.get("/api/v1/admin/composition?locale=xx").status_code == 400
    assert admin_api_client.get("/api/v1/admin/composition?status=zz").status_code == 400
    assert admin_api_client.get("/api/v1/admin/composition?pageSize=0").status_code == 400


def test_schema_endpoint(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/composition/schema")
    assert response.status_code == 200
    body = response.json()
    types = {bt["type"] for bt in body["blockTypes"]}
    assert {"hero", "gallery", "divider"} <= types
    layouts = {sl["value"]: sl for sl in body["sectionLayouts"]}
    assert layouts["2col"]["ratios"] == ["1:1", "1:2", "2:1"]
    hero = next(bt for bt in body["blockTypes"] if bt["type"] == "hero")
    assert {f["key"] for f in hero["fields"]} >= {"titleFa", "titleEn", "mediaId"}


def test_detail_404(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/composition/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_detail_returns_sections_and_blocks(admin_api_client):
    created = _create_page(admin_api_client, key="about", title="About")
    assert created.status_code == 201
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    doc = {
        "sections": [
            {
                "layout": "2col",
                "ratio": "1:2",
                "enabled": True,
                "blocks": [
                    _hero_block(),
                    _text_block(),
                ],
            }
        ]
    }
    put = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc, updated_at)
    assert put.status_code == 200

    detail = admin_api_client.get(f"/api/v1/admin/composition/{page_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["sections"]) == 1
    section = body["sections"][0]
    assert section["layout"] == "2col"
    assert section["ratio"] == "1:2"
    assert section["position"] == 0
    assert [b["blockType"] for b in section["blocks"]] == ["hero", "text"]


def test_put_full_document_with_gallery(admin_api_client):
    media = _make_media(title="pic")
    created = _create_page(admin_api_client, key="port", title="Portfolio")
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    doc = {
        "sections": [
            {"layout": "1col", "ratio": "", "enabled": True, "blocks": []},
            {
                "layout": "2col",
                "ratio": "1:1",
                "enabled": True,
                "blocks": [
                    _hero_block(mediaId=media.pk),
                    {
                        "blockType": "gallery",
                        "settings": {"mediaIds": [media.pk]},
                        "enabled": True,
                    },
                    {"blockType": "divider", "settings": {}, "enabled": True},
                ],
            },
        ]
    }
    put = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc, updated_at)
    assert put.status_code == 200
    body = put.json()
    assert len(body["sections"]) == 2
    assert body["sections"][1]["blocks"][0]["settings"]["mediaId"] == media.pk
    assert body["sections"][1]["blocks"][1]["settings"]["mediaIds"] == [media.pk]


def test_put_invalid_block_settings_400(admin_api_client):
    created = _create_page(admin_api_client, key="inv", title="Invalid")
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    # hero missing required titleFa
    doc = {
        "sections": [
            {
                "layout": "1col",
                "ratio": "",
                "enabled": True,
                "blocks": [_hero_block(titleFa="")],
            }
        ]
    }
    r = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc, updated_at)
    assert r.status_code == 400

    # gallery with zero media
    doc2 = {
        "sections": [
            {
                "layout": "1col",
                "ratio": "",
                "enabled": True,
                "blocks": [
                    {"blockType": "gallery", "settings": {"mediaIds": []}, "enabled": True}
                ],
            }
        ]
    }
    r2 = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc2, updated_at)
    assert r2.status_code == 400

    # unknown settings key (fail-closed)
    doc3 = {
        "sections": [
            {
                "layout": "1col",
                "ratio": "",
                "enabled": True,
                "blocks": [_hero_block(bogus="x")],
            }
        ]
    }
    r3 = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc3, updated_at)
    assert r3.status_code == 400


def test_put_unknown_block_type_400(admin_api_client):
    created = _create_page(admin_api_client, key="unk", title="Unknown")
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]
    doc = {
        "sections": [
            {
                "layout": "1col",
                "ratio": "",
                "enabled": True,
                "blocks": [{"blockType": "widget", "settings": {}, "enabled": True}],
            }
        ]
    }
    r = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc, updated_at)
    assert r.status_code == 400
    assert any("blockType" in key for key in r.json()["fields"])


def test_put_invalid_ratio_400(admin_api_client):
    created = _create_page(admin_api_client, key="ratio", title="Ratio")
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]
    doc = {
        "sections": [{"layout": "2col", "ratio": "3:1", "enabled": True, "blocks": []}]
    }
    r = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", doc, updated_at)
    assert r.status_code == 400
    assert "sections[0].ratio" in r.json()["fields"]


def test_put_conflict_409(admin_api_client):
    created = _create_page(admin_api_client, key="conf", title="Conflict")
    page_id = created.json()["id"]
    r = _put_json(
        admin_api_client,
        f"/api/v1/admin/composition/{page_id}",
        {"sections": []},
        "2000-01-01T00:00:00+00:00",
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "CONFLICT"
    assert body["currentUpdatedAt"] == created.json()["updatedAt"]


def test_put_publish_sets_published_at(admin_api_client):
    created = _create_page(admin_api_client, key="pub", title="Publish")
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]
    r = _put_json(
        admin_api_client,
        f"/api/v1/admin/composition/{page_id}",
        {"status": "published", "sections": []},
        updated_at,
    )
    assert r.status_code == 200
    assert r.json()["publishedAt"] is not None


def test_write_requires_otp(csrf_client, admin_user):
    csrf_client.force_login(admin_user)
    post = _post_json(
        csrf_client,
        "/api/v1/admin/composition",
        {"key": "no-otp", "locale": "en", "title": "No OTP"},
    )
    assert post.status_code == 403
    put = csrf_client.put(
        "/api/v1/admin/composition/1",
        data=json.dumps({"sections": []}),
        content_type="application/json",
    )
    assert put.status_code == 403


def test_media_ref_rejects_float_and_bool(admin_api_client):
    created = _create_page(admin_api_client, key="media-float", title="Media float")
    assert created.status_code == 201
    page_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    float_doc = {
        "sections": [
            {
                "layout": "1col",
                "ratio": "",
                "enabled": True,
                "blocks": [_hero_block(mediaId=1.9)],
            }
        ]
    }
    r = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", float_doc, updated_at)
    assert r.status_code == 400

    bool_doc = {
        "sections": [
            {
                "layout": "1col",
                "ratio": "",
                "enabled": True,
                "blocks": [_hero_block(mediaId=True)],
            }
        ]
    }
    r2 = _put_json(admin_api_client, f"/api/v1/admin/composition/{page_id}", bool_doc, updated_at)
    assert r2.status_code == 400
