"""The two keyword-search shapes must agree, and neither may leak across projects.

``build_search_filters`` has two code paths. Terms of ``MIN_INDEXED_TERM_LEN``
characters or more resolve the step and suite operands as uncorrelated
``= ANY(<array subquery>)``, which lets Postgres bitmap-index-scan
``test_cases`` instead of sequentially scanning it. Shorter terms keep the
original correlated ``EXISTS``, because pg_trgm pads them into boundary
trigrams whose posting lists cover nearly the whole table, making the
index-driven shape measurably the worse plan.

Two shapes is a correctness risk, so both are pinned here against real
Postgres -- the array/``ANY`` form cannot be exercised on SQLite:

* **They must return the same rows.** The threshold is monkeypatched so the
  *same* term goes through each path in turn; a shape difference shows up as a
  row difference rather than depending on which term a future test picks.
* **Neither may leak across projects.** The correlated form relied on the
  ``EXISTS`` correlating on the test's own canonical anchor. The new form has
  no correlation at all: it gathers canonical ids from steps across every
  project and matches them against ``test_cases.canonical_test_case_id``. That
  is still safe -- ``canonical_test_cases`` rows are per-(project, fingerprint),
  so an id gathered from project A can never equal a project-B test's anchor --
  but "still safe by argument" is exactly the claim a leak test should not take
  on trust.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

NEEDLE = "zzleakzz"
# A test case reachable ONLY through its run's primary_suite_name — the
# run-level label that surfaces live_stream runs whose per-event suite_name
# is the test class name. Dropping that operand from the indexed path used to
# fail nothing.
SUITE_NEEDLE = "qqsuiteqq"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Fixture:
    """Two projects. Project A owns a canonical test whose STEP carries the
    needle; project B has an unrelated test. The needle appears nowhere in
    either project's test names, suites or error messages, so the only route to
    a row is the step operand under test."""

    def __init__(self) -> None:
        self.project_a = uuid.uuid4()
        self.project_b = uuid.uuid4()
        self.suite_a = uuid.uuid4()
        self.suite_b = uuid.uuid4()
        self.canonical_a = uuid.uuid4()
        self.run_a = uuid.uuid4()
        self.run_b = uuid.uuid4()
        self.tag = uuid.uuid4().hex[:8]

    async def create(self, conn) -> None:
        for pid, name in ((self.project_a, "a"), (self.project_b, "b")):
            await conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, is_active) "
                    "VALUES (:i, :n, :s, true)"
                ),
                {
                    "i": pid,
                    "n": "scope-" + name + "-" + self.tag,
                    "s": "scope-" + name + "-" + self.tag,
                },
            )
        for sid, pid in ((self.suite_a, self.project_a), (self.suite_b, self.project_b)):
            await conn.execute(
                text(
                    "INSERT INTO test_suites (id, project_id, name) "
                    "VALUES (:i, :p, 'suite')"
                ),
                {"i": sid, "p": pid},
            )
        await conn.execute(
            text(
                "INSERT INTO canonical_test_cases "
                "(id, project_id, test_suite_id, test_fingerprint, test_name) "
                "VALUES (:i, :p, :s, :f, 'owning test')"
            ),
            {
                "i": self.canonical_a,
                "p": self.project_a,
                "s": self.suite_a,
                "f": "fp-a-" + self.tag,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO test_steps "
                "(canonical_test_case_id, ordinal, depth, name, status) "
                "VALUES (:c, 1, 0, :n, 'FAILED')"
            ),
            {"c": self.canonical_a, "n": "step " + NEEDLE + " detail"},
        )
        for rid, pid in ((self.run_a, self.project_a), (self.run_b, self.project_b)):
            await conn.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, build_number, status, "
                    "primary_suite_name) "
                    "VALUES (:i, :p, 'b1', 'COMPLETED', :label)"
                ),
                {
                    "i": rid,
                    "p": pid,
                    "label": (
                        SUITE_NEEDLE + " regression"
                        if rid == self.run_a
                        else "ordinary suite"
                    ),
                },
            )
        await conn.execute(
            text(
                "INSERT INTO test_cases (id, test_run_id, test_name, status, "
                "test_fingerprint, canonical_test_case_id) "
                "VALUES (:i, :r, 'owning test', 'FAILED', :f, :c)"
            ),
            {
                "i": uuid.uuid4(),
                "r": self.run_a,
                "f": "fp-a-" + self.tag,
                "c": self.canonical_a,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO test_cases (id, test_run_id, test_name, status, "
                "test_fingerprint) "
                "VALUES (:i, :r, 'unrelated test', 'FAILED', :f)"
            ),
            {"i": uuid.uuid4(), "r": self.run_b, "f": "fp-b-" + self.tag},
        )

    async def destroy(self, conn) -> None:
        await conn.execute(
            text("DELETE FROM test_cases WHERE test_run_id IN (:a, :b)"),
            {"a": self.run_a, "b": self.run_b},
        )
        await conn.execute(
            text("DELETE FROM test_steps WHERE canonical_test_case_id = :c"),
            {"c": self.canonical_a},
        )
        await conn.execute(
            text("DELETE FROM test_runs WHERE id IN (:a, :b)"),
            {"a": self.run_a, "b": self.run_b},
        )
        await conn.execute(
            text("DELETE FROM canonical_test_cases WHERE id = :c"),
            {"c": self.canonical_a},
        )
        await conn.execute(
            text("DELETE FROM test_suites WHERE id IN (:a, :b)"),
            {"a": self.suite_a, "b": self.suite_b},
        )
        await conn.execute(
            text("DELETE FROM projects WHERE id IN (:a, :b)"),
            {"a": self.project_a, "b": self.project_b},
        )


SHAPES = (("indexed", 0), ("correlated", 999))


async def test_run_level_suite_label_is_searchable_in_both_shapes(monkeypatch):
    """A test whose only match is its run's ``primary_suite_name`` must still be
    found. The label lives on ``test_runs``, so the indexed path reaches it via
    ``test_run_id = ANY(<runs whose label matches>)`` while the correlated path
    reads the column directly — two different mechanisms for one behaviour, and
    neither had a test until dropping the operand failed nothing."""
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _Fixture()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        for label, threshold in SHAPES:
            monkeypatch.setattr(ss, "MIN_INDEXED_TERM_LEN", threshold)
            async with session_factory() as db:
                _, found, _ = await ss.search_test_cases_query(
                    db, q=SUITE_NEEDLE, page=1, size=20,
                    project_id=str(fixture.project_a),
                )
                _, elsewhere, _ = await ss.search_test_cases_query(
                    db, q=SUITE_NEEDLE, page=1, size=20,
                    project_id=str(fixture.project_b),
                )
            assert found == 1, (
                label + " shape: a test findable only by its run's "
                "primary_suite_name was not returned"
            )
            assert elsewhere == 0, (
                label + " shape: project A's suite label surfaced a row under "
                "project B"
            )
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()


async def test_a_step_in_another_project_never_surfaces(monkeypatch):
    """Both shapes, same assertion: project A finds its own test through the
    step text; project B finds nothing."""
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _Fixture()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        for label, threshold in SHAPES:
            monkeypatch.setattr(ss, "MIN_INDEXED_TERM_LEN", threshold)
            async with session_factory() as db:
                _, own, _ = await ss.search_test_cases_query(
                    db, q=NEEDLE, page=1, size=20, project_id=str(fixture.project_a)
                )
                _, other, _ = await ss.search_test_cases_query(
                    db, q=NEEDLE, page=1, size=20, project_id=str(fixture.project_b)
                )
            assert own == 1, label + " shape: project A lost its own step match"
            assert other == 0, (
                label + " shape: project A's step surfaced a row under project "
                "B's filter -- cross-tenant leak"
            )
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()


@pytest.mark.parametrize(
    "term", [NEEDLE, SUITE_NEEDLE, "owning", "unrelated", "qqzzxx"]
)
async def test_both_shapes_return_identical_results(monkeypatch, term):
    """The same term through each code path must produce the same rows.

    Forcing the threshold rather than picking short and long terms means this
    compares the SHAPES, not two different queries.
    """
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _Fixture()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        results = {}
        for label, threshold in SHAPES:
            monkeypatch.setattr(ss, "MIN_INDEXED_TERM_LEN", threshold)
            async with session_factory() as db:
                items, total, _ = await ss.search_test_cases_query(
                    db, q=term, page=1, size=50
                )
            results[label] = (
                total,
                sorted(str(item["test_case_id"]) for item in items),
            )

        assert results["indexed"] == results["correlated"], (
            "the two search shapes disagree on " + repr(term) + ": "
            "indexed=" + repr(results["indexed"])
            + " correlated=" + repr(results["correlated"])
        )
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()
