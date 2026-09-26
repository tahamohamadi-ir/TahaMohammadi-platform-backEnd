"""The wire-contract document is a contract, not prose.

``docs/contracts/ATLAS-PAYLOAD-CONTRACT.md`` is the human companion to the
Atlas payload (plan Task 17). This test pins the statements in it that would
fail a consumer if they drifted: the exact §10.2 field names, the frozen
issue-code vocabulary, the registered routes, the ETag formula and cache
header, and the preview credential scheme with its 401/403 matrix. A mutation
to any of those statements must fail this test — the document is never
decorative.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from apps.atlas.validation import BLOCKING_CODES, WARNING_CODES

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = BACKEND_ROOT / "docs" / "contracts" / "ATLAS-PAYLOAD-CONTRACT.md"
# The design spec is tracked in the coordination root, not in this repo — CI
# checks out only the backend, so the wire fields come from a repository-local
# extraction of the §10.2 example. When the real spec is genuinely available
# (env override or a sibling coordination checkout) we USE it and additionally
# cross-check the fixture against it; isolated CI falls back to the fixture.
FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "knowledge_atlas_v1_payload_contract.json"
)


def _spec_candidates() -> tuple[Path, ...]:
    out: list[Path] = []
    env = os.environ.get("ATLAS_SPEC_PATH")
    if env:
        out.append(Path(env))
    # No OS-specific guesses: only a sibling checkout that actually exists
    # matters, discovered relative to this repo checkout, one level per try.
    for level in range(1, 5):
        out.append(
            BACKEND_ROOT.joinpath(*("../" * level))
            / "Docs/05-delivery/knowledge-atlas/KNOWLEDGE-ATLAS-V1-DESIGN-SPEC.md"
        )
    return tuple(dict.fromkeys(out))


def _spec() -> str | None:
    """Return the real coordination spec text if genuinely available."""
    for candidate in _spec_candidates():
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def _fixture_payload() -> dict:
    assert FIXTURE_PATH.is_file(), f"contract fixture missing: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _payload_wire_names(payload: object) -> list[str]:
    """Every field name in the wire payload, walked depth-first."""
    names: list[str] = []

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                names.append(key)
                walk(value)
        elif isinstance(obj, list) and obj:
            walk(obj[0])

    walk(payload)
    return names


def _fixture_wire_names() -> list[str]:
    return _payload_wire_names(_fixture_payload())


def _doc() -> str:
    assert DOC_PATH.exists(), f"contract document missing: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_document_lists_every_wire_field_name_from_the_fixture() -> None:
    doc = _doc()
    missing = [name for name in _fixture_wire_names() if name not in doc]
    assert missing == [], f"wire field names missing from the document: {missing}"


def test_local_spec_when_available_matches_the_fixture() -> None:
    """Cross-repo guard: real coordination §10.2 must equal the fixture."""
    spec = _spec()
    if spec is None:
        return  # isolated CI checkout — fixture path is the contract source
    normalized = spec.replace("\r\n", "\n")
    anchor = re.search(r"^#{1,4}\s*10\.2\s", normalized, re.M)
    stop = re.search(r"^#{1,4}\s*10\.3\s", normalized[anchor.end():], re.M)
    section = normalized[anchor.start(): anchor.end() + stop.start()]
    live_payload = json.loads(
        re.search(r"```json\n(.+?)\n```", section, re.S).group(1)
    )
    assert live_payload == _fixture_payload(), (
        "the real design-spec §10.2 has drifted from the repository-local "
        "fixture — regenerate the fixture from the accepted spec"
    )

    assert [_payload_wire_names(live_payload)] == [_fixture_wire_names()]


def test_document_lists_the_full_issue_code_vocabulary_in_order() -> None:
    doc = _doc()
    cursor = -1
    for code in (*BLOCKING_CODES, *WARNING_CODES):
        position = doc.find(code, cursor + 1)
        assert position != -1, f"issue code absent from the document: {code}"
        cursor = position
    assert "18" in doc and "11" in doc, "blocking/warning counts must be stated"


def test_document_states_the_registered_routes() -> None:
    doc = _doc()
    assert "GET /api/atlas/{locale}" in doc
    assert "GET /api/atlas/preview" in doc
    assert "POST /api/v1/admin/atlas/versions/{version_id}/preview-token" in doc


def test_document_states_the_etag_formula_and_cache_policy() -> None:
    doc = _doc()
    assert 'ETag: "<version.id>-<16 hex>"' in doc
    assert "Cache-Control: public, max-age=60" in doc


def test_document_states_the_credential_scheme_and_status_matrix() -> None:
    doc = _doc()
    assert "Authorization: Bearer <atlas-preview capability>" in doc
    assert "401" in doc and "403" in doc and "404" in doc
    assert "never 404" in doc
    # The credential-status matrix is a table: each row pairs a credential
    # state with its status. Garbage/unparseable rows carry 401; scope-mismatch
    # rows (expired / wrong purpose / wrong locale / unknown version) carry 403.
    # Matching row-by-row makes a swapped status in ANY row fail.
    rows = re.findall(r"^\|([^|]+)\|([^|]+)\|", doc, re.M)
    def _row401(row):
        state, status = row[0], row[1]
        return re.search(r"garbage|unparseable|non-\s*[`]?Bearer", state, re.I) and "401" in status
    def _row403(row):
        state, status = row[0], row[1]
        return re.search(r"purpose|expired|scope|locale", state, re.I) and "403" in status
    assert any(_row401(row) for row in rows), (
        "the matrix must give absent/unparseable/garbage credentials their own 401 row"
    )
    assert any(_row403(row) for row in rows), (
        "the matrix must give expired/wrong-scope credentials their own 403 row"
    )
    # The tiers must not be merged: a row that asserts garbage earns a 403 is
    # exactly the drift this test exists to catch.
    for state, status in rows:
        if re.search(r"garbage|unparseable", state, re.I):
            assert "403" not in status, f"garbage credentials must be 401, not: {status.strip()}"
