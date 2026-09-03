"""Regression: the denormalized primary release (migration 0152).

What this pins, and why it needs pinning
----------------------------------------
``test_runs.primary_release_id`` exists so a release filter is one indexed
predicate on a query shape that already exists, rather than a join to
``release_test_run_links`` on dozens of endpoints — a join that would also
double-count a run linked to two releases in any aggregate.

The cost of that is a value that can go stale. **Four live paths change which
link is primary** and every one must call ``sync_primary_release``:

  1. ``release_linker.link_run_to_release`` (ingest)
  2. ``release_service.link_test_run``       (Link Run modal)
  3. ``release_service.unlink_test_run``     (unlink)
  4. ``routers.runs`` set-release            (Run Detail control)

Miss one and nothing raises. Release-scoped analytics answer from the old
attribution while ``/runs`` — which reads the link table — answers correctly,
so two surfaces disagree with no error anywhere. The epic originally claimed
the linker was the sole writer of this column; it is not, and these tests
encode the four-writer reality so a fifth path cannot be added silently.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from app.models.postgres import TestRun
from app.services import release_linker, release_service


# ── Schema ───────────────────────────────────────────────────────────────────


def test_column_exists_and_does_not_cascade():
    """Deleting a release must not delete its runs.

    The sibling release FKs on this schema are CASCADE, so an unspecified
    ondelete would inherit exactly the wrong behaviour and a release deletion
    would take the test history with it.
    """
    col = TestRun.__table__.c.primary_release_id
    assert col.nullable is True, (
        "NULL is a real state — an in-flight live run has no link yet, and a "
        "swallowed linker error leaves one permanently unattributed"
    )
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"


def test_index_column_order_serves_the_query_shape():
    """(project_id, primary_release_id, created_at), in that order.

    Every release-scoped read is tenant-scoped first — project_id is never
    absent — then narrowed to a release, then bounded by the window. A
    release-first order would not serve the far commoner project+window read
    that carries no release filter, which is most of the 36 windowed endpoints.
    """
    index = next(
        (i for i in TestRun.__table__.indexes
         if i.name == "ix_test_runs_project_release_created"),
        None,
    )
    assert index is not None
    assert [c.name for c in index.columns] == [
        "project_id", "primary_release_id", "created_at",
    ]


# ── The four writers ─────────────────────────────────────────────────────────


def test_every_path_that_changes_the_primary_link_syncs_the_column():
    """The four-writer contract, asserted against source rather than trusted.

    A fifth path that mutates ``is_primary`` without syncing would introduce
    drift that only the sweep would catch, and only after the fact. This test
    is what makes adding one fail loudly at review time.
    """
    from app.routers import runs as runs_router

    writers = {
        "release_linker.link_run_to_release": inspect.getsource(
            release_linker.link_run_to_release
        ),
        "release_service.link_test_run": inspect.getsource(
            release_service.link_test_run
        ),
        "release_service.unlink_test_run": inspect.getsource(
            release_service.unlink_test_run
        ),
    }
    for name, src in writers.items():
        assert "sync_primary_release" in src, (
            f"{name} changes which link is primary but never syncs "
            f"test_runs.primary_release_id — release-scoped analytics would "
            f"answer from a stale attribution"
        )

    # Scope to the ENDPOINT, not the module: asserting on the whole file is
    # satisfied by the local `from ... import sync_primary_release` line alone,
    # so deleting the call would keep this green.
    endpoint_src = inspect.getsource(runs_router.set_run_release)
    assert endpoint_src.count("is_primary") >= 2, "expected the demote/promote pair"
    assert "await sync_primary_release(" in endpoint_src, (
        "the Run Detail control changes the primary link and must sync"
    )


def test_no_other_module_mutates_is_primary():
    """Sweep the service layer for a fifth writer.

    ``is_primary`` is only meaningful if every mutation is paired with a sync.
    Rather than trust the four we know about, assert that no other module
    assigns it at all.
    """
    import pathlib
    import re

    # Sweep ALL of app/, not just services/. The original version globbed
    # app/services/*.py — which cannot see routers/runs.py, one of the four
    # writers this very file names. It was structurally incapable of finding
    # the thing it was written to find.
    allowed = {"release_linker.py", "release_service.py", "runs.py"}
    app_root = pathlib.Path(release_service.__file__).parent.parent
    # Catch both forms: the Core `.values(is_primary=...)` and the ORM
    # attribute assignment `link.is_primary = False`.
    pattern = re.compile(r"is_primary\s*=(?!=)")
    offenders = []
    for path in app_root.rglob("*.py"):
        if "__pycache__" in path.parts or path.name in allowed:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(app_root)))
    assert not offenders, (
        f"these modules set is_primary without syncing primary_release_id: {offenders}"
    )


# ── Sync semantics ───────────────────────────────────────────────────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    def __init__(self, primary):
        self._primary = primary
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._primary)


@pytest.mark.asyncio
async def test_sync_writes_the_primary_release():
    release_id = uuid.uuid4()
    db = _Session(release_id)

    written = await release_linker.sync_primary_release(db, uuid.uuid4())

    assert written == release_id
    assert len(db.statements) == 2, "one SELECT for the primary link, one UPDATE"
    assert "UPDATE test_runs" in str(db.statements[1])


@pytest.mark.asyncio
async def test_sync_writes_null_when_the_run_has_no_primary_link():
    """The clearing case, and the one most likely to be skipped.

    After unlinking a run's last link the column must go NULL. An
    implementation that only wrote non-NULL values would leave the run
    attributed to a release it is no longer linked to — invisible, because the
    run still shows a release everywhere.
    """
    db = _Session(None)

    written = await release_linker.sync_primary_release(db, uuid.uuid4())

    assert written is None
    assert len(db.statements) == 2, "the UPDATE must still run, writing NULL"
    assert "UPDATE test_runs" in str(db.statements[1])
    # Pin the VALUE, not just that a statement happened: an implementation that
    # wrote the run's old release back would satisfy a count assertion.
    assert db.statements[1].compile().params["primary_release_id"] is None


@pytest.mark.asyncio
async def test_secondary_link_does_not_repoint_the_column():
    """Adding a cherry-pick membership must not move analytics.

    ``link_run_to_release`` syncs only when the new link IS the primary one.
    Syncing unconditionally would make the most recently added release win,
    which is exactly the non-determinism ``is_primary`` was introduced to end.
    """
    src = inspect.getsource(release_linker.link_run_to_release)
    sync_at = src.index("sync_primary_release")
    guard = src[:sync_at]
    assert "if not has_primary" in guard, (
        "the sync must be guarded on this link becoming primary"
    )


# ── Drift detection ──────────────────────────────────────────────────────────


def test_drift_query_uses_is_distinct_from():
    """``!=`` would skip the rows most likely to be wrong.

    NULL is legitimate on both sides — a run with no link, or a column not yet
    backfilled — and ``NULL != x`` is NULL, not true, so a plain inequality
    silently excludes every run whose column is NULL while its link is not.
    That is precisely the drift a missed sync call produces.
    """
    src = inspect.getsource(release_linker.find_primary_release_drift)
    assert "is_distinct_from" in src
    assert "!=" not in src.split("def ")[1].split('"""')[2]


def test_detection_is_separate_from_repair():
    """A sweep that repairs as it finds cannot report what it found.

    Keeping them apart is what lets the task count drift before fixing it —
    otherwise the invariant looks healthy precisely because something keeps
    repairing it, and the missed call site is never seen.
    """
    detect = inspect.getsource(release_linker.find_primary_release_drift)
    assert "update(" not in detect, "the detector must not write"
    assert "Detection only" in detect


# ── Migration ────────────────────────────────────────────────────────────────


def _migration_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "migrations" / "versions" / "0152_test_runs_primary_release.py"
    ).read_text(encoding="utf-8")


def _index_migration_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "migrations" / "versions" / "0153_release_indexes_and_backfill.py"
    ).read_text(encoding="utf-8")


def test_backfill_is_batched_and_self_terminating():
    """A single UPDATE over test_runs holds row locks across the whole table.

    The loop must also be self-terminating: each pass selects only rows whose
    value still differs, so a row fixed by one pass cannot be selected by the
    next. Without that predicate the loop never drains and the ceiling is the
    only thing stopping it.
    """
    src = _index_migration_source()
    assert "LIMIT :batch" in src
    assert "IS DISTINCT FROM" in src, (
        "the batch predicate is what makes the loop terminate"
    )
    assert "_MAX_BATCHES" in src, "an unbounded loop can hang a migration"


def test_backfill_batches_actually_commit():
    """Batching inside a migration transaction buys nothing.

    Alembic wraps the whole upgrade, so every row lock from every batch is held
    until the migration commits — the exact lock profile batching exists to
    avoid. The loop must be inside an autocommit block for the claim to be true.
    """
    src = _index_migration_source()
    loop_at = src.index("for _ in range(_MAX_BATCHES)")
    preceding = src[:loop_at]
    assert "autocommit_block" in preceding[preceding.rindex("def upgrade") :], (
        "the backfill loop must run inside autocommit_block or its batching "
        "is decorative"
    )


def test_migration_reports_when_it_does_not_drain():
    """Hitting the batch ceiling must be loud.

    A half-populated column looks identical to a complete one — every read
    still returns rows, just fewer. Absence is not health.
    """
    src = _index_migration_source()
    assert "logger.warning" in src
    assert "ceiling" in src


def test_index_is_built_concurrently():
    """Assert against the UPGRADE half only, and count.

    ``downgrade()`` also drops indexes CONCURRENTLY, so a substring check over
    the whole file passes even when every upgrade-side build has lost its flag
    — and 0152's version passed on its module docstring alone. Counting builds
    against concurrent builds fails the moment one loses it.
    """
    src = _index_migration_source()
    up = src[: src.index("def downgrade()")]

    # 0153 builds its indexes from a table, so there is one create_index call
    # for all of them. Assert on the table's contents, and that the single call
    # site carries both flags — that is where a regression would land.
    assert up.count("op.create_index(") == 1, (
        "0153 builds indexes from _INDEXES in a loop; if that changed, this "
        "test's shape must change with it"
    )
    assert "postgresql_concurrently=True" in up
    assert "if_not_exists=True" in up, (
        "a re-run must be a no-op — this migration commits mid-way, so a "
        "failure after the first index leaves it half-applied"
    )

    # Every index the release slices need must be in the table, or it is
    # simply never created — the models declare them but models do not build
    # indexes.
    import re

    declared = set(re.findall(r'"(ix_[a-z_]+)"', up))
    for required in (
        "ix_releases_project_active",
        "ix_rtr_links_test_run",
        "ix_rtr_links_primary",
        "ix_releases_project_sort",
        "ix_test_runs_project_release_created",
    ):
        assert required in declared, f"{required} is declared on a model but never created"
