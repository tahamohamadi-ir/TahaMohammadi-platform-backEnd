"""Guards for publishing every content family and reading its health row."""

from uuid import UUID

from apps.api.admin_content import ENTITY_MODELS
from apps.api.admin_health import BODY_FIELDS
from apps.content.revisions import _serialize_value


def test_content_health_names_a_body_field_for_every_entity():
    assert set(BODY_FIELDS) == set(ENTITY_MODELS)


def test_snapshot_values_encode_profile_translation_uuids():
    value = UUID("12345678-1234-5678-1234-567812345678")
    assert _serialize_value(value) == "12345678-1234-5678-1234-567812345678"
