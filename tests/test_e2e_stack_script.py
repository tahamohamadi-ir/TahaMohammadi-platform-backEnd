"""Regression pin: BACKEND-101 replaced the e2e stack script's monorepo path.

``scripts/run_e2e_stack.sh`` must resolve the admin SPA dist inside the
documented admin-panel repository (PROJECT-MANIFEST.md: ``Front-End/admin-panel``,
Vite default outDir ``dist``), not the legacy monorepo ``apps/admin`` layout.
Content-level assertion only — the dist itself exists only after a build.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_e2e_stack.sh"


def test_run_e2e_stack_script_exists() -> None:
    assert SCRIPT.is_file()


def test_run_e2e_stack_uses_admin_panel_repo_path() -> None:
    content = SCRIPT.read_text(encoding="utf-8")
    assert "../Front-End/admin-panel/dist" in content
    assert '"$CMS_ROOT/../admin/dist"' not in content


def test_run_e2e_stack_keeps_fail_closed_spa_check() -> None:
    content = SCRIPT.read_text(encoding="utf-8")
    assert '[[ ! -f "$ADMIN_DIST/index.html" ]]' in content
    assert "exit 1" in content
