"""CI gate: fresh OpenAPI re-export must match the accepted provenance record.

Re-runs ``scripts/export_openapi.py`` against the current source tree, then
compares the freshly written artifacts with the SHA-256 values recorded in
``docs/contracts/openapi/current/PROVENANCE.json``. The accepted record was
hashed over CRLF-encoded bytes, so both sides are compared through the same
CRLF re-encoding rule used by ``tests/test_openapi_hash_drift.py`` and
documented in ``Docs/03-contracts/OPENAPI-ARTIFACT-CONTRACT.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "docs" / "contracts" / "openapi" / "current"
ACCEPTED_STATUS = "scaffold-accepted"


def crlf_sha256(path: Path) -> str:
    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical.replace(b"\n", b"\r\n")).hexdigest()


def main() -> int:
    provenance_path = OUTPUT_DIRECTORY / "PROVENANCE.json"
    accepted = json.loads(provenance_path.read_text(encoding="utf-8"))
    if accepted.get("status") != ACCEPTED_STATUS:
        print(
            f"FAIL: {provenance_path} status is {accepted.get('status')!r}, "
            f"expected {ACCEPTED_STATUS!r}; there is no accepted record to gate against."
        )
        return 1

    # export_openapi.py rewrites the artifacts AND the provenance record (as
    # "source-generated-unaccepted"). Snapshot every tracked file it touches so
    # a local verification run leaves the accepted working tree byte-identical.
    restore: dict[Path, bytes | None] = {}
    for candidate in (provenance_path, *(
        OUTPUT_DIRECTORY / name for name in accepted["artifacts"]
    )):
        restore[candidate] = (
            candidate.read_bytes() if candidate.exists() else None
        )

    env = {**os.environ, "DJANGO_SETTINGS_MODULE": accepted["djangoSettingsModule"]}
    result = subprocess.run(
        [sys.executable, "scripts/export_openapi.py"],
        cwd=REPOSITORY_ROOT,
        env=env,
        check=False,
    )

    matched: list[tuple[str, str]] = []
    failures: list[str] = []
    for filename, record in accepted["artifacts"].items():
        artifact = OUTPUT_DIRECTORY / filename
        if not artifact.exists():
            failures.append(f"{filename}: fresh export missing")
            continue
        digest = crlf_sha256(artifact)
        if digest == record["sha256"]:
            matched.append((filename, digest))
        else:
            failures.append(
                f"{filename}: fresh sha256 {digest} != accepted {record['sha256']}"
            )

    # Restore the accepted working tree (artifacts + provenance) exactly as
    # they were before this verification ran.
    for path, blob in restore.items():
        if blob is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(blob)

    for filename, digest in matched:
        print(f"MATCH {filename}: sha256 {digest}")
    for failure in failures:
        print(f"FAIL: {failure}")
    if result.returncode != 0:
        print(f"FAIL: scripts/export_openapi.py exited with {result.returncode}.")
        return result.returncode
    if failures:
        print(
            "Fresh export drifted from the accepted provenance; reopen PS-05 per "
            "Docs/03-contracts/OPENAPI-ACCEPTANCE.md before adopting new artifacts."
        )
        return 1
    print("OpenAPI export matches accepted provenance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
