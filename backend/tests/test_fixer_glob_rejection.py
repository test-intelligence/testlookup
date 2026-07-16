"""Test-code-only glob rejection matrix (AI-2).

A generated diff may touch ONLY files matching the project's test globs.
Rejection is structural — it happens BEFORE any sandbox execution.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer.pipeline import (  # noqa: E402
    diff_touched_paths,
    glob_match,
    patch_touches_only_test_globs,
    summarize_patch,
)

DEFAULT_GLOBS = ["tests/**", "**/*.spec.*", "**/*.test.*"]


def _diff(*paths: str) -> str:
    out = []
    for p in paths:
        out.append(f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n")
    return "".join(out)


@pytest.mark.parametrize("path,pattern,expected", [
    ("tests/test_a.py", "tests/**", True),
    ("tests/sub/dir/test_a.py", "tests/**", True),
    ("src/app.py", "tests/**", False),
    ("frontend/x.spec.ts", "**/*.spec.*", True),
    ("x.spec.js", "**/*.spec.*", True),
    ("frontend/x.test.tsx", "**/*.test.*", True),
    ("src/app.py", "**/*.spec.*", False),
    ("tests/data.json", "tests/**", True),
])
def test_glob_match(path, pattern, expected):
    assert glob_match(path, pattern) is expected


def test_pure_test_diff_accepted():
    ok, offending = patch_touches_only_test_globs(_diff("tests/test_a.py"), DEFAULT_GLOBS)
    assert ok is True and offending == []


def test_diff_touching_product_code_rejected():
    ok, offending = patch_touches_only_test_globs(
        _diff("tests/test_a.py", "src/app.py"), DEFAULT_GLOBS,
    )
    assert ok is False
    assert offending == ["src/app.py"]


def test_spec_and_test_suffix_files_accepted():
    ok, _ = patch_touches_only_test_globs(
        _diff("frontend/login.spec.ts", "api/user.test.js"), DEFAULT_GLOBS,
    )
    assert ok is True


def test_empty_diff_rejected():
    ok, offending = patch_touches_only_test_globs("", DEFAULT_GLOBS)
    assert ok is False and offending == []


def test_diff_touched_paths_dedupes_a_and_b():
    paths = diff_touched_paths(_diff("tests/test_a.py"))
    assert paths == ["tests/test_a.py"]


def test_summarize_patch_counts():
    summary = summarize_patch(_diff("tests/test_a.py"))
    assert "tests/test_a.py" in summary
    assert "+1" in summary and "-1" in summary
