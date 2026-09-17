"""Prove M09 fallback labels, suite scope, links, and provider failures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST = "backend/tests/test_exploratory_m09_search_retrieval.py"
MUTATIONS = [
    (
        ROOT / "backend/app/services/semantic_search.py",
        "hybrid-swallows-chroma-failure",
        "        if raise_on_provider_error:\n            raise\n",
        "        if False and raise_on_provider_error:\n            raise\n",
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "suite-groups-across-projects",
        "        .group_by(effective_suite, TestRun.project_id)\n",
        "        .group_by(effective_suite)\n",
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "suite-drops-project-identity",
        '            "project_id": str(row.project_id),\n',
        '            "project_id": None,\n',
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "suite-link-is-unescaped-and-unscoped",
        '''            "navigation_url": "/coverage/suite?" + urlencode(
                {"name": row.suite_name, "project_id": str(row.project_id)}
            ),
''',
        '            "navigation_url": f"/coverage/suite?name={row.suite_name}",\n',
    ),
    (
        ROOT / "backend/app/routers/search.py",
        "similar-search-hides-database-failure",
        '''    tc_result = await db.execute(
        select(
            TestCase.test_name,
            TestCase.error_message,
            TestRun.project_id,
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(TestCase.id == source_id)
    )
    row = tc_result.first()
''',
        '''    try:
        tc_result = await db.execute(
            select(
                TestCase.test_name,
                TestCase.error_message,
                TestRun.project_id,
            )
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(TestCase.id == source_id)
        )
        row = tc_result.first()
    except Exception:
        row = None
''',
    ),
    (
        ROOT / "backend/app/routers/search.py",
        "entity-counts-overlap-session-operations",
        '''    test_run = await _count(test_runs_q)
    test_case = await _count(test_cases_q)
    suite = await _count(suites_q)
    defect = await _count(defects_q)
    flaky_test = await _count(flaky_q)
    release = await _count(releases_q)
''',
        '''    import asyncio as _asyncio
    test_run, test_case, suite, defect, flaky_test, release = await _asyncio.gather(
        _count(test_runs_q), _count(test_cases_q), _count(suites_q),
        _count(defects_q), _count(flaky_q), _count(releases_q),
    )
''',
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "global-search-hides-adapter-failure",
        "                failed_entity_types.append(entity_type)\n",
        "                pass  # hide the failed source\n",
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "global-search-claims-capped-sample-is-exact",
        "                if effective_limit is not None and len(results) >= effective_limit:\n",
        "                if False and effective_limit is not None and len(results) >= effective_limit:\n",
    ),
    (
        ROOT / "backend/app/services/semantic_search.py",
        "hybrid-later-pages-use-first-page-pool",
        "    candidate_size = 200\n",
        "    candidate_size = min(size * 2, 200)\n",
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "suite-result-identity-collides-across-projects",
        '            "entity_id": f"{row.project_id}:{row.suite_name}",\n',
        '            "entity_id": row.suite_name,\n',
    ),
    (
        ROOT / "backend/app/services/global_search_service.py",
        "flaky-rate-filter-runs-after-limit",
        '''        .having(
            total_runs >= 5,
            fail_count * 100 >= total_runs * 10,
            fail_count * 100 <= total_runs * 90,
        )
''',
        '''        .having(total_runs >= 5)
''',
    ),
]


def _run(label: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m09-{label}",
            TEST,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    baseline = _run("baseline")
    if baseline.returncode != 0:
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)

    for target, name, good, bad in MUTATIONS:
        original = target.read_bytes()
        source = original.decode("utf-8")
        if source.count(good) != 1:
            raise AssertionError(f"{name} mutation must apply exactly once")
        try:
            target.write_text(source.replace(good, bad, 1), encoding="utf-8", newline="")
            mutated = _run(name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{name} mutation was not killed with pytest exit 1\n"
                    + mutated.stdout
                    + mutated.stderr
                )
        finally:
            target.write_bytes(original)
        if target.read_bytes() != original:
            raise AssertionError(f"{name} mutation did not restore its target")

    print(f"M09 search-retrieval mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
