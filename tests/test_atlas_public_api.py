"""Public Atlas API — draft invisibility, fail-closed serving and preview tokens.

The route set this file pins (spec §10.10.1 and plan Task 15):

* ``GET /api/atlas/{locale}`` — the **active** version's §10.2 payload only.
  Draft rows are unreachable by construction (the serving path filters
  ``status="active"``), an unservable active version (a payload failing its own
  contract check) answers 500 with **no** partial body, and an unknown locale or
  a missing active version answers the ``atlas_not_found`` envelope.
* ``GET /api/atlas/preview?locale=<en|fa>`` with ``Authorization: Bearer …`` —
  the draft projection behind an unforgeable credential. The token travels only
  in the header (never a path segment, never the query string); failures are
  401 (absent/unparseable) or 403 (expired/wrong purpose/wrong locale/unknown
  version), **never** 404, and success carries the
  ``no-store``/``no-cache``/``noindex, nofollow``/``no-referrer`` header set.

Not this task's behaviour and deliberately untested here: ETag headers,
``If-None-Match`` and ``Cache-Control: public, max-age=60`` (plan Task 16) —
the serve path stays clean of them here.
"""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from apps.atlas.models import AtlasVersion
from apps.atlas.preview_tokens import build_atlas_preview_token
from apps.atlas.tests.factories import (
    _apply_placeholder_layout,
    _default_node_type,
    _node,
    _node_type,
    _relation,
    _research_focus_type,
    _seed_pair,
    _version,
)
from apps.atlas.tests.factories import (
    atlas_active_version as _fixture_atlas_active_version,
)
from apps.content.models import Article, Project

#: Ruling R5's import-by-name convention: ``factories.py`` is deliberately not a
#: conftest, so this module re-declares the fixture it consumes.
atlas_active_version = _fixture_atlas_active_version


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _atlas_preview_secret(settings):
    """The signing secret exists for every test in this module (backend-only)."""
    settings.PREVIEW_SHARE_SECRET = "test-atlas-preview-secret"


def _client() -> Client:
    """A logged-out public client, fresh per call."""
    return Client()


def _get(path, **kwargs):
    return _client().get(path, **kwargs)


def _draft_only_version(*, label="t15-draft"):
    """A fully resolvable draft version beside the fixture's active row."""
    draft = _version(status="draft", label=label)
    identity = _node(version=draft, node_type=_default_node_type(), visible=True)
    _relation(
        source=identity,
        target=_node(version=draft, node_type=_default_node_type(), visible=True),
        relation_type=_research_focus_type(),
        version=draft,
    )
    _apply_placeholder_layout(draft, list(draft.nodes.all()))
    return draft


# ---------------------------------------------------------------------------
# The active-version serve path


def test_serves_active_version_for_both_locales(atlas_active_version):
    for locale in ("en", "fa"):
        response = _get(f"/api/atlas/{locale}")
        assert response.status_code == 200, (locale, response.status_code)
        body = response.json()
        assert body["locale"] == locale
        assert body["version"]["nodeCount"] == 4


def test_serving_is_the_active_version(atlas_active_version):
    for locale in ("en", "fa"):
        response = _get(f"/api/atlas/{locale}")
        assert response.status_code == 200
        assert response.json()["version"]["id"] == atlas_active_version.pk
    versions = AtlasVersion.objects.filter(status="active")
    assert versions.count() == 1
    assert versions.get().pk == atlas_active_version.pk


def test_unknown_locale_and_missing_active_version_are_404_envelopes(atlas_active_version):
    assert _get("/api/atlas/de").status_code == 404
    assert _get("/api/atlas/de").json()["code"] == "atlas_not_found"
    assert _get("/api/atlas/en").status_code == 200
    AtlasVersion.objects.all().delete()
    response = _get("/api/atlas/en")
    assert response.status_code == 404
    assert response.json()["code"] == "atlas_not_found"


def test_draft_entities_are_never_served(atlas_active_version):
    draft_node_key = atlas_active_version.add_draft_only_node()
    response = _get("/api/atlas/en")
    assert response.status_code == 200
    body = response.json()
    assert draft_node_key not in json.dumps(body)
    assert body["version"]["id"] == atlas_active_version.pk


def test_a_whole_draft_version_is_never_served(atlas_active_version):
    """A second, *visible*, fully resolvable draft beside the active row stays
    unreachable: the route serves the active version's payload (spec §10.10.1 —
    the preview endpoint is the only place draft data is ever served)."""
    draft = _draft_only_version()
    body = _get("/api/atlas/en").json()
    assert body["version"]["id"] == atlas_active_version.pk
    for node in draft.nodes.all():
        assert node.public_key not in json.dumps(body)


# ---------------------------------------------------------------------------
# Fail-closed serving


def test_malformed_active_version_fails_closed_without_partial_data(
    atlas_active_version,
):
    atlas_active_version.corrupt_layout(lambda layout: layout.popitem())
    response = _get("/api/atlas/en")
    assert response.status_code == 500
    assert response.json()["code"] == "atlas_inval"
    assert "nodes" not in response.json()
    assert "relations" not in response.json()


def test_missing_layout_of_one_node_fails_the_request_closed(atlas_active_version):
    """Spec §20.1's ``MISSING_LAYOUT`` row is a blocking serving gate: a payload
    that would omit one node's ``position`` is not served at all — not served
    without that node."""
    node = atlas_active_version.areas[0]
    atlas_active_version.corrupt_layout(lambda layout: layout.pop(node.public_key))
    response = _get("/api/atlas/en")
    assert response.status_code == 500
    assert response.json()["code"] == "atlas_inval"
    assert "nodes" not in response.json()


def test_a_retired_relation_type_in_use_fails_closed(atlas_active_version):
    """``14-fix``'s obligation, expressed at the serving gate: retiring a
    relation type that a visible relation uses leaves the payload referencing a
    type its own catalog does not carry — that answer is never served."""
    relation_type = atlas_active_version.relations[0].relation_type
    relation_type.active = False
    relation_type.save(update_fields=["active"])
    response = _get("/api/atlas/en")
    assert response.status_code == 500
    assert response.json()["code"] == "atlas_inval"
    assert "relations" not in response.json()


def test_relations_never_reference_unknown_nodes(atlas_active_version):
    body = _get("/api/atlas/en").json()
    keys = {node["key"] for node in body["nodes"]}
    for relation in body["relations"]:
        assert relation["source"] in keys and relation["target"] in keys


# ---------------------------------------------------------------------------
# Exact-locale serving — no cross-locale fallback (§7.8)


def test_a_one_sided_node_is_absent_not_substituted(atlas_active_version):
    """A node whose canonical pair resolves FA only must never gain EN copy (and
    vice versa) on the wire: §7.8's "never falls back across locales"."""
    pair = _seed_pair(
        Project,
        uuid4(),
        en={"status": "draft", "published_at": None},
        fa={"title": "Farsi project", "slug": "fa-only-project"},
    )
    topic_fa = pair[1]
    node = _node(
        version=atlas_active_version.version,
        node_type=_node_type("project", canonical_source="project"),
        canonical_translation_key=topic_fa.translation_key,
        visible=True,
        sort_order=9,
    )
    atlas_active_version.corrupt_layout(
        lambda layout: layout.setdefault(node.public_key, [0.0, 0.0, 0.0])
    )
    body = _get("/api/atlas/en").json()
    served = json.dumps(body)
    node_entry = next(entry for entry in body["nodes"] if entry["key"] == node.public_key)
    assert "label" not in node_entry and "canonical" not in node_entry, (
        "a node that resolves in FA only must never gain the FA copy in EN — "
        "or any canonical block at all"
    )
    assert "Farsi project" not in served and "fa-only-project" not in served


# ---------------------------------------------------------------------------
# The preview endpoint — draft data behind a Bearer credential (spec §10.10.1)
# ---------------------------------------------------------------------------


def test_preview_serves_the_draft_projection_with_no_store_headers(atlas_active_version):
    draft = _draft_only_version(label="preview-target")
    draft_only_key = draft.nodes.first().public_key
    capability = build_atlas_preview_token(draft.pk, "en")  # ttl 600 s
    response = _get("/api/atlas/preview?locale=en", HTTP_AUTHORIZATION=f"Bearer {capability}")
    assert response.status_code == 200, response.status_code
    assert response["Cache-Control"] == "no-store"
    assert response["Pragma"] == "no-cache"
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert response["Referrer-Policy"] == "no-referrer"
    body = response.json()
    assert body["version"]["id"] == draft.pk
    assert body["locale"] == "en"
    assert draft_only_key in json.dumps(body)


def test_preview_credential_is_only_read_from_the_authorization_header(
    atlas_active_version,
):
    draft = _draft_only_version(label="preview-target-2")
    capability = build_atlas_preview_token(draft.pk, "en")
    assert _get("/api/atlas/preview?locale=en").status_code == 401  # absent
    assert _get("/api/atlas/preview?locale=en", HTTP_AUTHORIZATION="Bearer nope").status_code == 401
    # the query string is not a credential (the token never travels there)
    assert _get(f"/api/atlas/preview?locale=en&token={capability}").status_code == 401
    assert (
        _get("/api/atlas/preview?locale=en", HTTP_AUTHORIZATION=f"Token {capability}").status_code
        == 401
    )
    # the URL-carrying route of the rejected design must not exist at all
    assert _get(f"/api/atlas/preview/{capability}").status_code == 404


def test_preview_rejects_expired_wrong_locale_foreign_purpose_unknown_version(
    atlas_active_version,
):
    from apps.content.preview_token import build_preview_token

    draft = _draft_only_version(label="preview-target-3")
    article = Article.objects.create(
        locale="en",
        slug="preview-reject-article",
        title="Preview reject article",
        status="published",
        published_at=timezone.now() - timedelta(days=1),
    )
    for bad, expected in (
        (build_atlas_preview_token(draft.pk, "en", ttl_seconds=-5), 403),  # expired
        (build_atlas_preview_token(draft.pk, "fa"), 403),  # wrong locale
        # A foreign-family token (apps.content's ``article.<pk>.<exp>.<sig>``) is
        # signed over a DIFFERENT message shape (no locale segment), so under the
        # primitive's HMAC verification it is cryptographically indistinguishable
        # from garbage — and spec §10.10.1 answers garbage with 401, keeping a
        # probe from learning anything. The plan's matrix assumed the shapes were
        # comparable; they are not, so the honest reading is 401 and the test
        # states that explicitly instead of asserting the plan's number.
        (build_preview_token("article", article.pk), 401),
        (build_atlas_preview_token(999_999, "en"), 403),  # unknown version
    ):
        response = _get("/api/atlas/preview?locale=en", HTTP_AUTHORIZATION=f"Bearer {bad}")
        assert response.status_code == expected, (response.status_code, bad[:48])


def test_preview_is_read_only_and_never_leaks_into_the_public_route(
    atlas_active_version,
):
    draft = _draft_only_version(label="preview-target-4")
    draft_only_key = draft.nodes.first().public_key
    capability = build_atlas_preview_token(draft.pk, "en")
    preview_body = _get(
        "/api/atlas/preview?locale=en", HTTP_AUTHORIZATION=f"Bearer {capability}"
    ).json()
    assert draft_only_key in json.dumps(preview_body)
    # the URL-carrying route of the rejected design must not exist at all
    assert (
        _get(
            f"/api/atlas/preview/{capability}", HTTP_AUTHORIZATION=f"Bearer {capability}"
        ).status_code
        == 404
    )
    public_en = _get("/api/atlas/en").json()
    assert draft_only_key not in json.dumps(public_en)
    public_fa = _get("/api/atlas/fa").json()
    assert draft_only_key not in json.dumps(public_fa)


def test_an_atlas_family_token_with_a_wrong_purpose_is_403_while_garbage_is_401(
    atlas_active_version,
):
    """§10.10.1's boundary, direct: a token signed with OUR secret over OUR
    five-segment message shape, but carrying a purpose other than
    ``atlas-preview``, is *authenticated and authorized-refused* — 403 — while
    bytes that verify to nothing at all stay a 401. The plan's foreign-token
    shape (``article.<pk>.<exp>.<sig>``, four segments over a different message)
    is not this case: under HMAC verification it is cryptographically
    indistinguishable from garbage, so it keeps the honest 401 the earlier
    test already pinned."""
    import hashlib
    import hmac as _hmac
    import time

    from apps.atlas.preview_tokens import MESSAGE_PREFIX, _preview_secret

    secret = _preview_secret()
    exp = int(time.time()) + 60
    message = f"{MESSAGE_PREFIX}:article:{atlas_active_version.pk}:en:{exp}"
    sig = _hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    wrong_purpose = f"article.{atlas_active_version.pk}.en.{exp}.{sig}"
    response = _get(
        "/api/atlas/preview?locale=en", HTTP_AUTHORIZATION=f"Bearer {wrong_purpose}"
    )
    assert response.status_code == 403, response.status_code
    assert _get(
        "/api/atlas/preview?locale=en", HTTP_AUTHORIZATION="Bearer nope"
    ).status_code == 401
