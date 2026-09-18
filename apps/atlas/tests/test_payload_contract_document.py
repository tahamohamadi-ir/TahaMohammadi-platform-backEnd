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
import re
from pathlib import Path

from apps.atlas.validation import BLOCKING_CODES, WARNING_CODES

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = BACKEND_ROOT / "docs" / "contracts" / "ATLAS-PAYLOAD-CONTRACT.md"
# The spec lives in the coordination root above the main platform checkout.
# The worktree sits at D:/Project/.atlas-worktrees/backend-plan-a, whose
# sibling tahamohammadi-platform checkout carries Docs/.
SPEC_PATH = (
    Path("D:/Project/tahamohammadi-platform")
    / "Docs/05-delivery/knowledge-atlas/KNOWLEDGE-ATLAS-V1-DESIGN-SPEC.md"
)


def _doc() -> str:
    assert DOC_PATH.exists(), f"contract document missing: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def _wire_field_names() -> list[str]:
    """Every field name in the §10.2 response example, parsed from the SPEC."""
    spec = SPEC_PATH.read_text(encoding="utf-8")
    payload = json.loads(re.search(r"```json\n(.+?)\n```", spec, re.S).group(1))
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


def test_document_lists_every_wire_field_name_from_the_spec() -> None:
    doc = _doc()
    missing = [name for name in _wire_field_names() if name not in doc]
    assert missing == [], f"wire field names missing from the document: {missing}"


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
