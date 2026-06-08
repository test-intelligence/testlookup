"""Regression: ``DELETE /api/v1/releases/{id}`` must delete a release together
with its phases and run-links, **without** deleting the underlying TestRuns.

Why this is pinned
------------------
``release_service.delete_release`` is a bare ``await db.delete(release)``. That
only succeeds (and only does the right thing) because of how the ORM
relationships and FKs are configured on the model:

  * ``Release.phases``         → ``cascade="all, delete-orphan"`` + FK
    ``release_phases.release_id ON DELETE CASCADE``
  * ``Release.test_run_links`` → ``cascade="all, delete-orphan"`` + FK
    ``release_test_run_links.release_id ON DELETE CASCADE``

If a future refactor drops the ``delete-orphan`` cascade, deleting a release
that has any phase or linked run would raise an ``IntegrityError`` (FK
violation) at commit and surface as a 500 — exactly the failure mode the
``/releases`` delete button would hit in production once a release has data.

Just as important: deleting a release must **not** cascade into the actual
``TestRun`` rows — only the link rows. The link table owns the FK to the run
(``test_run_id ON DELETE CASCADE`` points *from* link *to* run), and ``Release``
has no relationship targeting ``TestRun``, so runs survive a release deletion.

This test introspects the mapper/table config (no DB connection needed) so it
runs in the same fast unit lane as the rest of the regression suite.
"""
from __future__ import annotations

import os

# Lazy asyncpg URL: app.db.postgres builds a lazy engine at import; it never
# connects here. Mirrors the env the rest of the suite uses in CI.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/test")

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import inspect as sa_inspect  # noqa: E402

from app.models.postgres import (  # noqa: E402
    Release,
    ReleasePhase,
    ReleaseTestRunLink,
)
from app.models.postgres import TestRun as RunModel  # noqa: E402  (avoid Test* collection)


def _rel(model, name):
    return sa_inspect(model).relationships[name]


def test_release_phases_relationship_has_delete_orphan_cascade():
    """A release with phases must be deletable in one ``db.delete(release)``."""
    casc = _rel(Release, "phases").cascade
    assert casc.delete, "Release.phases must cascade delete"
    assert casc.delete_orphan, (
        "Release.phases must be delete-orphan — otherwise deleting a release "
        "with phases raises an FK IntegrityError (500 on the /releases delete)"
    )


def test_release_test_run_links_relationship_has_delete_orphan_cascade():
    """A release with linked runs must be deletable in one ``db.delete``."""
    casc = _rel(Release, "test_run_links").cascade
    assert casc.delete, "Release.test_run_links must cascade delete"
    assert casc.delete_orphan, (
        "Release.test_run_links must be delete-orphan — otherwise deleting a "
        "release that has any linked run raises an FK IntegrityError (500)"
    )


def test_deleting_a_release_does_not_delete_the_underlying_test_runs():
    """Release deletion drops links, never the TestRuns themselves.

    Guard 1: ``Release`` exposes no relationship that targets ``TestRun`` (so
    the ORM cascade can't reach a run).
    Guard 2: the link's ``test_run_id`` FK points link→run with ON DELETE
    CASCADE, which only deletes the *link* when a *run* is removed — not the
    reverse.
    """
    release_targets = {
        r.mapper.class_ for r in sa_inspect(Release).relationships
    }
    assert RunModel not in release_targets, (
        "Release must not hold a relationship to TestRun, or deleting a "
        "release could cascade-delete shared run history"
    )

    fk = next(
        fk for fk in ReleaseTestRunLink.__table__.c.test_run_id.foreign_keys
    )
    assert fk.column.table.name == "test_runs"
    assert fk.ondelete == "CASCADE"  # run-removal clears the link, not vice versa


def test_link_release_fk_is_ondelete_cascade():
    """DB-level backstop for the ORM cascade on release deletion."""
    fk = next(
        fk for fk in ReleaseTestRunLink.__table__.c.release_id.foreign_keys
    )
    assert fk.column.table.name == "releases"
    assert fk.ondelete == "CASCADE"

    pfk = next(fk for fk in ReleasePhase.__table__.c.release_id.foreign_keys)
    assert pfk.column.table.name == "releases"
    assert pfk.ondelete == "CASCADE"
