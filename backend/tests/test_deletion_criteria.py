"""S5 — the criteria model and the resolver that freezes a candidate set.

The nightly purge's candidate set is a pure function of ``(policy, now)``, so
re-running its resolver gives the same answer. Criteria are not: they read
columns that other code rewrites while the job is queued. Everything here
exists because of that difference.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from app.services import deletion_criteria as dc  # noqa: E402


def _sql(predicates):
    """Render predicates as one AND-ed WHERE clause, with literals inlined."""
    from sqlalchemy import and_, select
    from sqlalchemy.dialects import postgresql

    from app.models.postgres import TestRun

    return str(
        select(TestRun.id)
        .where(and_(*predicates))
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


# ── what the model refuses ───────────────────────────────────────────────────


def test_a_project_id_alone_is_refused():
    """"Delete by criteria" with no criteria is a full project purge.

    Accepting it would put that behind a form that looks like a filter, which
    is how an operator deletes a project believing they narrowed it.
    """
    with pytest.raises(ValueError, match="at least one criterion"):
        dc.RetentionCriteria()


def test_an_unknown_status_is_refused_rather_than_matching_nothing():
    """A literal outside the column's vocabulary matches nothing, forever.

    The delete would report success having found zero runs, and the operator
    would conclude there was nothing to delete.
    """
    with pytest.raises(ValueError, match="unknown status"):
        dc.RetentionCriteria(statuses=["passed"])  # lowercase; column is upper


def test_every_launch_status_is_accepted():
    """The other half: a vocabulary check that rejects valid values would make
    the most-indexed filter on the table unusable."""
    from app.models.postgres import LaunchStatus

    for member in LaunchStatus:
        assert dc.RetentionCriteria(statuses=[member.value])


def test_an_inverted_date_range_is_refused():
    """``date_from > date_to`` selects nothing. Silently returning an empty
    set reads as "no runs matched" rather than "your range is backwards"."""
    with pytest.raises(ValueError, match="date_from must not be after"):
        dc.RetentionCriteria(
            date_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_more_than_500_run_ids_is_refused():
    """An unbounded set is not something an operator can review before
    authorising, and this endpoint's whole safety story is the review."""
    with pytest.raises(Exception):
        dc.RetentionCriteria(run_ids=[uuid.uuid4() for _ in range(501)])

    assert dc.RetentionCriteria(run_ids=[uuid.uuid4() for _ in range(500)])


def test_an_unknown_field_is_refused():
    """``extra="forbid"``. A typo'd criterion that is silently ignored widens
    the deletion to everything the caller *thought* they had narrowed."""
    with pytest.raises(Exception):
        dc.RetentionCriteria(status=["PASSED"])  # singular; real field is plural


# ── AND across fields, OR within a list ──────────────────────────────────────


def test_two_fields_are_combined_with_and_not_or():
    """The acceptance criterion, asserted on a case where the readings differ.

    ``statuses=[FAILED] , branches=[main]`` under AND means *failed AND on
    main*; under OR it means *failed OR on main*, which would delete every run
    on main regardless of status. A test using a single field cannot tell them
    apart.
    """
    project_id = uuid.uuid4()
    criteria = dc.RetentionCriteria(statuses=["FAILED"], branches=["main"])

    sql = _sql(dc.build_predicates(criteria, project_id=project_id))

    where = sql.split("WHERE", 1)[1]
    assert " AND " in where
    # The disjunction must not span the two fields.
    assert "status" in where and "branch" in where
    assert where.count(" OR ") == 0, (
        "the two fields were combined with OR — every run on main would be "
        "deleted regardless of status"
    )


def test_a_list_within_one_field_is_a_disjunction():
    """``statuses=[FAILED, STOPPED]`` must match either, not both at once —
    which is impossible, so an AND here would match nothing."""
    criteria = dc.RetentionCriteria(statuses=["FAILED", "STOPPED"])

    where = _sql(dc.build_predicates(criteria, project_id=uuid.uuid4())).split("WHERE", 1)[1]

    assert "IN (" in where.upper()
    assert "'FAILED'" in where and "'STOPPED'" in where


def test_the_project_scope_is_always_present():
    """Every criteria query is tenant-scoped whether or not the caller said so."""
    project_id = uuid.uuid4()

    where = _sql(
        dc.build_predicates(dc.RetentionCriteria(statuses=["PASSED"]), project_id=project_id)
    ).split("WHERE", 1)[1]

    assert str(project_id) in where


# ── the date aliases ─────────────────────────────────────────────────────────


def test_older_than_days_is_an_alias_for_date_to():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    criteria = dc.RetentionCriteria(older_than_days=30)

    assert criteria.effective_date_to(now=now) == now - timedelta(days=30)


def test_the_earlier_of_the_two_ceilings_wins():
    """Both are ceilings on how new a run may be.

    Honouring only one would delete runs the other excluded — and which one
    won would depend on argument order, which no operator can predict.
    """
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    explicit = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # older_than_days=30 -> 2026-05-02, which is LATER than the explicit date.
    criteria = dc.RetentionCriteria(date_to=explicit, older_than_days=30)

    assert criteria.effective_date_to(now=now) == explicit

    # And the other way round.
    criteria = dc.RetentionCriteria(
        date_to=datetime(2026, 5, 30, tzinfo=timezone.utc), older_than_days=300
    )
    assert criteria.effective_date_to(now=now) == now - timedelta(days=300)


def test_no_date_bound_yields_none_not_now():
    """Defaulting to ``now`` would silently make every criteria set an
    "everything older than this instant" purge."""
    assert dc.RetentionCriteria(statuses=["PASSED"]).effective_date_to() is None


# ── suite matching ───────────────────────────────────────────────────────────


def test_suite_match_only_uses_the_indexed_run_column():
    """The conservative default: a multi-suite run is not deleted because one
    of its suites was named."""
    criteria = dc.RetentionCriteria(suite_names=["Auth"], suite_match="only")

    where = _sql(dc.build_predicates(criteria, project_id=uuid.uuid4())).split("WHERE", 1)[1]

    assert "primary_suite_name" in where
    assert "EXISTS" not in where.upper(), (
        "the default must not reach into test_cases — that is the destructive "
        "reading and is opt-in"
    )


def test_suite_match_any_considers_both_suite_columns():
    """Suite membership lives on BOTH columns.

    Old live-stream runs carry it only at run level; file uploads carry it only
    per test case. A filter reading either alone misses runs and under-deletes
    silently — the house rule the effective-suite helpers exist for.
    """
    criteria = dc.RetentionCriteria(suite_names=["Auth"], suite_match="any")

    where = _sql(dc.build_predicates(criteria, project_id=uuid.uuid4())).split("WHERE", 1)[1]

    assert "primary_suite_name" in where
    assert "EXISTS" in where.upper() and "test_cases" in where
    assert " OR " in where, "the two suite columns must be a disjunction"


def test_the_default_suite_match_is_the_conservative_one():
    assert dc.RetentionCriteria(suite_names=["Auth"]).suite_match == "only"


# ── the resolver ─────────────────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return type("S", (), {"all": staticmethod(lambda: list(self._rows))})()


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, statement, *_a, **_kw):
        self.statements.append(statement)
        return _Rows(self._rows)


@pytest.mark.asyncio
async def test_the_resolver_returns_run_ids_and_nothing_else():
    """Run-scoped BY CONSTRUCTION.

    The audit logs, provenance records, expired memory and compliance packs
    belong to the nightly purge's other three clocks. A criteria path that
    returned any of them would let ``older_than_days`` delete a project's
    audit trail.
    """
    ids = [uuid.uuid4() for _ in range(3)]
    db = _DB(ids)

    out = await dc.resolve_criteria_candidates(
        db, project_id=uuid.uuid4(), criteria=dc.RetentionCriteria(statuses=["FAILED"])
    )

    assert out == ids
    assert all(isinstance(i, uuid.UUID) for i in out)


@pytest.mark.asyncio
async def test_the_resolver_is_bounded():
    """A set too large to review is a set too large to authorise."""
    db = _DB([uuid.uuid4()])

    await dc.resolve_criteria_candidates(
        db, project_id=uuid.uuid4(), criteria=dc.RetentionCriteria(statuses=["FAILED"])
    )

    compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert f"LIMIT {dc.MAX_CANDIDATES + 1}" in compiled, (
        "unbounded resolve — and the +1 is what lets the caller SEE the "
        "truncation rather than silently deleting the first N"
    )


@pytest.mark.asyncio
async def test_foreign_run_ids_are_reported():
    """Ids from another project must fail the whole request.

    Filtering them out and deleting the rest tells the caller nothing, and
    quietly acts on a request they got wrong.
    """
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    db = _DB([mine])  # only `mine` comes back as owned

    foreign = await dc.foreign_run_ids(
        db, project_id=uuid.uuid4(), run_ids=[mine, theirs]
    )

    assert foreign == [theirs]


@pytest.mark.asyncio
async def test_no_foreign_ids_means_an_empty_list_not_a_falsy_surprise():
    mine = uuid.uuid4()
    db = _DB([mine])

    assert await dc.foreign_run_ids(db, project_id=uuid.uuid4(), run_ids=[mine]) == []
    assert await dc.foreign_run_ids(db, project_id=uuid.uuid4(), run_ids=[]) == []
