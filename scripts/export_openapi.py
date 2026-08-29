"""Export source-generated OpenAPI snapshots for frontend contract review.

This command deliberately imports the two Ninja APIs in-process. It does not
fetch a protected endpoint, seed data, or replace the authenticated endpoint
acceptance required by the shared OpenAPI artifact contract.

Usage (PowerShell):
  $env:DJANGO_SETTINGS_MODULE = 'config.settings.development'
  .venv/Scripts/python.exe scripts/export_openapi.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "docs" / "contracts" / "openapi" / "current"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_endpoint_inventory(path: Path, public_schema: dict, admin_schema: dict) -> int:
    """Write a stable review table from the generated schemas, not guessed routes."""
    rows = [
        "# Generated endpoint inventory",
        "",
        "Status: source-generated-unaccepted. Do not implement against this file until the endpoint-access fixtures and contract acceptance are complete.",
        "",
        "| Surface | Method | Path | Summary |",
        "|---|---|---|---|",
    ]
    count = 0
    for surface, schema in (("public", public_schema), ("admin", admin_schema)):
        for path_name, path_item in sorted(schema.get("paths", {}).items()):
            for method, operation in sorted(path_item.items()):
                if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                summary = str(operation.get("summary", "")).replace("|", "\\|")
                rows.append(f"| {surface} | {method.upper()} | `{path_name}` | {summary} |")
                count += 1
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return count


def main() -> None:
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

    import django

    django.setup()

    from apps.api.admin_api import admin_api
    from apps.api.api import api

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    public_path = OUTPUT_DIRECTORY / "public-openapi.json"
    admin_path = OUTPUT_DIRECTORY / "admin-openapi.json"
    public_schema = api.get_openapi_schema()
    admin_schema = admin_api.get_openapi_schema()
    write_json(public_path, public_schema)
    write_json(admin_path, admin_schema)
    inventory_path = OUTPUT_DIRECTORY / "endpoint-inventory.md"
    endpoint_count = write_endpoint_inventory(inventory_path, public_schema, admin_schema)

    provenance = {
        "status": "source-generated-unaccepted",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceCommit": git_commit(),
        "djangoSettingsModule": os.environ["DJANGO_SETTINGS_MODULE"],
        "command": "python scripts/export_openapi.py",
        "accessEvidence": {
            "public": "Generated in-process; anonymous endpoint client check pending.",
            "admin": "Generated in-process; verified staff plus OTP endpoint fixture pending.",
        },
        "artifacts": {
            "public-openapi.json": {
                "sha256": sha256(public_path),
                "paths": len(public_schema.get("paths", {})),
                "version": public_schema.get("info", {}).get("version"),
            },
            "admin-openapi.json": {
                "sha256": sha256(admin_path),
                "paths": len(admin_schema.get("paths", {})),
                "version": admin_schema.get("info", {}).get("version"),
            },
            "endpoint-inventory.md": {"sha256": sha256(inventory_path), "operations": endpoint_count},
        },
    }
    write_json(OUTPUT_DIRECTORY / "PROVENANCE.json", provenance)
    print(f"Exported {public_path}")
    print(f"Exported {admin_path}")


if __name__ == "__main__":
    main()
