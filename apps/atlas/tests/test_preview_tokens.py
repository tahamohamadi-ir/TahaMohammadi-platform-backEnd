"""The Atlas preview-token primitive — the one mint/verify capability module.

Plan B task 6 mints through :func:`build_atlas_preview_token` and Plan A's
endpoint verifies through :func:`parse_atlas_preview_token`. The tests prove
the four properties the serve path leans on: the round trip (a real token
carries exactly what was minted), unforgeability (every segment alteration →
``None``), the purpose boundary (a content-preview token is not an Atlas
capability) and TTL ownership (the TTL lives at minting, never in the request).
"""

from __future__ import annotations

import pytest

from apps.atlas.preview_tokens import (
    ATLAS_PREVIEW_PURPOSE,
    DEFAULT_TTL_SECONDS,
    AtlasPreviewCapability,
    build_atlas_preview_token,
    parse_atlas_preview_token,
)
from apps.content.preview_token import build_preview_token

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _atlas_preview_secret(settings):
    """The signing secret exists for every test in this module (backend-only)."""
    settings.PREVIEW_SHARE_SECRET = "test-atlas-preview-secret"


def test_a_minted_token_round_trips_to_its_capability():
    token = build_atlas_preview_token(42, "en", ttl_seconds=600)
    capability = parse_atlas_preview_token(token)
    assert capability == AtlasPreviewCapability(
        version_id=42, locale="en", purpose=ATLAS_PREVIEW_PURPOSE, exp=capability.exp
    )


def test_ttl_defaults_to_the_spec_ten_minutes():
    assert DEFAULT_TTL_SECONDS == 600
    token = build_atlas_preview_token(7, "fa")
    assert parse_atlas_preview_token(token) is not None


def test_tampered_signature_parses_to_none():
    token = build_atlas_preview_token(42, "en")
    tampered = token[:-4] + ("0000" if token[-4:] != "0000" else "1111")
    assert parse_atlas_preview_token(tampered) is None


def test_swapped_version_id_parses_to_none():
    token = build_atlas_preview_token(42, "en")
    segments = token.split(".")
    segments[1] = "43"
    assert parse_atlas_preview_token(".".join(segments)) is None


def test_swapped_locale_parses_to_none():
    token = build_atlas_preview_token(42, "en")
    segments = token.split(".")
    segments[2] = "fa"
    assert parse_atlas_preview_token(".".join(segments)) is None


def test_a_content_preview_token_is_not_an_atlas_capability():
    foreign = build_preview_token("hero", 42)
    assert parse_atlas_preview_token(foreign) is None


def test_an_absent_or_garbled_token_parses_to_none():
    assert parse_atlas_preview_token("") is None
    assert parse_atlas_preview_token("nope") is None
    assert parse_atlas_preview_token("atlas-preview.42.en.extra.parts") is None


def test_an_expired_token_still_parses_not_none():
    token = build_atlas_preview_token(42, "en", ttl_seconds=-5)
    capability = parse_atlas_preview_token(token)
    assert capability is not None, "expiry is the caller's 403, not a 401"


def test_swapped_expiry_parses_to_none():
    token = build_atlas_preview_token(42, "en")
    segments = token.split(".")
    segments[3] = str(int(segments[3]) + 60)
    assert parse_atlas_preview_token(".".join(segments)) is None
