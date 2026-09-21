"""VIZ-213 — the large synthetic dataset generator, without a database.

The row generators are pure, so everything about their shape is checked
here: counts per scale (by arithmetic for medium/large — nothing iterates a
million rows), determinism, FK consistency, aggregate consistency, the skew
the docstring promises, and the safety rails on the CLI. The real COPY load
and ``--wipe`` run in ``tests/integration/test_viz_seed_postgres.py``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.pass_rate import canonical_pass_rate
from app.models.postgres import Base
from app.services.flaky_signals import error_signature
from scripts import seed_large_dataset as sld
from scripts.seed_large_dataset import (
    COPY_TABLES,
    SCALES,
    SIGNATURE_COUNT,
    SIGNATURE_TEMPLATES,
    DatasetIdentity,
    build_catalogue,
    build_run,
    iter_canonical,
    iter_releases,
    iter_run_batches,
    iter_suites,
    pipeline_bounds,
    plan_counts,
    render_signature,
    suite_sizes,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SMALL = SCALES["small"]
IDENTITY = DatasetIdentity(slug="synthetic-perf-unit-test", seed=7)

_EXECUTED = ("PASSED", "FAILED", "BROKEN")
_FAILING = ("FAILED", "BROKEN")


@pytest.fixture(scope="module")
def small():
    """Every row of the small dataset, fully materialised once."""
    batches = list(iter_run_batches(SMALL, IDENTITY, NOW))
    return SimpleNamespace(
        releases=list(iter_releases(SMALL, IDENTITY, NOW)),
        suites=list(iter_suites(SMALL, IDENTITY)),
        canonical=list(iter_canonical(SMALL, IDENTITY)),
        runs=[run for batch in batches for run in batch.runs],
        cases=[case for batch in batches for case in batch.cases],
        links=[link for batch in batches for link in batch.links],
        batches=batches,
    )


# ── counts per scale ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scale", "runs", "releases", "suites", "cases"),
    [
        ("small", 20, 4, 10, 2_000),
        ("medium", 500, 10, 50, 100_000),
        ("large", 5_000, 20, 200, 1_000_000),
    ],
)
def test_plan_counts_per_scale(scale, runs, releases, suites, cases):
    counts = plan_counts(scale)
    assert (counts.runs, counts.releases, counts.suites, counts.test_cases) == (
        runs,
        releases,
        suites,
        cases,
    )
    assert counts.linked_runs + counts.unattributed_runs == counts.runs
    assert 0 < counts.unattributed_runs < counts.runs // 10
    assert (
        counts.canonical_tests == SCALES[scale].tests_per_run * SCALES[scale].pipelines
    )


def test_small_scale_generates_exactly_what_plan_counts_promises(small):
    counts = plan_counts("small")
    assert len(small.releases) == counts.releases
    assert len(small.suites) == counts.suites
    assert len(small.canonical) == counts.canonical_tests
    assert len(small.runs) == counts.runs
    assert len(small.cases) == counts.test_cases
    assert len(small.links) == counts.linked_runs
    assert (
        sum(1 for run in small.runs if run.primary_release_id is None)
        == counts.unattributed_runs
    )


def test_every_run_carries_tests_per_run_cases(small):
    per_run = Counter(case.test_run_id for case in small.cases)
    assert set(per_run.values()) == {SMALL.tests_per_run}


def test_suite_sizes_partition_the_catalogue_for_every_scale():
    for spec in SCALES.values():
        sizes = suite_sizes(spec)
        assert len(sizes) == spec.suites
        assert min(sizes) >= 1
        assert sum(sizes) == spec.tests_per_run * spec.pipelines


# ── determinism ──────────────────────────────────────────────────────────────


def test_generation_is_deterministic(small):
    again = list(iter_run_batches(SMALL, IDENTITY, NOW))
    assert [b.runs for b in again] == [b.runs for b in small.batches]
    assert [b.cases for b in again] == [b.cases for b in small.batches]
    assert [b.links for b in again] == [b.links for b in small.batches]
    assert list(iter_releases(SMALL, IDENTITY, NOW)) == small.releases
    assert list(iter_canonical(SMALL, IDENTITY)) == small.canonical


def test_batch_size_does_not_change_the_rows(small):
    rebatched = list(iter_run_batches(SMALL, IDENTITY, NOW, batch_runs=3))
    assert len(rebatched) == 7
    assert [run for b in rebatched for run in b.runs] == small.runs
    assert [case for b in rebatched for case in b.cases] == small.cases


def test_any_run_can_be_rebuilt_alone(small):
    catalogue = build_catalogue(SMALL)
    run, cases, link = build_run(SMALL, IDENTITY, NOW, catalogue, 13)
    assert run == small.runs[13]
    assert cases == [case for case in small.cases if case.test_run_id == run.id]
    assert link == next(link for link in small.links if link.test_run_id == run.id)


def test_a_different_slug_or_seed_is_a_different_dataset(small):
    other = DatasetIdentity(slug="synthetic-perf-unit-test", seed=8)
    assert other.project_id != IDENTITY.project_id
    assert list(iter_run_batches(SMALL, other, NOW))[0].runs != small.batches[0].runs
    assert DatasetIdentity(slug="elsewhere", seed=7).project_id != IDENTITY.project_id


# ── FK consistency ───────────────────────────────────────────────────────────


def test_every_case_references_a_generated_run_and_canonical(small):
    run_ids = {run.id for run in small.runs}
    canonical_ids = {row.id for row in small.canonical}
    for case in small.cases:
        assert case.test_run_id in run_ids
        assert case.canonical_test_case_id in canonical_ids
    canonical_by_id = {row.id: row for row in small.canonical}
    for case in small.cases:
        assert (
            canonical_by_id[case.canonical_test_case_id].test_fingerprint
            == case.test_fingerprint
        )


def test_every_run_references_a_generated_release_or_none(small):
    release_ids = {release.id for release in small.releases}
    for run in small.runs:
        assert run.primary_release_id is None or run.primary_release_id in release_ids
        assert run.project_id == IDENTITY.project_id


def test_every_canonical_references_a_generated_suite(small):
    suite_ids = {suite.id for suite in small.suites}
    for row in small.canonical:
        assert row.test_suite_id in suite_ids


def test_links_mirror_primary_release_id_exactly(small):
    by_run = {link.test_run_id: link for link in small.links}
    assert len(by_run) == len(small.links), "one link per run"
    for run in small.runs:
        link = by_run.get(run.id)
        if run.primary_release_id is None:
            assert link is None
        else:
            assert link is not None and link.is_primary
            assert link.release_id == run.primary_release_id
            assert link.project_id == run.project_id


def test_fingerprints_are_unique_within_a_run(small):
    seen = Counter((case.test_run_id, case.test_fingerprint) for case in small.cases)
    assert max(seen.values()) == 1


@pytest.mark.parametrize("scale", sorted(SCALES))
def test_fingerprints_are_file_ingestions_own_and_fit_the_column(scale):
    """A real report ingested into the synthetic project must land on the same
    canonical rows, so the recipe is ingestion's — called, not copied."""
    from app.services.ingestion import make_test_fingerprint

    catalogue = build_catalogue(SCALES[scale])
    for t in catalogue[::37] + catalogue[-3:]:
        assert t.test_fingerprint == make_test_fingerprint(t.test_name, t.class_name)
    # (project_id, test_fingerprint) is unique on canonical_test_cases.
    assert len({t.test_fingerprint for t in catalogue}) == len(catalogue)
    width = max(len(t.test_fingerprint) for t in catalogue)
    for table in ("canonical_test_cases", "test_cases"):
        assert (
            width <= Base.metadata.tables[table].columns["test_fingerprint"].type.length
        ), table


def test_generated_rows_carry_the_ingestion_fingerprint(small):
    from app.services.ingestion import make_test_fingerprint

    for row in small.canonical[::11]:
        assert row.test_fingerprint == make_test_fingerprint(
            row.test_name, row.class_name
        )
    for case in small.cases[::97]:
        assert case.test_fingerprint == make_test_fingerprint(
            case.test_name, case.class_name
        )


def test_ids_are_unique_across_the_dataset(small):
    ids = [
        row.id
        for rows in (
            small.releases,
            small.suites,
            small.canonical,
            small.runs,
            small.cases,
            small.links,
        )
        for row in rows
    ]
    assert len(set(ids)) == len(ids)


def test_pipeline_bounds_are_real_runs_of_that_pipeline():
    for spec in SCALES.values():
        for pipeline in range(spec.pipelines):
            first, last = pipeline_bounds(spec, pipeline)
            assert 0 <= first <= last < spec.runs
            assert (
                first % spec.pipelines == pipeline and last % spec.pipelines == pipeline
            )
            assert (
                last + spec.pipelines >= spec.runs
            ), "nothing later executes this pipeline"


# ── aggregates ───────────────────────────────────────────────────────────────


def test_run_aggregates_equal_their_cases(small):
    cases_by_run: dict = {}
    for case in small.cases:
        cases_by_run.setdefault(case.test_run_id, []).append(case)
    for run in small.runs:
        counts = Counter(case.status for case in cases_by_run[run.id])
        assert run.total_tests == len(cases_by_run[run.id])
        assert run.passed_tests == counts["PASSED"]
        assert run.failed_tests == counts["FAILED"]
        assert run.skipped_tests == counts["SKIPPED"]
        assert run.broken_tests == counts["BROKEN"]
        assert run.unknown_tests == counts["UNKNOWN"]
        assert run.pass_rate == canonical_pass_rate(
            run.passed_tests, run.failed_tests, run.broken_tests
        )
        timed = [
            case.duration_ms
            for case in cases_by_run[run.id]
            if case.duration_ms is not None
        ]
        assert run.duration_ms == (sum(timed) if timed else None)
        if run.failed_tests + run.broken_tests:
            assert run.status == "FAILED"


def test_runs_are_ordered_oldest_first_inside_the_history(small):
    starts = [run.start_time for run in small.runs]
    assert starts == sorted(starts)
    assert starts[0] >= NOW - timedelta(days=SMALL.history_days)
    assert starts[-1] <= NOW
    for run in small.runs:
        assert run.created_at == run.start_time and run.end_time >= run.start_time


# ── skew and realism ─────────────────────────────────────────────────────────


def test_a_few_suites_hold_most_of_the_tests(small):
    per_suite = Counter(case.suite_name for case in small.cases)
    ranked = sorted(per_suite.values(), reverse=True)
    top = max(1, len(ranked) // 5)
    assert sum(ranked[:top]) > 0.5 * len(small.cases), ranked


def test_about_three_percent_fail_concentrated_in_few_signatures(small):
    failing = [case for case in small.cases if case.status in _FAILING]
    share = len(failing) / len(small.cases)
    assert 0.015 <= share <= 0.05, share
    assert all(case.error_message and case.failure_category for case in failing)

    signatures = Counter(error_signature(case.error_message) for case in failing)
    assert "" not in signatures
    assert 3 <= len(signatures) <= SIGNATURE_COUNT
    top_five = sum(count for _sig, count in signatures.most_common(5))
    assert top_five >= 0.5 * len(failing)
    # Raw messages differ where signatures do not.
    assert len({case.error_message for case in failing}) > len(signatures)


def _expected_fail_rate(hot: int, size: int) -> float:
    return (hot * sld._HOT_FAIL_P + (size - hot) * sld._COLD_FAIL_P) / size


@pytest.mark.parametrize("scale", sorted(SCALES))
def test_hot_tests_are_spread_over_every_pipeline(scale):
    """Arithmetic over the catalogue (2,000 entries at large — no runs built).

    Bound asserted: the per-pipeline EXPECTED failure rate is identical to
    within float error (spread ≤ 1e-12). Every run executes one pipeline's
    fixed slice and the failure draw depends only on hot/cold, so equal slice
    sizes and equal hot counts give equal expected rates; anything looser
    would admit the bug this pins (at large, pipeline 0 at 9.8% against 2.25%).
    """
    spec = SCALES[scale]
    catalogue = build_catalogue(spec)
    size = Counter(t.index % spec.pipelines for t in catalogue)
    hot = Counter(t.index % spec.pipelines for t in catalogue if t.hot)
    for pipeline in range(spec.pipelines):
        assert size[pipeline] == spec.tests_per_run
        assert hot[pipeline] >= 1, f"pipeline {pipeline} has no hot test"
    rates = [_expected_fail_rate(hot[p], size[p]) for p in range(spec.pipelines)]
    assert max(rates) - min(rates) <= 1e-12, rates
    # And the docstring's ~3% overall share survives the move.
    assert sum(hot.values()) / len(catalogue) == pytest.approx(1 / sld._HOT_TEST_EVERY)
    assert 0.025 <= _expected_fail_rate(sum(hot.values()), len(catalogue)) <= 0.035


@pytest.mark.parametrize("scale", sorted(SCALES))
def test_every_pipeline_executes_hot_tests_in_generated_runs(scale):
    """Sampled, not generated in full: one run per pipeline (the first
    ``pipelines`` runs), a few thousand cases even at large scale."""
    spec = SCALES[scale]
    catalogue = build_catalogue(spec)
    hot_ids = {IDENTITY.id("canonical", t.index) for t in catalogue if t.hot}
    for run_index in range(spec.pipelines):
        run, cases, _link = build_run(spec, IDENTITY, NOW, catalogue, run_index)
        assert run.jenkins_job == f"perf-pipeline-{run_index:02d}"
        assert any(
            case.canonical_test_case_id in hot_ids for case in cases
        ), run.jenkins_job


def test_failures_and_flaky_flags_come_from_every_pipeline(small):
    job_of = {run.id: run.jenkins_job for run in small.runs}
    jobs = set(job_of.values())
    assert len(jobs) == SMALL.pipelines
    flaky_jobs = {job_of[case.test_run_id] for case in small.cases if case.is_flaky_run}
    assert flaky_jobs == jobs, "retries/flaky flags must not all come from one job"
    failing = Counter(
        job_of[case.test_run_id] for case in small.cases if case.status in _FAILING
    )
    total = Counter(job_of[case.test_run_id] for case in small.cases)
    rates = {job: failing[job] / total[job] for job in jobs}
    # Measured (seeded, so deterministic): 1,000 cases per pipeline at small,
    # binomial sd ≈ 0.55 points at a 3% rate. Before the fix the expected gap
    # alone was 1.5 points (3.76% vs 2.25%); the arithmetic test above is the
    # exact pin, this one only catches a gross regression in generated rows.
    assert max(rates.values()) - min(rates.values()) <= 0.02, rates


def test_the_thirty_templates_are_thirty_signatures():
    import random

    assert SIGNATURE_COUNT == 30
    first = {
        error_signature(render_signature(i, random.Random(1), NOW)[0])
        for i in range(SIGNATURE_COUNT)
    }
    second = {
        error_signature(render_signature(i, random.Random(2), NOW)[0])
        for i in range(SIGNATURE_COUNT)
    }
    assert len(first) == 30
    assert first == second, "noise must not leak into the signature"
    assert all(sig for sig in first)
    assert len(SIGNATURE_TEMPLATES) == 30


def test_durations_are_null_for_some_executed_cases_and_all_unexecuted(small):
    executed = [case for case in small.cases if case.status in _EXECUTED]
    null_share = sum(1 for case in executed if case.duration_ms is None) / len(executed)
    assert 0.01 <= null_share <= 0.06, null_share
    for case in small.cases:
        if case.status in ("SKIPPED", "UNKNOWN"):
            assert case.duration_ms is None
        elif case.duration_ms is not None:
            assert 1 <= case.duration_ms <= sld._MAX_DURATION_MS
    timed = sorted(
        case.duration_ms for case in executed if case.duration_ms is not None
    )
    median = timed[len(timed) // 2]
    # Log-normal: a long right tail, the mean well above the median.
    assert 200 <= median <= 1500, median
    assert sum(timed) / len(timed) > median


def test_passing_cases_carry_no_error_and_some_retried(small):
    for case in small.cases:
        if case.status not in _FAILING:
            assert case.error_message is None and case.failure_category is None
            assert case.has_attachments is False
    assert any(case.retry_count == 1 and case.is_flaky_run for case in small.cases)


def test_dimensions_are_populated(small):
    assert {run.environment for run in small.runs} <= set(sld.ENVIRONMENTS)
    assert len({run.environment for run in small.runs}) >= 2
    assert len({run.branch for run in small.runs}) >= 2
    assert {run.ingestion_source for run in small.runs} <= set(sld.INGESTION_SOURCES)
    assert {release.status for release in small.releases} <= {
        "planning",
        "in_progress",
        "released",
        "cancelled",
        "archived",
    }
    assert all(release.sort_key for release in small.releases)
    assert all(sld.DATASET_MARKER in release.description for release in small.releases)


# ── COPY column lists ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("table", "row_type"), COPY_TABLES, ids=[t for t, _ in COPY_TABLES]
)
def test_copy_columns_exist_and_cover_every_required_column(table, row_type):
    """COPY names its columns; a NOT NULL column with no server default that
    the row shape omits fails on the first batch. Checked against the ORM
    metadata, so no database is needed to catch a drift."""
    columns = Base.metadata.tables[table].columns
    fields = set(row_type._fields)
    unknown = fields - {column.name for column in columns}
    assert not unknown, f"{table}: not columns: {sorted(unknown)}"
    required = {
        column.name
        for column in columns
        if not column.nullable and column.server_default is None
    }
    missing = required - fields
    assert not missing, f"{table}: required columns omitted: {sorted(missing)}"


def test_the_marked_project_is_recognisable():
    assert sld.DATASET_MARKER in IDENTITY.description
    assert IDENTITY.slug in IDENTITY.project_name


# ── CLI safety rails ─────────────────────────────────────────────────────────


@pytest.fixture
def no_database(monkeypatch):
    """Any attempt to open a connection fails the test."""

    async def _refuse(dsn):
        raise AssertionError(f"tried to connect to {dsn!r}")

    monkeypatch.setattr(sld, "_connect", _refuse)


def _settings(**overrides):
    values = {
        "is_production": False,
        "DATABASE_URL": "postgresql+asyncpg://perf:s3cret@db.internal:5432/testlookup",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_refuses_to_run_in_production(monkeypatch, no_database, capsys):
    monkeypatch.setattr(sld, "settings", _settings(is_production=True))
    assert sld.main(["--scale", "small", "--yes"]) == 2
    assert sld.main(["--wipe", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "production" in err


def test_requires_yes_to_write_and_prints_the_target(monkeypatch, no_database, capsys):
    monkeypatch.setattr(sld, "settings", _settings())
    assert sld.main(["--scale", "medium"]) == 2
    out = capsys.readouterr().out
    assert "db.internal:5432/testlookup" in out
    assert "s3cret" not in out
    assert "100,000" in out
    assert "--yes" in out


def test_requires_yes_to_wipe(monkeypatch, no_database, capsys):
    monkeypatch.setattr(sld, "settings", _settings())
    assert sld.main(["--wipe"]) == 2
    assert "--yes" in capsys.readouterr().out


def test_refuses_without_a_database_url(monkeypatch, no_database, capsys):
    monkeypatch.setattr(sld, "settings", _settings(DATABASE_URL=""))
    assert sld.main(["--scale", "small", "--yes"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_yes_hands_the_chosen_scale_and_slug_to_the_writer(monkeypatch, no_database):
    monkeypatch.setattr(sld, "settings", _settings())
    calls = []

    async def _fake_write(url, spec, *, identity):
        calls.append((url, spec.name, identity))
        return plan_counts(spec)

    monkeypatch.setattr(sld, "write_dataset", _fake_write)
    assert (
        sld.main(["--scale", "medium", "--yes", "--slug", "perf-x", "--seed", "3"]) == 0
    )
    assert calls == [
        (_settings().DATABASE_URL, "medium", DatasetIdentity(slug="perf-x", seed=3))
    ]


def test_yes_and_wipe_hand_the_slug_to_the_wiper(monkeypatch, no_database):
    monkeypatch.setattr(sld, "settings", _settings())
    calls = []

    async def _fake_wipe(url, *, identity):
        calls.append((url, identity))
        return 1

    monkeypatch.setattr(sld, "wipe_dataset", _fake_wipe)
    assert sld.main(["--wipe", "--yes", "--slug", "perf-x"]) == 0
    assert calls == [(_settings().DATABASE_URL, DatasetIdentity(slug="perf-x"))]


# ── --wipe statement order ───────────────────────────────────────────────────


class _RecordingConnection:
    """Stands in for an asyncpg connection: records every statement, returns
    the marked project and ``run_count`` run ids. No database involved."""

    def __init__(self, project_id, run_count: int):
        self.project_id = project_id
        self.run_ids = [IDENTITY.id("run", i) for i in range(run_count)]
        self.statements: list[tuple[str, tuple]] = []
        self.closed = False

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args))
        return self.project_id

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        return [{"id": run_id} for run_id in self.run_ids]

    async def execute(self, sql, *args):
        self.statements.append((sql, args))

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def close(self):
        self.closed = True


def _wipe_with(monkeypatch, run_count: int) -> _RecordingConnection:
    import asyncio

    conn = _RecordingConnection(IDENTITY.project_id, run_count)

    async def _fake_connect(dsn):
        return conn

    monkeypatch.setattr(sld, "_connect", _fake_connect)
    assert (
        asyncio.run(
            sld.wipe_dataset(
                "postgresql://fake/none", identity=IDENTITY, log=lambda *_: None
            )
        )
        == 1
    )
    assert conn.closed
    return conn


def _deleted_table(sql: str) -> str:
    import re

    match = re.match(r"\s*DELETE FROM (\w+)", sql)
    assert match, sql
    return match.group(1)


def test_wipe_deletes_in_an_order_that_never_rewrites_a_canonical_row(monkeypatch):
    """Canonicals go before runs: first_seen/last_seen/deleted_at_run_id are
    SET NULL pointers to runs, so deleting runs first rewrites the canonical
    rows (on a table shared by every project). Cases go before canonicals for
    the same reason on test_cases.canonical_test_case_id. 450 runs → three
    chunks of 200, so the chunked phases are exercised."""
    conn = _wipe_with(monkeypatch, run_count=450)
    deletes = [
        _deleted_table(sql)
        for sql, _ in conn.statements
        if sql.lstrip().startswith("DELETE")
    ]
    phases = [
        table for i, table in enumerate(deletes) if i == 0 or deletes[i - 1] != table
    ]
    assert phases == [
        "test_cases",
        "release_test_run_links",
        "canonical_test_cases",
        "test_suites",
        "test_runs",
        "releases",
        "projects",
    ]
    # Chunked where the row count scales with runs.
    assert Counter(deletes)["test_cases"] == 3
    assert Counter(deletes)["test_runs"] == 3
    deleted_runs = [
        run_id
        for sql, args in conn.statements
        if _is_run_delete(sql)
        for run_id in args[1]
    ]
    assert deleted_runs == conn.run_ids


def _is_run_delete(sql: str) -> bool:
    return sql.lstrip().startswith("DELETE FROM test_runs")


def test_wipe_matches_the_marker_exactly_and_scopes_every_statement(monkeypatch):
    conn = _wipe_with(monkeypatch, run_count=5)
    for sql, args in conn.statements:
        # "_" in the marker is a LIKE wildcard: no pattern matching anywhere.
        assert " like " not in f" {sql.lower()} ", sql
    lookup_sql, lookup_args = conn.statements[0]
    assert "strpos(description, $2) > 0" in lookup_sql
    assert lookup_args == (IDENTITY.slug, sld.DATASET_MARKER)
    # Everything after the lookup is bound to the marked project's id.
    for sql, args in conn.statements[1:]:
        assert IDENTITY.project_id in args, sql
        if sql.lstrip().startswith("DELETE FROM projects"):
            assert "WHERE id = $1" in sql
        else:
            assert "project_id = $1" in sql, sql


def test_wipe_of_a_missing_project_issues_no_delete(monkeypatch):
    import asyncio

    conn = _RecordingConnection(None, run_count=0)

    async def _fake_connect(dsn):
        return conn

    monkeypatch.setattr(sld, "_connect", _fake_connect)
    assert (
        asyncio.run(
            sld.wipe_dataset(
                "postgresql://fake/none", identity=IDENTITY, log=lambda *_: None
            )
        )
        == 0
    )
    assert not any(sql.lstrip().startswith("DELETE") for sql, _ in conn.statements)


def test_asyncpg_dsn_drops_the_sqlalchemy_driver_and_keeps_the_password():
    assert (
        sld.asyncpg_dsn("postgresql+asyncpg://u:p%40ss@h:5433/d")
        == "postgresql://u:p%40ss@h:5433/d"
    )
    assert sld.describe_target("postgresql+asyncpg://u:p@h/d") == "h:5432/d as u"
