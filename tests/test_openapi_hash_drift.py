"""OpenAPI hash drift enforcement (BACKEND-140).

The accepted artifact hashes, path counts, and versions below are locked by
``Docs/03-contracts/OPENAPI-ACCEPTANCE.md`` (provenance ``scaffold-accepted``,
backend commit ``edecc10188c207df88a3c7bceaf31dcd6fcfc5ec``, acceptance
addendum 2026-09-02 — G-E media delete + G-G profile sibling-locale). Any
content change to the exported snapshots without regenerating
``PROVENANCE.json`` and creating a new acceptance record fails these tests and
reopens PS-05.

Line-ending provenance: the acceptance record hashed CRLF-encoded bytes
(``0f672693…`` / ``60e5aba0…`` / ``df2d1921…``). The repository now normalizes
text to LF (``.gitattributes`` ``eol=lf``), so the committed files hash
differently while being byte-identical modulo line endings. These tests hash
the LF-canonical content and additionally prove that re-encoding it with CRLF
reproduces the accepted hashes exactly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "docs/contracts/openapi/current"

# Hashes exactly as recorded in OPENAPI-ACCEPTANCE.md and PROVENANCE.json
# (computed over CRLF-encoded bytes at acceptance time).
ACCEPTED = {
    "public-openapi.json": {
        "sha256": "0f672693de28ed33286789e5119eb3226c062693fb15168b1aba5513c257c0a5",
        "paths": 40,
        "version": "0.4.0",
    },
    "admin-openapi.json": {
        "sha256": "60e5aba0e19426ced2b0386aad23cef388f5909c22294fd5799aea533cf13bfc",
        "paths": 48,
        "version": "0.1.0",
    },
    "endpoint-inventory.md": {
        "sha256": "df2d1921d7fda90169dd5e1926a94c5d3e229be245ed6cac3a3abdb19fb70605",
        "operations": 105,
    },
}

# Hashes of the same content with LF line endings (canonical repo form).
CANONICAL_LF = {
    "public-openapi.json": "be8fdbea748aa5215d20ceb4140434fc4e90582c667e66607956cb02ebaf5f94",
    "admin-openapi.json": "5b56abe2fbb062293435329658749515f1c035a08e4fd8ba2eb80a1dc4e572b2",
    "endpoint-inventory.md": "fe05fc1e375b26802367372936d246fdebe9c4ed26a3e930b3462f08158dfdd3",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _provenance() -> dict:
    return json.loads((CONTRACTS_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename", sorted(ACCEPTED))
def test_accepted_artifact_content_unchanged(filename):
    artifact = CONTRACTS_DIR / filename
    assert artifact.exists(), f"Accepted artifact missing: {artifact}"
    canonical = artifact.read_bytes().replace(b"\r\n", b"\n")
    assert _sha256(canonical) == CANONICAL_LF[filename], (
        f"{filename} drifted from the accepted content; reopen PS-05 per "
        "Docs/03-contracts/OPENAPI-ACCEPTANCE.md before adopting client types."
    )


@pytest.mark.parametrize("filename", sorted(ACCEPTED))
def test_canonical_content_reencodes_to_accepted_hash(filename):
    canonical = (CONTRACTS_DIR / filename).read_bytes().replace(b"\r\n", b"\n")
    crlf = canonical.replace(b"\n", b"\r\n")
    assert _sha256(crlf) == ACCEPTED[filename]["sha256"], (
        f"{filename} content no longer matches the accepted acceptance-record hash"
    )


@pytest.mark.parametrize("filename", ["public-openapi.json", "admin-openapi.json"])
def test_artifact_shape_matches_acceptance(filename):
    schema = json.loads((CONTRACTS_DIR / filename).read_text(encoding="utf-8"))
    accepted = ACCEPTED[filename]
    assert len(schema["paths"]) == accepted["paths"]
    assert schema["info"]["version"] == accepted["version"]


def test_provenance_matches_accepted_record():
    provenance = _provenance()
    assert provenance["status"] == "scaffold-accepted"
    artifacts = provenance["artifacts"]
    for filename, expected in ACCEPTED.items():
        recorded = artifacts[filename]
        assert recorded["sha256"] == expected["sha256"], filename
        for field in ("paths", "version", "operations"):
            if field in expected:
                assert recorded[field] == expected[field], filename
    assert provenance["acceptanceRecord"].endswith("OPENAPI-ACCEPTANCE.md")


def test_provenance_hash_matches_artifact_content():
    provenance = _provenance()
    for filename, recorded in provenance["artifacts"].items():
        canonical = (CONTRACTS_DIR / filename).read_bytes().replace(b"\r\n", b"\n")
        assert _sha256(canonical.replace(b"\n", b"\r\n")) == recorded["sha256"], filename
