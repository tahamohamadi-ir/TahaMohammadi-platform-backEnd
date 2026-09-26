"""Test-suite hygiene guard.

A duplicated top-level test function silently replaces its namesake: Python binds
the second definition over the first, so a file with two ``def test_x`` collects
and runs **one** test while the run still looks complete. That happened during
Plan A (Task 7): a real-row test reused an existing test's name and the suite
reported a green count one lower than the number of definitions.

The guard scans exactly what pytest collects under its two roots: every
``test_*.py`` and ``*_test.py`` file under ``tests/`` and ``apps/`` — the mirror
of ``testpaths``/``python_files`` in ``pyproject.toml``, not just files inside a
``tests/`` directory. A collectable duplicate the guard cannot see is a hole the
check falls through, so the globs stay deliberately redundant
(``apps/**/test_*.py`` subsumes the older ``apps/**/tests/**/test_*.py`` shape);
overlap between them is deduplicated below.

This is deliberately a collected test rather than an ad-hoc script so the check
cannot rot, and it is non-vacuous: it asserts it actually found test files.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: Mirrors ``testpaths = ["tests", "apps"]`` x ``python_files`` in
#: ``pyproject.toml``: pytest collects any matching file below either root, so
#: the guard must cover the same set. ``apps/**/test_*.py`` subsumes the older
#: ``apps/**/tests/**/test_*.py``; ``_test_files`` deduplicates the overlap.
TEST_GLOBS = (
    "tests/**/test_*.py",
    "tests/**/*_test.py",
    "apps/**/test_*.py",
    "apps/**/*_test.py",
)
TEST_PREFIX = "test_"


def _test_files() -> list[Path]:
    """Every collectable test file, deduplicated (the globs may overlap)."""
    return sorted({path for pattern in TEST_GLOBS for path in REPO_ROOT.glob(pattern)})


def _top_level_test_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith(TEST_PREFIX)
    ]


def test_the_guard_finds_test_files():
    files = _test_files()
    assert len(files) >= 10, f"guard is vacuous: only found {len(files)} test files"
    assert any(f.name == "test_test_hygiene.py" for f in files)


def test_no_duplicate_top_level_test_names():
    duplicates: dict[str, list[str]] = {}
    for path in _test_files():
        names = _top_level_test_names(path)
        seen: set[str] = set()
        for name in names:
            if name in seen:
                duplicates.setdefault(path.relative_to(REPO_ROOT).as_posix(), []).append(name)
            seen.add(name)
    assert duplicates == {}, f"duplicate top-level test names silently shadow a test: {duplicates}"
