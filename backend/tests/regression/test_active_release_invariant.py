"""Regression: the active-release invariant and link provenance (migration 0150).

What these pin down
-------------------
S0 replaces a permanent per-project ``is_default`` bucket with a *rotating*
active release. The value of that change rests on three properties, and each
one has a way of quietly not holding:

  1. **Every project has exactly one active release, always.** "At most one" is
     the database's job (partial unique index). "At least one" is code's job,
     and it is the half that can silently regress — a new deletion path, a
     status transition nobody thought about, and a project is left with no
     release to attribute runs to.

  2. **The fallback resolves as of EXECUTION time, not ingest time.** This is
     the entire justification for preferring a rotating pointer to the old
     bucket. If it reads the current flag instead, an archive uploaded after a
     rotation lands in the wrong release and nothing ever re-attributes it —
     and the bug is invisible because the run still shows *a* release.

  3. **Every link records how it was decided.** Without ``link_source`` the
     rotating release is a *worse* disguise than the bucket it replaced: the
     old junk drawer at least self-labelled as ``Default Release (…)``, whereas
     an untagged nightly now lands under a real version number. A verdict built
     on inferred membership must be able to say so.

These use fakes rather than a live database on purpose: the suite has no
Postgres by default, and the properties above are about *control flow* — which
branch runs, what value it writes — not about SQL semantics. The one property
that genuinely needs Postgres (that the partial unique index rejects a second
active release) is asserted structurally here and belongs in an integration
test when one exists.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.postgres import (
    ASSERTED_LINK_SOURCES,
    LinkSource,
    Release,
    ReleaseTestRunLink,
)
from app.services import release_lifecycle_service as lifecycle


def _where_clause(stmt) -> str:
    """The WHERE clause alone — no SELECT list, no ORDER BY, no LIMIT.

    Splitting on "WHERE" is not enough on its own: everything AFTER the
    predicate comes along too, so an assertion on a column name can be
    satisfied by the ORDER BY tail while the predicate that actually matters
    has been deleted. Both traps have bitten these tests already.
    """
    import re

    after_where = str(stmt).split("WHERE", 1)[1]
    return re.split(r"ORDER BY|LIMIT|GROUP BY", after_where)[0]


# ── Structural guarantees ────────────────────────────────────────────────────


def test_at_most_one_active_release_is_enforced_by_the_database():
    """The 'at most one' half must be a constraint, not a convention.

    Rotation deactivates the incumbent and activates a successor in one
    transaction. If that were only enforced in Python, a concurrent activation
    would produce two active releases and the attribution fallback would pick
    one arbitrarily — a bug that surfaces as a handful of runs in the wrong
    release, with nothing in the logs.
    """
    index = next(
        (i for i in Release.__table__.indexes if i.name == "ix_releases_project_active"),
        None,
    )
    assert index is not None, "ix_releases_project_active is missing from the model"
    assert index.unique is True
    assert [c.name for c in index.columns] == ["project_id"]

    where = index.dialect_options["postgresql"]["where"]
    assert "is_active" in str(where), (
        "the index must be PARTIAL on is_active — a plain unique index on "
        "project_id would allow only one release per project, full stop"
    )


def test_is_default_is_retained_so_the_migration_can_be_rolled_back():
    """``is_default`` is superseded, not dropped.

    0150 flips every default release to active. If the column were dropped in
    the same migration, a downgrade would have no way to know which release had
    been the fallback, and the rollback would silently change behaviour rather
    than restoring it.
    """
    assert "is_default" in Release.__table__.c
    assert "is_active" in Release.__table__.c


def test_link_source_vocabulary_separates_assertion_from_inference():
    """Only a client naming the release, or a human choosing it, is an assertion.

    This split is what the release scorecard reports. If ``active_release`` ever
    drifted into the asserted set, a release whose entire membership was
    inferred would present as fully attributed — exactly the failure the
    provenance column exists to prevent.
    """
    assert ASSERTED_LINK_SOURCES == {
        LinkSource.EXPLICIT_CLIENT.value,
        LinkSource.MANUAL_UI.value,
        # Added in S3b. An external match is read from GitHub, which maintains
        # the milestone-to-PR link itself — so it is a system of record, not a
        # pattern or a date range this codebase guessed with. A release whose
        # evidence is entirely external_match IS fully attributed, and a
        # scorecard that called it inferred would understate its own evidence.
        LinkSource.EXTERNAL_MATCH.value,
    }
    for inferred in (
        LinkSource.ACTIVE_RELEASE,
        LinkSource.CUTOFF_WINDOW,
        LinkSource.RULE_MATCH,
        LinkSource.DEFAULT_FALLBACK,
        LinkSource.UNKNOWN,
    ):
        assert inferred.value not in ASSERTED_LINK_SOURCES


def test_link_row_carries_provenance():
    assert "link_source" in ReleaseTestRunLink.__table__.c


# ── As-of resolution ─────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RecordingSession:
    """Captures the statements executed so the WHERE clause can be inspected."""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []
        self.flushes = 0
        self.adds = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._results.pop(0) if self._results else None)

    async def flush(self):
        self.flushes += 1

    def add(self, _obj):
        self.adds += 1

    def begin_nested(self):
        from unittest.mock import AsyncMock

        sp = AsyncMock()
        sp.__aenter__ = AsyncMock(return_value=sp)
        sp.__aexit__ = AsyncMock(return_value=False)
        return sp


def _release(**kw):
    r = Release(
        project_id=kw.get("project_id", uuid.uuid4()),
        name=kw.get("name", "2.4.0"),
    )
    for k, v in kw.items():
        setattr(r, k, v)
    return r


@pytest.mark.asyncio
async def test_resolve_at_none_reads_the_flag_directly():
    """The live path should not pay for an interval scan."""
    project_id = uuid.uuid4()
    current = _release(project_id=project_id, name="current")
    db = _RecordingSession([current])

    got = await lifecycle.resolve_active_release_at(db, project_id, None)

    assert got is current
    assert len(db.statements) == 1
    # WHERE clause only. ``is_active`` is a mapped column of Release, so it
    # appears in the SELECT list of every ``select(Release)`` — asserting on
    # the full statement would pass even if the filter were deleted entirely.
    where = _where_clause(db.statements[0])
    assert "is_active" in where
    assert "activated_at" not in where, (
        "the now-path must short-circuit to the flag, not pay for an "
        "interval scan"
    )


@pytest.mark.asyncio
async def test_resolve_at_a_past_moment_queries_the_activation_interval():
    """The whole point of S0: attribute by WHEN THE RUN RAN.

    An archive uploaded after a rotation must land in the release that was
    underway while the tests executed. If this ever regresses to reading
    ``is_active``, the run still gets *a* release — so the only way to catch it
    is to assert the query shape.
    """
    project_id = uuid.uuid4()
    past = datetime(2026, 6, 1, tzinfo=timezone.utc)
    older = _release(project_id=project_id, name="2.3.0")
    db = _RecordingSession([older])

    got = await lifecycle.resolve_active_release_at(db, project_id, past)

    assert got is older
    # Predicate only. Splitting on WHERE alone leaves
    # ``ORDER BY releases.activated_at DESC`` in the region, which satisfies an
    # "activated_at" assertion by itself — so deleting both activated_at
    # PREDICATES would keep this test green while the query silently regressed
    # to "whatever is current".
    where = _where_clause(db.statements[0])
    assert "activated_at" in where, "must resolve against the activation interval"
    assert "<=" in where, "activated_at must be COMPARED to `at`, not just ordered by"
    assert "deactivated_at" in where
    assert "is_active" not in where, (
        "reading the current flag for a past moment is the exact bug this "
        "function exists to avoid"
    )


@pytest.mark.asyncio
async def test_resolve_falls_back_to_current_when_the_run_predates_all_activations():
    """Runs older than any recorded activation are not left unattributed.

    Right after 0150 ships, a migrated release has ``activated_at = created_at``
    while the runs beneath it are older. Falling back to the current active
    release gives those the same answer the pre-S0 bucket gave, so the
    migration never makes an existing attribution worse.
    """
    project_id = uuid.uuid4()
    current = _release(project_id=project_id, name="current")
    # First query (interval) misses, second (flag) hits.
    db = _RecordingSession([None, current])

    got = await lifecycle.resolve_active_release_at(
        db, project_id, datetime(2020, 1, 1, tzinfo=timezone.utc)
    )

    assert got is current
    assert len(db.statements) == 2


# ── Rotation ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_closes_the_previous_interval_and_opens_the_new_one():
    """Both writes in one transaction — otherwise there is a committed moment
    with zero active releases, and any ingest landing in it has nowhere to go.
    """
    project_id = uuid.uuid4()
    incumbent = _release(
        project_id=project_id,
        name="2.3.0",
        is_active=True,
        activated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    successor = _release(project_id=project_id, name="2.4.0", status="planning")
    db = _RecordingSession([incumbent])

    previous = await lifecycle.activate_release(db, successor, reason="test")

    assert previous is incumbent
    assert incumbent.is_active is False
    assert incumbent.deactivated_at is not None, (
        "an un-closed interval makes the as-of lookup ambiguous forever"
    )
    assert successor.is_active is True
    assert successor.activated_at is not None
    assert successor.deactivated_at is None
    assert successor.status == "in_progress"
    # The demotion MUST reach the database before the promotion is staged.
    # SQLAlchemy orders persistent UPDATEs by primary key, not by assignment
    # order, and Release.id is a random uuid4 — so without an intervening
    # flush, roughly half of all activations emit the promote first and
    # ix_releases_project_active rejects it. Verified empirically against
    # SQLAlchemy 2.0.36 during the S0-S2 review.
    assert db.flushes >= 1, (
        "activate_release must flush the demotion before claiming the slot"
    )


@pytest.mark.asyncio
async def test_activating_the_incumbent_is_a_no_op():
    """Re-activating the current release must not close its own interval."""
    project_id = uuid.uuid4()
    current = _release(
        project_id=project_id,
        name="2.4.0",
        is_active=True,
        activated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db = _RecordingSession([current])

    previous = await lifecycle.activate_release(db, current, reason="test")

    assert previous is None
    assert current.is_active is True
    assert current.deactivated_at is None


@pytest.mark.asyncio
async def test_successor_pool_excludes_auto_named_releases():
    """Ingest auto-creates a ``planning`` release for every unrecognised
    ``release_name`` a client sends, so the planning pool is NOT a curated
    queue. Promoting from it unfiltered would activate whatever string some CI
    job happened to pass.
    """
    project_id = uuid.uuid4()
    db = _RecordingSession([None])

    await lifecycle._next_successor(db, project_id, uuid.uuid4())

    # literal_binds so the bound status value is visible — otherwise the
    # rendered SQL only shows ``:status_1`` and the assertion proves nothing.
    #
    # WHERE clause only, for the same reason: every Release column appears in
    # the SELECT list, so `"is_auto_named" in sql` stays true even after the
    # filter is deleted. Mutation-testing this assertion is what caught it.
    where = str(
        db.statements[0].compile(compile_kwargs={"literal_binds": True})
    ).split("WHERE", 1)[1]
    assert "is_auto_named" in where, (
        "the successor query must filter out auto-created placeholders"
    )
    assert "'planning'" in where


@pytest.mark.asyncio
async def test_rotation_promotes_a_human_created_planning_release():
    """The whole point of rotation: shipping never leaves a project without an
    active release, and it prefers something a person actually planned.
    """
    project_id = uuid.uuid4()
    closing = _release(
        project_id=project_id, name="2.4.0", is_active=True, status="released"
    )
    closing.id = uuid.uuid4()
    planned = _release(project_id=project_id, name="2.5.0", status="planning")
    planned.id = uuid.uuid4()
    # _next_successor, then _current_active inside activate_release.
    db = _RecordingSession([planned, closing])

    successor = await lifecycle.rotate_on_close(db, closing, reason="released")

    assert successor is planned
    assert planned.is_active is True
    assert closing.is_active is False


@pytest.mark.asyncio
async def test_rotation_reuses_a_placeholder_rather_than_minting_a_duplicate():
    """``uq_releases_project_lower_name`` allows exactly ONE "Unreleased" per
    project, ever — including a finished one.

    Minting a second unconditionally made every rotation raise: mark a release
    shipped on a project whose only other release is the original placeholder,
    and the INSERT collided, the handler re-raised, and the endpoint 500'd
    without shipping anything.
    """
    project_id = uuid.uuid4()
    closing = _release(project_id=project_id, name="2.4.0", is_active=True)
    closing.id = uuid.uuid4()
    placeholder = _release(
        project_id=project_id, name=lifecycle.AUTO_RELEASE_NAME, status="planning"
    )
    placeholder.id = uuid.uuid4()
    placeholder.is_auto_named = True
    # no curated successor -> reusable placeholder -> _current_active
    db = _RecordingSession([None, placeholder, closing])

    successor = await lifecycle.rotate_on_close(db, closing, reason="released")

    assert successor is placeholder, "must reuse, not INSERT a second Unreleased"
    assert db.adds == 0, "no new release row may be inserted when one is reusable"


# ── Naming ───────────────────────────────────────────────────────────────────


def test_auto_release_name_is_not_a_guessed_version():
    """Guessing 2.5.0 after 2.4.0 is wrong whenever the next release is 2.4.1,
    and a wrong version number looks authoritative — runs pile up under a
    version that will never exist. A placeholder is honest about being one.
    """
    assert lifecycle.AUTO_RELEASE_NAME == "Unreleased"
    assert not any(ch.isdigit() for ch in lifecycle.AUTO_RELEASE_NAME)


def test_terminal_statuses_trigger_rotation():
    """Every status that means "finished" must hand the flag on. A status
    missing from this set keeps a shipped release collecting new runs.
    """
    assert set(lifecycle.TERMINAL_STATUSES) == {"released", "cancelled", "archived"}


# ── Migration ────────────────────────────────────────────────────────────────


def _migration_source() -> str:
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0150_active_release_and_link_provenance.py"
    )
    return path.read_text(encoding="utf-8")


def test_migration_stamps_default_bucket_links_before_flipping_the_flag():
    """Order matters, and it is easy to get wrong.

    The backfill answers "which links belong to the old bucket?" from
    ``is_default``. If the flag flip ran first it would still work today — but
    the two statements are only decoupled by luck, and a later edit that made
    the link backfill read ``is_active`` would silently stamp every link on a
    newly-active release as ``default_fallback``. Pin the order.
    """
    src = _migration_source()
    link_backfill = src.index("SET link_source = 'default_fallback'")
    flag_flip = src.index("SET is_active = TRUE")
    assert link_backfill < flag_flip


def test_migration_marks_migrated_releases_as_auto_named():
    """A migrated default release was never named by a human.

    Without this flag the "name your release" prompt and the cross-project
    count of unconfigured teams both skip precisely the projects that need
    them most — the ones that never set releases up at all.
    """
    src = _migration_source()
    flip = src[src.index("SET is_active = TRUE") :]
    assert "is_auto_named = TRUE" in flip[:400]


def test_migration_distinguishes_unknown_from_default_fallback():
    """A pre-0150 link to a real release has no recoverable provenance.

    Stamping it ``default_fallback`` would claim it came from the bucket, which
    is a specific and false statement. ``unknown`` says what is actually known.
    """
    src = _migration_source()
    assert "SET link_source = 'unknown'" in src
    assert "'default_fallback'" in src


def test_migration_does_not_drop_is_default():
    src = _migration_source()
    assert 'drop_column("releases", "is_default")' not in src


def test_0150_stays_fully_transactional():
    """0150 adds columns and backfills; index builds live in 0153.

    They cannot share a migration. ``CREATE INDEX CONCURRENTLY`` must run
    outside a transaction, so Alembic commits everything before it — and a
    failure at the build then leaves committed columns behind a migration that
    cannot be re-run, because ``add_column`` is not idempotent.
    """
    src = _migration_source()
    up = src[: src.index("def downgrade()")]
    assert "op.create_index(" not in up
    assert "autocommit_block" not in up
