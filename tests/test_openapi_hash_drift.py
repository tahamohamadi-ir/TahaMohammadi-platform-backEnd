"""OpenAPI hash drift enforcement (BACKEND-140).

The accepted artifact hashes, path counts, and versions below are locked by
``Docs/03-contracts/OPENAPI-ACCEPTANCE.md`` (provenance ``scaffold-accepted``,
backend commit ``82e3984520154b60146009ae4a0d21eb5c30373e``). Any content change
to the exported snapshots without regenerating ``PROVENANCE.json`` and creating
a new acceptance record fails these tests and reopens PS-05.

Line-ending provenance: the acceptance record hashed CRLF-encoded bytes
(``0f672693…`` / ``1328f824…`` / ``618ab188…``). The repository now normalizes
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
        "sha256": "1328f8244c5541f225648082891a0a1244961c0dead6692488992ac8c7606f09",
        "paths": 47,
        "version": "0.1.0",
    },
    "endpoint-inventory.md": {
        "sha256": "618ab18826875a5f27357ab87a3917082291d2e8610959b18aa2f936a7f3aa96",
        "operations": 103,
    },
}

# Hashes of the same content with LF line endings (canonical repo form).
CANONICAL_LF = {
    "public-openapi.json": "be8fdbea748aa5215d20ceb4140434fc4e90582c667e66607956cb02ebaf5f94",
    "admin-openapi.json": "46e456c63e878a85625fbfcfdc028a66c6ce5becbdb029c87b062a4641746f46",
    "endpoint-inventory.md": "08310cd1dd0de009028cc005e3b3881fda17e1e711132221e4ceecb737ba57d9",
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
