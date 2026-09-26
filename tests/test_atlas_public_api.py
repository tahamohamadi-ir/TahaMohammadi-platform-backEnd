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

Plan Task 16 extends the public active route with the HTTP validators: the
quoted ``ETag`` (the projection's unquoted validator, quoted only at the
boundary), ``Cache-Control: public, max-age=60``, and conditional GET — a
matching ``If-None-Match`` answers 304 with no body, a non-matching one serves
normally, garbage never matches, EN and FA carry distinct validators, and a
matching validator on a payload that fails its own contract check still fails
closed (500), never 304.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from apps.atlas.models import AtlasVersion
from apps.atlas.preview_tokens import build_atlas_preview_token
from apps.atlas.projection import projection_etag
from apps.atlas.services import activate_version, version_revision
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
from apps.atlas.tests.factories import (
    atlas_two_versions as _fixture_atlas_two_versions,
)
from apps.content.models import Article, Project

#: Ruling R5's import-by-name convention: ``factories.py`` is deliberately not a
#: conftest, so this module re-declares the fixture it consumes.
atlas_active_version = _fixture_atlas_active_version
atlas_two_versions = _fixture_atlas_two_versions


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


def test_a_hidden_node_of_the_active_version_stays_hidden(atlas_active_version):
    """REVIEW-15 fix: `M2` coverage — remove a hidden node of the ACTIVE version
    from BOTH the endpoint route and the preview route: the review's own
    e2e harness caught the invisible filter surviving the committed suite,
    while its own projection-level test never exercised the endpoint path.
    §10.10/§10.10.1: the public Atlas serves the active version's *visible*
    graph, and the preview serves nothing the active body would not."""
    from apps.atlas.tests.factories import _node

    hidden = _node(
        version=atlas_active_version.version,
        public_key="project-abc123ab-hidden-1a2b",
        visible=False,
    )
    body = _get("/api/atlas/en")
    assert body.status_code == 200
    body = body.json()
    assert hidden.public_key not in json.dumps(body)
    preview = _get(
        "/api/atlas/preview?locale=en",
        HTTP_AUTHORIZATION=f"Bearer {build_atlas_preview_token(atlas_active_version.pk, 'en')}",
    )
    assert preview.status_code == 200
    assert hidden.public_key not in json.dumps(preview.json())

# ---------------------------------------------------------------------------
# HTTP validators: ETag / If-None-Match / Cache-Control (plan Task 16)
# ---------------------------------------------------------------------------


def test_etag_and_cache_control_headers(atlas_active_version):
    """The public active route answers 200 with a quoted ETag over the
    projection validator (spec §10.5) and a public cache header."""
    response = _get("/api/atlas/en")
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=60"
    quoted = response["ETag"]
    match = re.fullmatch(r'"(\d+)-([0-9a-f]{16})"', quoted)
    assert match is not None, quoted
    assert int(match.group(1)) == atlas_active_version.pk


def test_if_none_match_returns_304_with_an_empty_body(atlas_active_version):
    """A matching If-None-Match answers 304 with no body, echoing the validator."""
    etag = _get("/api/atlas/en")["ETag"]
    response = _get("/api/atlas/en", HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 304
    assert response.content == b""
    assert response["ETag"] == etag


def test_weak_or_listed_candidate_validators_still_match(atlas_active_version):
    """§10.5: the validator is the literal — a `W/`-prefixed candidate or a
    comma list carrying the current validator matches; a 304 still applies."""
    etag = _get("/api/atlas/en")["ETag"]
    weak = f"W/{etag}"
    assert _get("/api/atlas/en", HTTP_IF_NONE_MATCH=weak).status_code == 304
    listed = f'"nope-0000000000000000", {etag}'
    assert _get("/api/atlas/en", HTTP_IF_NONE_MATCH=listed).status_code == 304


def test_a_non_matching_if_none_match_is_served_normally(atlas_active_version):
    etag = _get("/api/atlas/en")["ETag"]
    response = _get("/api/atlas/en", HTTP_IF_NONE_MATCH='"deadbeef-0000000000000000"')
    assert response.status_code == 200
    assert response["ETag"] == etag
    assert response.json()["locale"] == "en"


def test_a_malformed_if_none_match_never_answers_304(atlas_active_version):
    """Garbage in the conditional header matches nothing: the unquoted literal
    (the wire form is quoted), an unparsable split quote, or a bare `W/`."""
    for bad in ("unquoted-garbage", "W/", '"one', 'two"', "", "W/\"one,really\""):
        response = _get("/api/atlas/en", HTTP_IF_NONE_MATCH=bad)
        assert response.status_code == 200, (bad, response.status_code)


def test_en_and_fa_carry_distinct_validators(atlas_active_version):
    etag_en = _get("/api/atlas/en")["ETag"]
    etag_fa = _get("/api/atlas/fa")["ETag"]
    assert etag_en != etag_fa


def test_an_unchanged_active_version_repeats_the_same_etag(atlas_active_version):
    first = _get("/api/atlas/en")["ETag"]
    second = _get("/api/atlas/en")["ETag"]
    assert first == second


def test_projection_etag_stays_unquoted_and_quotes_arrive_on_the_wire(
    atlas_active_version,
):
    """Task 14-fix contract: the projection-level validator keeps its bare
    `<version-id>-<16hex>` form — quoting happens only at the HTTP header
    boundary, so the header's inside equals the projection function's output."""
    from apps.api.api import public_atlas_payload

    payload = public_atlas_payload("en")
    unquoted = projection_etag(payload)
    assert re.fullmatch(r"\d+-[0-9a-f]{16}", unquoted), unquoted
    on_wire = _get("/api/atlas/en")["ETag"]
    assert on_wire == f'"{unquoted}"'


def test_projection_etag_over_a_differently_ordered_payload_is_unchanged_on_the_wire(
    atlas_active_version,
):
    """The projection's own 14-fix contract test pins `sort_keys=True`; the
    HTTP layer must not break it. Rebuild the same payload from a differently
    ordered mapping and confirm the served ETag still equals the validator."""
    from apps.api.api import public_atlas_payload

    payload = public_atlas_payload("en")
    reordered = {k: payload[k] for k in reversed(list(payload.keys()))}
    assert projection_etag(reordered) == projection_etag(payload)
    assert _get("/api/atlas/en")["ETag"] == f'"{projection_etag(payload)}"'


def test_a_changed_payload_changes_the_wire_validator(atlas_active_version):
    """Any payload change (here: a node's own override) must move the ETag."""
    before = _get("/api/atlas/en")["ETag"]
    node = atlas_active_version.areas[0]
    atlas_active_version.override(node, locale="en", label="Renamed on the wire")
    after = _get("/api/atlas/en")["ETag"]
    assert before != after


def test_a_changed_row_stamp_changes_the_wire_validator(atlas_active_version):
    """Even a payload untouched but stamped again (publishedAt moves) must
    move the validator — the projection carries the version's stamps."""
    before = _get("/api/atlas/fa")["ETag"]
    version = AtlasVersion.objects.get(pk=atlas_active_version.pk)
    assert version.updated_at is not None
    version.updated_at = version.updated_at + timedelta(seconds=1)
    version.save(update_fields=["updated_at"])
    after = _get("/api/atlas/fa")["ETag"]
    assert before != after


def test_activation_of_a_new_version_changes_the_etag(atlas_two_versions):
    """A freshly activated version (new row, new publishedAt) moves the tag."""
    draft = atlas_two_versions.draft
    before = _get("/api/atlas/en")["ETag"]
    activate_version(draft.pk, expected_revision=version_revision(draft))
    after = _get("/api/atlas/en")["ETag"]
    assert before != after


def test_a_corrupt_active_version_fails_closed_even_with_a_matching_validator(
    atlas_active_version,
):
    """A client's matching If-None-Match must never buy a 304 for a payload
    that no longer passes its own contract check: the gate is re-evaluated and
    the answer fails closed (500) — no conditional short-circuit is trusted."""
    etag = _get("/api/atlas/en").headers.get("ETag")
    atlas_active_version.corrupt_layout(lambda layout: layout.popitem())
    response = _get("/api/atlas/en", HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 500
    assert response.json()["code"] == "atlas_inval"


def test_the_304_path_avoids_rebuilding_the_projection(
    atlas_active_version, monkeypatch
):
    """A matching If-None-Match short-circuits at the validator: the projection
    answers an EMPTY body — it never serialises the projection to wire bytes
    again (the plan's no-heavy-work requirement), while the serving gate itself
    is deliberately re-evaluated on every conditional request: the fail-closed
    rule (a corrupt payload never earns a 304) outranks the optimisation, and
    skipping the projection rebuild would hand a corrupted active version a
    free 304 off a stale client validator."""
    from importlib import import_module

    from django.urls import get_resolver

    _ = list(get_resolver().url_patterns)  # warm the URL conf first (admin circular import)
    api_module = import_module("apps.api.api")

    etag = _get("/api/atlas/en").headers.get("ETag")
    serialisations = []
    real_canonical_json = api_module.canonical_json

    def _spy(payload, **kwargs):
        serialisations.append(1)
        return real_canonical_json(payload)

    monkeypatch.setattr(api_module, "canonical_json", _spy)
    response = _get("/api/atlas/en", HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 304
    assert response.content == b""
    assert serialisations == []
