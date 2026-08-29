"""Anonymous public OpenAPI availability required for frontend review."""

from django.test import Client


def test_public_openapi_schema_is_anonymous_and_valid_json():
    response = Client().get("/api/openapi.json")

    assert response.status_code == 200
    body = response.json()
    assert body["info"]["title"] == "Taha CMS Public API"
    assert body["info"]["version"] == "0.4.0"
    assert len(body["paths"]) > 0
