"""Every read keyed on a test fingerprint must also name a project or a run.

A fingerprint is ``hash(test_name + class_name)``; it is not salted per
project, so two projects holding the same test share one. A query that filters
on the fingerprint alone reads every project's rows. Three did (D-07, S-A-3,
S-A-4): the analysis pipeline's history counts ("16/106 runs" on a project
with 53 runs), the ML trainer's history features, and the flaky coach's test
name and suite lookup.

This guard walks each statement that filters on a fingerprint and requires the
same statement to bind a project (``project_id``) or a run (``test_run_id``).
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

_FINGERPRINT_FILTER = re.compile(
    r"test_fingerprint\.in_\(|test_fingerprint\s*==\s*(?!\w+\.c\.)|test_fingerprint\s*=\s*ANY"
    r"|ON\s+\w+\.test_(?:fingerprint|name)\s*=\s*\w+\.test_(?:fingerprint|name)",
    re.IGNORECASE,
)
_STATEMENT_START = re.compile(r"\bselect\(|\bSELECT\b|\bupdate\(|\bdelete\(")
# A run id, a project id, a project filter template, or a project's cluster ids
# (``SystemicFlakeCluster`` rows are loaded per project before members are read).
_SCOPE = re.compile(r"project_id|test_run_id|\{project_filter\}|cluster_id\.in_\(")


def _statement_around(lines: list[str], index: int) -> str:
    start = index
    while start > 0 and not _STATEMENT_START.search(lines[start]):
        start -= 1
    end = index
    depth = 0
    for end in range(start, min(len(lines), index + 25)):
        depth += lines[end].count("(") - lines[end].count(")")
        if end >= index and depth <= 0:
            break
    return "\n".join(lines[start : end + 1])


def _unscoped_reads() -> list[str]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.lstrip().startswith("#") or not _FINGERPRINT_FILTER.search(line):
                continue
            if not _SCOPE.search(_statement_around(lines, index)):
                found.append(f"{path.relative_to(APP.parent)}:{index + 1}: {line.strip()}")
    return found


def test_no_fingerprint_read_crosses_projects():
    assert _unscoped_reads() == []


def test_the_guard_catches_an_unscoped_read(tmp_path):
    """The guard must be able to fail: the coach lookup as it was."""
    module = tmp_path / "app" / "services" / "leak.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "tc_stmt = (\n"
        "    select(TestCase.test_fingerprint, TestCase.suite_name)\n"
        "    .where(TestCase.test_fingerprint.in_(fps))\n"
        "    .distinct(TestCase.test_fingerprint)\n"
        ")\n",
        encoding="utf-8",
    )
    global APP
    saved, APP = APP, tmp_path / "app"
    try:
        assert len(_unscoped_reads()) == 1
    finally:
        APP = saved
