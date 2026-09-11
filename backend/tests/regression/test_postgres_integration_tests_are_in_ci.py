"""Every real-Postgres integration test must be named in the CI job that runs them.

The ``Backend — PostgreSQL/Mongo integration and rollback`` job does not
discover ``tests/integration/``; it passes an explicit list of files to pytest.
A new ``*_postgres*.py`` file is therefore **not run by CI at all** until
someone remembers to add it — and it fails no build while it sits there, so the
omission looks exactly like a passing test.

That is the same shape as a guard that reports success because it never looked.
This check closes it: adding the file to the repo forces adding it to the job.

(Measured when this was written: 13 of 13 existing files were correctly listed,
so this pins a healthy state rather than papering over a backlog.)

It has since earned its keep: re-audit batch 2 added
``test_search_tags_index_postgres.py`` without listing it, and this went red.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INTEGRATION_DIR = REPO_ROOT / "backend" / "tests" / "integration"


def test_every_postgres_integration_file_is_listed_in_the_ci_job():
    if not CI_WORKFLOW.exists():
        pytest.skip("ci.yml not present in this checkout")

    listed = set(
        re.findall(r"tests/integration/(\S+\.py)", CI_WORKFLOW.read_text(encoding="utf-8"))
    )
    on_disk = sorted(p.name for p in INTEGRATION_DIR.glob("*_postgres*.py"))

    # If the glob ever matches nothing the assertion below is vacuous, and this
    # test would pass while proving nothing.
    assert on_disk, (
        f"no *_postgres*.py files found under {INTEGRATION_DIR} — the glob is "
        f"wrong, so this check cannot see anything"
    )

    missing = [name for name in on_disk if name not in listed]
    assert not missing, (
        "these real-Postgres integration tests exist but CI never runs them — "
        "add them to the 'Run protected PostgreSQL integration suite' step in "
        f".github/workflows/ci.yml: {missing}"
    )


def test_ci_authority_seed_supplies_non_null_execution_defaults():
    """Pin the raw seed to the migrated ``test_cases`` write contract."""
    if not CI_WORKFLOW.exists():
        pytest.skip("ci.yml not present in this checkout")

    source = CI_WORKFLOW.read_text(encoding="utf-8")
    seed = source.split(
        "- name: Seed disposable authority rows for integration fixtures",
        1,
    )[1].split("- name: Run protected PostgreSQL integration suite", 1)[0]
    test_case_insert = seed.split("INSERT INTO test_cases", 1)[1].split(
        '"""',
        1,
    )[0]

    assert "steps_present" in test_case_insert
    assert "'seeded integration failure', false" in test_case_insert


def test_a_listed_file_outside_tests_integration_carries_the_marker():
    """The job filters with ``-m integration``, and only ``tests/integration`` is
    marked automatically (``tests/conftest.py``). A listed file anywhere else is
    deselected -- silently, the job stays green -- unless it is marked itself."""
    if not CI_WORKFLOW.exists():
        pytest.skip("ci.yml not present in this checkout")

    source = CI_WORKFLOW.read_text(encoding="utf-8")
    step = source.split("- name: Run protected PostgreSQL integration suite", 1)[1]
    step = step.split("- name:", 1)[0]
    named = re.findall(r"(?<![\w/])tests/[\w/]+\.py", step)
    assert len(named) >= 20, f"the step names too few files to be the right one: {named}"

    unmarked = [
        rel for rel in named
        if not rel.startswith("tests/integration/")
        and "mark.integration" not in (REPO_ROOT / "backend" / rel).read_text(encoding="utf-8")
    ]
    assert not unmarked, (
        f"-m integration deselects these, so CI lists them but never runs them: {unmarked}"
    )
