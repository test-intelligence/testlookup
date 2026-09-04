"""Regression: a go/no-go verdict for a RELEASE, append-only (S6a-1).

What this table is for
----------------------
``ReleaseDecision`` is keyed ``test_run_id`` UNIQUE — one verdict per run,
answering "is this run shippable?". That is a real question and it is not the
one a release manager asks. "Is 2.4.0 shippable?" is a judgement over every run
attributed to the release, and it has no run to hang off.

Three properties, each with a specific way of going wrong
---------------------------------------------------------
**Append-only.** Re-evaluating inserts and demotes; it never updates a verdict
in place. A gate that rewrites its own past cannot answer "why did we ship
that?" six months later, which is the question it exists for.

**Exactly one CURRENT verdict**, per release and per phase, enforced by two
PARTIAL unique indexes. Partial matters twice over: unfiltered, the index would
permit exactly one verdict per release ever and destroy the history; and split
across ``phase_id IS NULL`` / ``IS NOT NULL`` because Postgres treats NULLs as
distinct, so one combined index would leave the release-level rows
unconstrained — the very rows the first index exists to constrain.

**The snapshot is authoritative.** Denominator, run set, policy and rollup are
stored, not recomputed. Retention deletes runs and policies get edited; a
verdict that recomputed itself on read would silently restate history under
today's inputs.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from app.models.postgres import ReleaseGateDecision
from app.services import release_gate_decision_service as svc


class TestTheIndexesEncodeTheInvariant:
    def test_both_current_indexes_are_partial_on_is_current(self):
        by_name = {i.name: i for i in ReleaseGateDecision.__table__.indexes}

        for name in ("ix_rgd_release_current", "ix_rgd_phase_current"):
            index = by_name[name]
            assert index.unique, f"{name} must be unique or it enforces nothing"
            where = str(index.dialect_options["postgresql"]["where"])
            # Without the is_current clause the index permits exactly ONE
            # verdict per release for all time, which deletes the history this
            # table exists to keep.
            assert "is_current" in where, f"{name} would constrain history rows"

    def test_the_two_indexes_split_on_phase_nullness(self):
        by_name = {i.name: i for i in ReleaseGateDecision.__table__.indexes}
        release_where = str(by_name["ix_rgd_release_current"].dialect_options["postgresql"]["where"])
        phase_where = str(by_name["ix_rgd_phase_current"].dialect_options["postgresql"]["where"])

        # Postgres treats NULLs as distinct, so a single index over
        # (release_id, phase_id) would not constrain the release-level rows at
        # all. The split is what makes the release-level slot real.
        assert "phase_id IS NULL" in release_where
        assert "phase_id IS NOT NULL" in phase_where

    def test_the_release_level_index_keys_on_release_alone(self):
        by_name = {i.name: i for i in ReleaseGateDecision.__table__.indexes}
        cols = [c.name for c in by_name["ix_rgd_release_current"].columns]

        # Including phase_id here would let a NULL phase slip past the unique
        # constraint for the reason above.
        assert cols == ["release_id"]

    def test_the_model_and_the_migration_name_the_same_indexes(self):
        import io
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "versions"
            / "0156_release_gate_decision.py"
        )
        source = io.open(migration, encoding="utf-8").read()
        # Only the UPGRADE half. Every name also appears in downgrade's
        # drop_index calls, so checking the whole file cannot tell an index
        # that is CREATED from one that is merely dropped — a migration that
        # dropped an index it never created would have passed. Mutation
        # testing found exactly that.
        sql = source.split("def downgrade")[0]
        assert "def upgrade" in sql

        # A model and a database that disagree about index names make the
        # inventory in either one a lie.
        for index in ReleaseGateDecision.__table__.indexes:
            # The QUOTED name WITH its trailing comma, exactly as the migration
            # writes it. A bare substring check passes when the migration names
            # `ix_rgd_phase_currentX` — mutation testing caught precisely that,
            # so the assertion could not see a renamed index.
            assert f'"{index.name}",' in sql, (
                f"{index.name} is declared on the model but the migration does "
                f"not create an index by that exact name"
            )


class TestTheVerdictVocabulary:
    def test_not_evaluated_is_a_real_verdict(self):
        # Below the evidence floor "we cannot say" is the honest answer.
        # Collapsing it into GO ("nothing failed") or NO_GO ("no proof") is a
        # confident lie in one direction or the other — absence is not health,
        # and it is not sickness either.
        assert "NOT_EVALUATED" in svc.VERDICTS

    def test_an_unknown_verdict_is_refused(self):
        # The column is String(20), so the database accepts anything. A value
        # outside the vocabulary would simply never match a status query again
        # — the silent-filter-failure class that made "quarantined" match
        # nothing forever.
        import asyncio

        class _Session:
            async def execute(self, *a, **kw):  # pragma: no cover - not reached
                raise AssertionError("validation must happen before any query")

        with pytest.raises(ValueError, match="unknown verdict"):
            asyncio.run(svc.record_decision(_Session(), uuid.uuid4(), "SHIP_IT"))


class TestTheFlushBetweenDemoteAndPromote:
    """The defect that has now appeared three times in this epic.

    Both rows are UPDATEs against the same partial unique index, and
    SQLAlchemy's unit of work orders persistent UPDATEs by PRIMARY KEY, not by
    assignment order. The ids are uuid4, so without a flush the promote is
    emitted before the demote roughly half the time and the index rejects it.

    It broke ``activate_release`` in S0 and ``unlink_test_run`` in the same
    review, both found by running the code rather than reading it. So this is
    asserted on the ORDER OF OPERATIONS, not on the presence of a call.
    """

    def test_the_demote_is_flushed_before_the_new_row_is_added(self):
        import asyncio

        events: list[str] = []
        previous = ReleaseGateDecision(
            id=uuid.uuid4(), release_id=uuid.uuid4(), verdict="GO", is_current=True
        )

        class _Result:
            def scalar_one_or_none(self):
                return previous

        class _Session:
            async def execute(self, *a, **kw):
                return _Result()

            async def flush(self):
                events.append("flush")

            def add(self, obj):
                events.append("add")

        asyncio.run(svc.record_decision(_Session(), previous.release_id, "NO_GO"))

        # The demotion must be flushed BEFORE the replacement is added, or the
        # two updates race for one index slot.
        assert events[0] == "flush", (
            f"expected a flush before the insert, got {events} — the demote and "
            f"the promote will contend for the same partial unique index"
        )
        assert "add" in events
        assert events.index("flush") < events.index("add")
        # And the previous row is demoted rather than deleted: the history is
        # the point.
        assert previous.is_current is False

    def test_no_flush_is_wasted_when_there_is_nothing_to_demote(self):
        import asyncio

        events: list[str] = []

        class _Result:
            def scalar_one_or_none(self):
                return None

        class _Session:
            async def execute(self, *a, **kw):
                return _Result()

            async def flush(self):
                events.append("flush")

            def add(self, obj):
                events.append("add")

        asyncio.run(svc.record_decision(_Session(), uuid.uuid4(), "GO"))

        # First verdict for a release: nothing to demote, so the only flush is
        # the one that persists the new row.
        assert events == ["add", "flush"]


class TestItNeverOwnsTheTransaction:
    def test_the_service_does_not_commit(self):
        src = inspect.getsource(svc)

        # Repo rule: the router owns the commit, so one unit of work covers the
        # demote and the promote together. A commit here would also mean a
        # failure could leave a release with two current verdicts or none.
        assert "db.commit()" not in src
        # And never a rollback on an injected session — that aborts the
        # caller's transaction and surfaces as an opaque downstream 500.
        assert "db.rollback()" not in src


class TestTheReleaseLevelLookupUsesIsNull:
    def test_the_release_level_query_matches_null_phase_with_is_null(self):
        src = inspect.getsource(svc.current_decision)

        # `phase_id == None` is never true in SQL, so the plain comparison
        # returns nothing and every caller reads "no verdict yet" for a release
        # that has one.
        assert "ReleaseGateDecision.phase_id.is_(None)" in src
