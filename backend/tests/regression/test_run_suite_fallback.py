"""A run's suite label falls back to the suites its test cases actually name.

Reported as *"fix the unknown suite name in the dropdowns"*: the agents page's
run pickers read ``Unknown suite · Run #12`` for every option.

``TestRun.primary_suite_name`` / ``suite_names`` are a **denormalisation** of
``test_cases.suite_name``, maintained by ``_update_run_aggregates``. Rows that
never went through that path — seeded and legacy runs — carried NULL despite
their test cases naming suites perfectly well. Measured on the reference
deployment: 1241 runs had both columns, and **298 had a NULL primary while
their own test cases named a suite**. The data was there; only the copy was
missing.

This is the read half of the effective-suite rule the repo already states: any
query that reads a suite must consider **both** ``tc.suite_name`` and
``tr.primary_suite_name``. The write half already works for live ingestion, so
deriving at read time fixes every existing row without a migration or a
backfill sweep.

The properties worth guarding are not "the label is right" but:

1. A **recorded** label always wins. It is a user's chosen run name; a derived
   one must never overwrite it.
2. The derivation is **batched** — a picker showing 25 runs must not fire 25
   queries.
3. Nothing is invented when there is nothing to derive from.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from app.services.runs_service import enrich_runs_with_release


class _Run:
    """Minimal TestRun stand-in — ``serialize_run`` reads attributes only."""

    def __init__(self, primary=None, suite_names=None):
        self.id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.primary_suite_name = primary
        self.suite_names = suite_names


@pytest.fixture(autouse=True)
def _serialize_passthrough(monkeypatch):
    """``serialize_run`` pulls dozens of columns; only the suite fields matter
    here, so keep the fixture honest and small."""
    import app.services.runs_service as svc

    monkeypatch.setattr(
        svc,
        "serialize_run",
        lambda run: {
            "id": str(run.id),
            "primary_suite_name": run.primary_suite_name,
            "suite_names": run.suite_names,
        },
    )


def _enrich(run, derived):
    return enrich_runs_with_release(
        [run], {}, None, run_seq_map=None, suite_map={str(run.id): derived},
    )[0]


# ── The fix ──────────────────────────────────────────────────────────────────

def test_a_run_with_no_recorded_suite_uses_the_one_its_tests_name():
    """The reported bug. Three suites on the test cases, nothing on the run."""
    run = _Run(primary=None, suite_names=None)
    item = _enrich(run, ["AuthenticationSuite", "AuthorizationSuite", "PasswordSuite"])
    assert item["primary_suite_name"] == "AuthenticationSuite"
    assert item["suite_names"] == [
        "AuthenticationSuite", "AuthorizationSuite", "PasswordSuite",
    ]


@pytest.mark.asyncio
async def test_the_derived_primary_is_deterministic():
    """Whatever order the database returns rows in, the label a user sees must
    not change between two loads of the same page.

    Asserted against ``fetch_run_suites_map``, which is where the ordering is
    decided — an earlier version of this test went through the enrichment
    helper instead, which simply takes ``derived[0]`` and therefore could not
    observe the sort at all. It passed with the sort deleted.
    """
    from app.services.runs_service import fetch_run_suites_map

    run_id = uuid.uuid4()

    class _Result:
        def all(self):
            # Deliberately unsorted, as a database is free to return.
            return [
                (run_id, "PasswordSuite"),
                (run_id, "AuthenticationSuite"),
                (run_id, "AuthorizationSuite"),
            ]

    class _DB:
        async def execute(self, *args, **kwargs):
            return _Result()

    mapped = await fetch_run_suites_map(_DB(), [run_id])
    assert mapped[str(run_id)] == [
        "AuthenticationSuite", "AuthorizationSuite", "PasswordSuite",
    ]


# ── A recorded label always wins ─────────────────────────────────────────────

def test_a_recorded_primary_is_never_overwritten():
    """It is the user's chosen run label — e.g. the testng.xml <suite name>.
    Overwriting it with a dominant test-class name is a bug this repo has
    already had once, and the fix must not reintroduce it."""
    run = _Run(primary="API Regression Multi-Class", suite_names=None)
    item = _enrich(run, ["com.example.OrderApiRegressionTests"])
    assert item["primary_suite_name"] == "API Regression Multi-Class"


def test_a_recorded_suite_list_is_never_overwritten():
    run = _Run(primary=None, suite_names=["Recorded Suite"])
    item = _enrich(run, ["Derived A", "Derived B"])
    assert item["suite_names"] == ["Recorded Suite"]


def test_a_partial_record_is_completed_not_replaced():
    """Four runs on the reference deployment had a suite list but no primary."""
    run = _Run(primary=None, suite_names=["Recorded Suite"])
    item = _enrich(run, ["Derived A"])
    assert item["suite_names"] == ["Recorded Suite"]
    assert item["primary_suite_name"] == "Derived A"


# ── Nothing is invented ──────────────────────────────────────────────────────

def test_nothing_is_derived_when_the_tests_name_no_suite():
    """A run whose tests genuinely carry no suite must stay empty — the UI's
    own "Unknown suite" wording is honest there."""
    run = _Run(primary=None, suite_names=None)
    item = _enrich(run, [])
    assert item["primary_suite_name"] is None
    assert not item["suite_names"]


def test_enrichment_without_a_suite_map_changes_nothing():
    """Callers that never ask for suites must be unaffected."""
    run = _Run(primary=None, suite_names=None)
    item = enrich_runs_with_release([run], {}, None, run_seq_map=None)[0]
    assert item["primary_suite_name"] is None


# ── Batched, not N+1 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_derivation_is_one_query_for_the_whole_page():
    """A picker showing 25 runs must not fire 25 queries."""
    from app.services.runs_service import fetch_run_suites_map

    calls = []

    class _Result:
        def all(self):
            return []

    class _DB:
        async def execute(self, *args, **kwargs):
            calls.append(1)
            return _Result()

    await fetch_run_suites_map(_DB(), [uuid.uuid4() for _ in range(25)])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_no_run_ids_fires_no_query():
    from app.services.runs_service import fetch_run_suites_map

    class _DB:
        async def execute(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("should not query for an empty page")

    assert await fetch_run_suites_map(_DB(), []) == {}


def test_blank_suite_names_are_excluded_at_the_query():
    """An empty-string suite is not a suite. Letting one through would render
    a blank label, which is worse than "Unknown" because it looks like a bug
    in the page rather than missing data."""
    import app.services.runs_service as svc

    source = inspect.getsource(svc.fetch_run_suites_map)
    assert "isnot(None)" in source
    assert 'func.trim(TestCase.suite_name) != ""' in source


def test_the_derivation_is_scoped_to_the_requested_runs():
    """``test_run_id.in_(run_ids)`` — an unscoped read would blend runs, and
    a suite from someone else's run is worse than no suite."""
    import app.services.runs_service as svc

    source = inspect.getsource(svc.fetch_run_suites_map)
    assert "TestCase.test_run_id.in_(run_ids)" in source


def test_both_run_read_paths_derive_suites():
    """The list and the single-run fetch feed different pages; fixing only one
    would leave the other reading "Unknown suite"."""
    import app.services.runs_service as svc

    for func_name in ("list_project_runs", "get_run_with_release"):
        source = inspect.getsource(getattr(svc, func_name))
        assert "fetch_run_suites_map" in source, func_name
        assert "suite_map=suite_map" in source, func_name
