"""A retried ingest must resume its own run, not collide with it.

Found by exploratory testing (2026-08-07) — bulk-ingesting 60 runs into a
throwaway project via ``POST /api/v1/ingest/file``. Three minutes later:

  * **56 of 60 runs still IN_PROGRESS**, aggregates never populated, zero
    progress across repeated polls;
  * **38 distinct** ``ingest_uploaded_file`` task ids in retry loops and
    **145** duplicate-key events in the ingestion worker::

      sqlalchemy.exc.IntegrityError: UniqueViolationError:
        duplicate key value violates unique constraint "test_runs_pkey"
        DETAIL: Key (id)=(7741218b-…) already exists.
      Task ingest_uploaded_file[…] retry: Retry in 126s

  * Redis queue depths all **0** — the tasks sat in retry-ETA, not queued, so
    "the queue is empty" was actively misleading.

Root cause: ``routers/ingest.py`` mints ``run_id`` up front and passes it to
``ingest_uploaded_file.delay(run_id=…)``. The task inserts a ``TestRun`` with
that id. If it fails *after* the insert, Celery retries with the SAME id and
dies on the primary key — so the task can **never** succeed. Any transient
failure became a permanently stuck run, retrying every ~2 minutes forever, while
dashboards silently under-reported (the run's aggregates stay zero).

This violated a convention the repo states in ``backend/CLAUDE.md``:
*"Per-(entity, run) writes must be idempotent."*

Fix: when an explicit ``run_id`` already exists **in the same project**, resume
that row.

The distinction that matters, and why this does not reopen the bug the
``reuse_existing=False`` branch exists to prevent: that branch refuses to merge
on a *fuzzy* match — ``(project_id, build_number)`` — where an operator-typed or
timestamp-defaulted build label could collide with an unrelated run and blend two
datasets. This resumption matches the caller's **own explicit primary key**, so
it can only ever resume the run this same task created. Project scoping keeps a
cross-tenant id from resolving.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import ingestion_pipeline  # noqa: E402


def _src() -> str:
    """Executable source only — comments/docstring stripped.

    The fix's own comment discusses ``run_id``, ``reuse_existing`` and
    ``test_runs_pkey`` at length. Prose has fooled structural assertions
    repeatedly in this session; strip it before reasoning.
    """
    src = inspect.getsource(ingestion_pipeline.create_run_from_payload)
    out, in_doc = [], False
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith(('"""', "'''")):
            if not in_doc and stripped.count('"""') >= 2 and len(stripped) > 3:
                continue
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


class TestRetryResumption:
    def test_an_existing_run_id_is_looked_up(self):
        src = _src()
        assert "TestRun.id ==" in src, (
            "create_run_from_payload never checks whether the supplied run_id "
            "already exists, so a retry re-inserts it and dies on test_runs_pkey"
        )

    def test_the_lookup_is_project_scoped(self):
        """A bare id lookup would resolve across tenants."""
        src = _src()
        m = re.search(r"TestRun\.id ==.*?\)\s*\)\s*\)\.scalar_one_or_none\(\)", src, re.S)
        assert m, "could not locate the run_id lookup — update this test"
        assert "TestRun.project_id == pid" in m.group(0), (
            "the run_id lookup is not project-scoped"
        )

    def test_resumption_happens_before_the_insert(self):
        """Ordering is the fix. After the insert it would be unreachable."""
        src = _src()
        lookup_at = src.find("TestRun.id ==")
        insert_at = src.find("db.add(run)")
        assert lookup_at != -1 and insert_at != -1
        assert lookup_at < insert_at, (
            "the run_id lookup must precede db.add(run), or the duplicate-key "
            "collision still happens on every retry"
        )

    def test_it_returns_early_rather_than_falling_through(self):
        src = _src()
        head = src[: src.find("db.add(run)")]
        assert "return prior" in head, (
            "resumption must return the existing run; falling through would "
            "still construct and insert a second TestRun"
        )


class TestTheNoMergeInvariantSurvives:
    """The ``reuse_existing=False`` branch exists to stop unrelated runs merging
    on a fuzzy (project_id, build_number) match. That must still hold."""

    def test_unique_build_suffixing_is_still_applied(self):
        assert "_unique_build_number" in _src(), (
            "the no-merge path lost its build-label de-collision"
        )

    def test_fuzzy_build_reuse_is_still_gated_on_reuse_existing(self):
        src = _src()
        assert "if reuse_existing:" in src
        reuse_block_at = src.find("if reuse_existing:")
        build_match_at = src.find("TestRun.build_number == build_number")
        assert build_match_at > reuse_block_at, (
            "build-number matching escaped the reuse_existing guard — unrelated "
            "runs could merge again"
        )

    def test_resumption_matches_on_id_not_on_build_number(self):
        """The whole safety argument: exact PK, never a fuzzy label."""
        src = _src()
        head = src[: src.find("db.add(run)")]
        prior_lookup = head[head.find("if run_id:") :]
        assert "TestRun.id ==" in prior_lookup
        assert "build_number" not in prior_lookup.split("scalar_one_or_none")[0], (
            "resumption must not key on build_number — that is the fuzzy match "
            "the no-merge branch deliberately refuses"
        )


def test_convention_is_documented():
    """Anchors the fix to the stated rule it restores."""
    from pathlib import Path

    claude_md = Path(__file__).resolve().parents[2] / "CLAUDE.md"
    if not claude_md.exists():
        pytest.skip("backend/CLAUDE.md is gitignored and absent in this checkout")
    assert "idempotent" in claude_md.read_text(encoding="utf-8").lower()
