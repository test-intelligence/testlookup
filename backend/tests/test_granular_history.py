"""Phase 2 — granular test-case history / flakiness / metadata read path.

Covers the read-only ``GET /runs/{run_id}/tests/{test_id}/history`` service
(``services.test_case_history_service.get_test_case_history``) and its router
wiring:

  * Project-scoping: a same-``test_fingerprint`` test in ANOTHER project must NOT
    bleed into the timeline (fingerprint is not salted → every history query
    JOINs ``test_runs`` on ``project_id``).
  * Flakiness value matches ``analytics_service.flaky_tests`` semantics for a
    known fixture: ``failure_rate_pct == COUNT(FAILED|BROKEN) * 100 / COUNT`` and
    the classification/impact reuse ``test_health_coach_service`` (no new
    formula).
  * ``require_run_access`` IDOR: a run the caller can't reach is rejected before
    any history is read.
  * Empty-history case: a test with no ``test_case_history`` rows yields an empty
    timeline and a non-flaky, HEALTHY flakiness block.

Unit-level: a content-dispatching fake async DB stands in for Postgres (the
window query + the ``_effective_suite_sql`` raw text are Postgres-shaped). The
fake serves exactly the SELECT surface the service touches, project-scoped so
the cross-project leak test is meaningful.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services import test_case_history_service as svc  # noqa: E402
from app.services.test_health_coach_service import (  # noqa: E402
    _compute_impact_score,
    _compute_quarantine_recommendation,
)


# ─────────────────────────── Fake async DB ───────────────────────────


class _Result:
    def __init__(self, *, first=None, all_=None, scalar=None):
        self._first = first
        self._all = all_ or []
        self._scalar = scalar

    def first(self):
        return self._first

    def all(self):
        return self._all

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDB:
    """Content-dispatching fake. Stores per-project history + identity rows.

    ``history`` is a list of dicts keyed by ``(project_id, test_fingerprint)`` so
    the project-scope filter is honoured exactly the way the real JOIN to
    ``test_runs`` would honour it.
    """

    def __init__(self):
        # (project_id, fingerprint) -> list[history point dict]
        self.history: dict[tuple, list[dict]] = {}
        # test_case row + its run, returned by the first resolver query
        self.tc = None
        self.run = None
        self.canonical = None
        self.effective_suite = None
        self.runs_by_id: dict[uuid.UUID, SimpleNamespace] = {}

    def _in_window_points(self) -> list[dict]:
        """Project-scoped history rows within the service's 30-day cutoff.

        Mirrors the real ``created_at >= now - 30d`` filter so out-of-window
        rows are excluded from BOTH the timeline and the flakiness denominator,
        exactly as the JOINed query would exclude them.
        """
        proj = self.run.project_id
        fp = self.tc.test_fingerprint
        cutoff = datetime.now(timezone.utc) - timedelta(days=svc._FLAKY_WINDOW_DAYS)
        return [
            p for p in self.history.get((proj, fp), [])
            if p["created_at"] >= cutoff
        ]

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()

        # 1. raw text effective-suite probe (check before generic test_cases).
        if "as suite" in sql and "test_runs tr" in sql:
            return _Result(first=SimpleNamespace(suite=self.effective_suite))

        # 2. windowed history query (ROW_NUMBER over test_case_history). Honours
        #    the 30-day created_at cutoff the service now applies.
        if "test_case_history" in sql and "row_number" in sql:
            points = self._in_window_points()
            rows = [
                SimpleNamespace(
                    run_id=p["run_id"],
                    status=p["status"],
                    duration_ms=p.get("duration_ms"),
                    created_at=p["created_at"],
                    build_number=p.get("build_number"),
                    primary_suite_name=p.get("primary_suite_name"),
                    trigger_source=p.get("trigger_source", "ci"),
                )
                for p in sorted(points, key=lambda x: x["created_at"], reverse=True)
            ]
            return _Result(all_=rows[: svc._HISTORY_LIMIT])

        # 2b. flakiness aggregation (count/filter over test_case_history, NO
        #     row_number, NO 50-cap) — the true in-window population, mirroring
        #     analytics_service.flaky_tests. Also honours the 30-day cutoff.
        if "test_case_history" in sql and "count" in sql:
            points = self._in_window_points()
            total = len(points)
            failed = sum(1 for p in points if str(p["status"]) in ("FAILED", "BROKEN"))
            passed = sum(1 for p in points if str(p["status"]) == "PASSED")
            return _Result(first=SimpleNamespace(total=total, failed=failed, passed=passed))

        # 3. canonical identity row.
        if "canonical_test_cases" in sql:
            return _Result(scalar=self.canonical)

        # 4. resolver: select(TestCase, TestRun) join → first() = (tc, run).
        #    (Checked before the label lookup because the resolver SELECTs all
        #    TestRun columns, including build_number.)
        if "test_cases" in sql:
            if self.tc is None:
                return _Result(first=None)
            return _Result(first=(self.tc, self.run))

        # 5. TestRun id/build/created label lookup (in_ list), no test_cases.
        if "test_runs" in sql and "build_number" in sql:
            rows = list(self.runs_by_id.values())
            return _Result(all_=[
                SimpleNamespace(id=r.id, build_number=r.build_number, created_at=r.created_at)
                for r in rows
            ])

        return _Result()


# ─────────────────────────── Builders ───────────────────────────


def _mk_tc(run_id, fingerprint, *, owner="alice", suite="Checkout"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        test_run_id=run_id,
        test_fingerprint=fingerprint,
        test_name="test_checkout",
        owner=owner,
        assigned_to_user_id=None,
        suite_name=suite,
        severity="critical",
        feature="checkout",
    )


def _mk_run(project_id):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        build_number="build-42",
        primary_suite_name="Checkout",
        trigger_source="ci",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def _hist_points(run_id, statuses, *, start=None):
    # Anchor in-window (recent) by default so the service's real 30-day
    # created_at cutoff keeps every seeded row. Tests that want out-of-window
    # rows pass an explicit ``start`` older than 30 days.
    base = start or (datetime.now(timezone.utc) - timedelta(days=7))
    out = []
    for i, st in enumerate(statuses):
        out.append({
            "run_id": run_id if i == 0 else uuid.uuid4(),
            "status": st,
            "duration_ms": 100 + i,
            "created_at": base + timedelta(hours=i),
            "build_number": f"build-{i}",
            "primary_suite_name": "Checkout",
            "trigger_source": "ci",
        })
    return out


@pytest.fixture(autouse=True)
def _patch_seq_map(monkeypatch):
    """fetch_run_seq_map needs a real window query; stub it for the unit DB."""
    async def _fake_seq(db, run_ids):
        return {str(r): i + 1 for i, r in enumerate(run_ids)}
    monkeypatch.setattr(
        "app.services.runs_service.fetch_run_seq_map", _fake_seq, raising=True,
    )


# ─────────────────────────── Tests ───────────────────────────


@pytest.mark.asyncio
async def test_project_scoping_no_cross_project_leak():
    """A same-fingerprint test in another project must NOT appear in history."""
    fp = "deadbeef" * 8
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    db = _FakeDB()
    run_a = _mk_run(proj_a)
    db.run = run_a
    db.tc = _mk_tc(run_a.id, fp)

    # Project A: 4 runs for this fingerprint.
    db.history[(proj_a, fp)] = _hist_points(run_a.id, ["PASSED", "FAILED", "PASSED", "FAILED"])
    # Project B: SAME fingerprint, different project — must be invisible.
    db.history[(proj_b, fp)] = _hist_points(uuid.uuid4(), ["FAILED"] * 9)

    out = await svc.get_test_case_history(db, run_a.id, db.tc.id)

    assert out is not None
    # Only project A's 4 points — project B's 9 never leak in.
    assert len(out["history"]) == 4
    assert out["flakiness"]["total_runs"] == 4
    assert out["flakiness"]["failed"] == 2


@pytest.mark.asyncio
async def test_flakiness_matches_analytics_formula():
    """failure_rate_pct == COUNT(FAILED|BROKEN)*100/COUNT (analytics_service),
    and classification/impact reuse test_health_coach (no new formula)."""
    fp = "cafe" * 16
    proj = uuid.uuid4()
    db = _FakeDB()
    run = _mk_run(proj)
    db.run = run
    db.tc = _mk_tc(run.id, fp)

    # 10 runs: 3 FAILED + 1 BROKEN + 6 PASSED → 4/10 fail.
    statuses = ["PASSED", "FAILED", "PASSED", "BROKEN", "PASSED",
                "FAILED", "PASSED", "FAILED", "PASSED", "PASSED"]
    db.history[(proj, fp)] = _hist_points(run.id, statuses)

    out = await svc.get_test_case_history(db, run.id, db.tc.id)
    flak = out["flakiness"]

    total = len(statuses)
    failed = sum(1 for s in statuses if s in ("FAILED", "BROKEN"))
    # analytics_service.flaky_tests: ROUND(fail*100/total, 1)
    assert flak["failure_rate_pct"] == round(failed * 100.0 / total, 1)
    assert flak["failure_rate"] == round(failed / total, 4)
    # test_health_coach reuse — identical helper values.
    assert flak["impact_score"] == _compute_impact_score(failed / total, total)
    assert flak["classification"] == _compute_quarantine_recommendation(failed / total)
    # mix of pass+fail, >=3 runs → flaky per the auto-detector gate.
    assert flak["is_flaky"] is True


@pytest.mark.asyncio
async def test_flakiness_excludes_out_of_window_rows():
    """Rows older than the 30-day window must NOT count toward total/failed —
    the value matches analytics_service.flaky_tests' WHERE created_at >= cutoff.

    Regression for the dropped window filter: the service previously took the
    most-recent 50 rows of ALL TIME, so stale failures inflated failure_rate
    relative to /failures + /flaky-coach.
    """
    fp = "beef" * 16
    proj = uuid.uuid4()
    db = _FakeDB()
    run = _mk_run(proj)
    db.run = run
    db.tc = _mk_tc(run.id, fp)

    # 4 in-window runs (2 PASSED + 2 FAILED → 0.5) ...
    in_window = _hist_points(
        run.id, ["PASSED", "FAILED", "PASSED", "FAILED"],
        start=datetime.now(timezone.utc) - timedelta(days=5),
    )
    # ... plus 6 FAILED runs from ~200 days ago that MUST be excluded.
    out_of_window = _hist_points(
        uuid.uuid4(), ["FAILED"] * 6,
        start=datetime.now(timezone.utc) - timedelta(days=200),
    )
    db.history[(proj, fp)] = in_window + out_of_window

    out = await svc.get_test_case_history(db, run.id, db.tc.id)
    flak = out["flakiness"]

    # Only the 4 in-window rows count; the 6 stale failures are gone.
    assert flak["total_runs"] == 4
    assert flak["failed"] == 2
    assert flak["passed"] == 2
    assert flak["failure_rate_pct"] == 50.0
    # Timeline is likewise window-bounded.
    assert len(out["history"]) == 4


@pytest.mark.asyncio
async def test_is_flaky_follows_failures_ratio_band():
    """is_flaky gates on the /failures auto-detector band (0.05–0.95), so a
    near-always-pass (2%) or near-always-fail (98%) test reads NON-flaky here,
    matching analytics_service.flaky_tests' HAVING ratio BETWEEN 0.05 AND 0.95.
    """
    proj = uuid.uuid4()

    # 1 FAILED / 49 PASSED = 0.02 → below 0.05 band → not flaky.
    fp_low = "0a" * 32
    db = _FakeDB()
    run = _mk_run(proj)
    db.run = run
    db.tc = _mk_tc(run.id, fp_low)
    db.history[(proj, fp_low)] = _hist_points(run.id, ["FAILED"] + ["PASSED"] * 49)
    out = await svc.get_test_case_history(db, run.id, db.tc.id)
    assert out["flakiness"]["is_flaky"] is False
    assert out["flakiness"]["total_runs"] == 50
    assert out["flakiness"]["failed"] == 1

    # 49 FAILED / 1 PASSED = 0.98 → above 0.95 band → not flaky.
    fp_high = "0b" * 32
    db2 = _FakeDB()
    run2 = _mk_run(proj)
    db2.run = run2
    db2.tc = _mk_tc(run2.id, fp_high)
    db2.history[(proj, fp_high)] = _hist_points(run2.id, ["PASSED"] + ["FAILED"] * 49)
    out2 = await svc.get_test_case_history(db2, run2.id, db2.tc.id)
    assert out2["flakiness"]["is_flaky"] is False
    assert out2["flakiness"]["failed"] == 49


@pytest.mark.asyncio
async def test_empty_history_case():
    """A test with no history rows → empty timeline, non-flaky HEALTHY block."""
    fp = "0" * 64
    proj = uuid.uuid4()
    db = _FakeDB()
    run = _mk_run(proj)
    db.run = run
    db.tc = _mk_tc(run.id, fp)
    # No db.history entry for (proj, fp).

    out = await svc.get_test_case_history(db, run.id, db.tc.id)

    assert out["history"] == []
    flak = out["flakiness"]
    assert flak["is_flaky"] is False
    assert flak["total_runs"] == 0
    assert flak["failure_rate"] == 0.0
    assert flak["classification"] == "HEALTHY"


@pytest.mark.asyncio
async def test_metadata_first_last_seen_and_suite():
    """Metadata surfaces owner, effective suite, and first/last seen labels."""
    fp = "ab" * 32
    proj = uuid.uuid4()
    db = _FakeDB()
    run = _mk_run(proj)
    db.run = run
    db.tc = _mk_tc(run.id, fp, owner="bob")
    db.effective_suite = "Checkout"
    db.history[(proj, fp)] = _hist_points(run.id, ["PASSED", "FAILED"])

    first_run = SimpleNamespace(
        id=uuid.uuid4(), build_number="first-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    last_run = SimpleNamespace(
        id=uuid.uuid4(), build_number="last-9",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    db.runs_by_id = {first_run.id: first_run, last_run.id: last_run}
    db.canonical = SimpleNamespace(
        first_seen_run_id=first_run.id,
        last_seen_run_id=last_run.id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    out = await svc.get_test_case_history(db, run.id, db.tc.id)
    meta = out["metadata"]
    assert meta["owner"] == "bob"
    assert meta["suite"] == "Checkout"
    assert meta["first_seen_run_label"] == "first-1"
    assert meta["last_seen_run_label"] == "last-9"


@pytest.mark.asyncio
async def test_resolver_returns_none_for_wrong_run():
    """test_id that doesn't belong to run_id → None (router 404s)."""
    db = _FakeDB()
    db.tc = None  # resolver finds nothing
    out = await svc.get_test_case_history(db, uuid.uuid4(), uuid.uuid4())
    assert out is None


@pytest.mark.asyncio
async def test_require_run_access_rejects_inaccessible_run():
    """The endpoint guard verifies the PROVIDED run_id — a non-member is 403'd
    BEFORE any history read (IDOR ratchet)."""
    from fastapi import HTTPException
    from app.core.deps import require_run_access

    guard = require_run_access()

    run_id = uuid.uuid4()
    foreign_project = uuid.uuid4()

    # DB returns the run's project; caller is a non-admin, non-member.
    access_db = MagicMock()
    access_db.execute = AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: foreign_project),  # run → project
        SimpleNamespace(scalar_one_or_none=lambda: None),             # membership: none
    ])
    request = SimpleNamespace(path_params={"run_id": str(run_id)})
    user = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", api_key_project_id=None)

    with pytest.raises(HTTPException) as ei:
        await guard(request=request, db=access_db, current_user=user)
    assert ei.value.status_code == 403
