"""Prove M06 source regressions reject a helper that preserves docstrings."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/tests/regression/test_release_read_axis.py"
TESTS = (
    "tests/regression/test_release_read_axis.py::"
    "test_the_predicate_is_conditional_not_null_tolerant",
    "tests/regression/test_release_read_axis.py::"
    "test_the_predicate_reads_the_denormalized_column_not_the_link_table",
)
GOOD = """\
    tree = ast.parse(textwrap.dedent(src))
    function = tree.body[0]
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise AssertionError("expected function source")
    if not function.body or not isinstance(function.body[0], ast.Expr):
        return src
    doc_node = function.body[0]
    if not isinstance(doc_node.value, ast.Constant) or not isinstance(
        doc_node.value.value, str
    ):
        return src
    lines = src.splitlines(keepends=True)
    del lines[doc_node.lineno - 1 : doc_node.end_lineno]
    return "".join(lines)
"""
BAD = """\
    return src
"""


def main() -> None:
    original = TARGET.read_bytes()
    source = original.decode("utf-8")
    count = source.count(GOOD)
    if count != 1:
        raise AssertionError(f"mutation must apply exactly once; found {count}")
    try:
        mutated = source.replace(GOOD, BAD, 1)
        if mutated == source:
            raise AssertionError("mutation did not change source")
        TARGET.write_text(mutated, encoding="utf-8", newline="")
        run = subprocess.run(
            [sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:testlookup"],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            timeout=60,
        )
        if run.returncode == 0:
            raise AssertionError("docstring-preserving helper mutation survived")
    finally:
        TARGET.write_bytes(original)
    if TARGET.read_bytes() != original:
        raise AssertionError("mutation restoration failed")
    print("M06 source-helper mutation check: 1 mutation killed")


if __name__ == "__main__":
    main()
