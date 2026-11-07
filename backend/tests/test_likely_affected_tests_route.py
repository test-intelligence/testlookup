"""W36 STRAT-08: ``GET /api/v1/runs/{run_id}/likely-affected-tests`` (route + service, contract §2).

The real router, the real ``require_run_access`` guard, the real
``test_impact_service`` and the real ``get_tia_readiness`` run on a session
double that EVALUATES each statement against an in-memory world: it filters by
every bind the statement carries (run, window, prior, statuses) and by every
``project_id`` bind -- and by nothing it does not carry, so a statement that
lost its project predicate reads the foreign project's rows and the scoping
test goes red.

Pins: the two honesty gates (``no_commit_range``; ``no_changed_files`` for an
``unavailable`` row and for a range whose commits carry no files); history not
ready -> the path-only list with ``co_failure_runs`` null; available with no
match -> ``tests: []`` + ``available: true``; co-failure counted against a hand
count, where a FOREIGN project with the identical fingerprints, identical
changed paths and identical failures adds nothing; a non-member gets the
guard's refusal before any service read; ``limit`` 1..50; and a GET that adds /
flushes / commits / rolls back nothing and never lazily resolves a range.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.routers import commit_attribution as commit_router
from app.services import test_impact_service as tis

CHANGED = ["payments/charge.py", "docs/README.md"]


class _Result:
    def __init__(self, value=None, rows=()):
        self._value, self._rows = value, list(rows)

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


def _pids(params):
    return [v for k, v in params.items() if k.startswith("project_id")]


def _in_project(project_id, params):
    return all(project_id == p for p in _pids(params))


class _Session:
    """Evaluates the guard, the run load and the service's statements on the world; spies every write."""

    def __init__(self, world):
        self.w = world
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        w = self.w
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, p = str(compiled), dict(compiled.params)
        self.statements.append((sql, p))
        if "FROM project_members" in sql:
            return _Result(uuid.uuid4() if w.member else None)
        if sql.startswith("SELECT test_runs.project_id"):
            run = w.runs.get(p["id_1"])
            return _Result(run.project_id if run else None)
        if sql.startswith("SELECT test_runs.id"):
            return _Result(w.runs.get(p["id_1"]))
        if sql.startswith("SELECT run_commit_ranges.source, run_commit_ranges.base_source"):
            return _Result(rows=[r for r in w.ranges if r.run_id == p["run_id_1"] and _in_project(r.project_id, p)])
        if sql.startswith("SELECT run_commit_ranges.resolved_at"):  # get_tia_readiness
            rows = [r for r in w.ranges if _in_project(r.project_id, p) and r.resolved_at >= p["resolved_at_1"]]
            return _Result(rows=sorted(rows, key=lambda r: r.resolved_at, reverse=True)[: p["param_1"]])
        if sql.startswith("SELECT run_commit_ranges.run_id"):     # the prior ranges
            rows = [r for r in w.ranges if _in_project(r.project_id, p) and r.resolved_at >= p["resolved_at_1"]
                    and r.run_id != p["run_id_1"] and w.runs[r.run_id].created_at < p["created_at_1"]]
            return _Result(rows=sorted(rows, key=lambda r: r.resolved_at, reverse=True)[: p["param_1"]])
        if sql.startswith("SELECT count(test_cases.id)"):
            return _Result(sum(1 for c in w.cases if c.run_id == p["test_run_id_1"] and _in_project(c.project_id, p)))
        if sql.startswith("SELECT test_cases.test_fingerprint, test_cases.test_name"):
            return _Result(rows=[c for c in w.cases if c.run_id == p["test_run_id_1"] and _in_project(c.project_id, p)])
        if sql.startswith("SELECT test_cases.test_fingerprint, count("):  # co-failure, self-joined to this run
            current = {c.test_fingerprint for c in w.cases if c.run_id == p["test_run_id_1"]}
            counts: dict[str, set] = {}
            for c in w.cases:
                if (c.run_id in p["test_run_id_2"] and c.status in p["status_1"] and _in_project(c.project_id, p)
                        and c.test_fingerprint in current):
                    counts.setdefault(c.test_fingerprint, set()).add(c.run_id)
            return _Result(rows=[(fp, len(runs)) for fp, runs in counts.items()])
        if "FROM flaky_score" in sql:
            current = {c.test_fingerprint for c in w.cases if c.run_id == p["test_run_id_1"]}
            return _Result(rows=[(f.test_fingerprint, f.score, f.confidence) for f in w.flaky
                                 if _in_project(f.project_id, p) and f.test_fingerprint in current])
        raise AssertionError(f"unexpected statement: {sql[:160]}")


TESTS = {  # fingerprint -> (test_name, class_name); the same md5 in both projects
    "fp-charge": ("test_charge_succeeds", "payments.ChargeTest"),   # stem overlap with payments/charge.py
    "fp-refund": ("test_refund", "billing.RefundTest"),             # no overlap
    "fp-cart": ("test_cart", "cart.CartTest"),                      # no overlap
    "fp-ui": ("test_button", "ui.ButtonTest"),                      # no overlap, never fails
}


def _build_world(now, *, corpus=True):
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    w = SimpleNamespace(p1=p1, p2=p2, runs={}, ranges=[], cases=[], flaky=[], member=True,
                        user=SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True, username="vera"))

    def run(project, days_ago, files=None, *, source="supplied", base="green_baseline", statuses=None):
        r = SimpleNamespace(id=uuid.uuid4(), project_id=project, created_at=now - timedelta(days=days_ago),
                            commit_hash=None, ci_repo=None)
        w.runs[r.id] = r
        if files is not None:
            w.ranges.append(SimpleNamespace(run_id=r.id, project_id=project, source=source, base_source=base,
                                            commits=[{"sha": f"s{len(w.ranges)}", "files": files}],
                                            resolved_at=r.created_at + timedelta(minutes=5)))
        for fp, status in (statuses or {}).items():
            name, cls = TESTS[fp]
            w.cases.append(SimpleNamespace(run_id=r.id, project_id=project, test_fingerprint=fp, test_name=name,
                                           class_name=cls, package_name=None, status=status, stack_trace=None,
                                           error_message=None))
        return r

    # The viewed run: four tests; CHARGE failed here.
    w.run = run(p1, 0, CHANGED, statuses={"fp-charge": "FAILED", "fp-refund": "PASSED", "fp-cart": "PASSED",
                                          "fp-ui": "PASSED"})
    # Prior P1 runs sharing payments/charge.py (hand count: charge 1, cart 2, refund 0).
    run(p1, 3, ["payments/charge.py"], statuses={"fp-charge": "FAILED", "fp-cart": "FAILED"})
    run(p1, 2, ["payments/charge.py", "web/a.ts"], statuses={"fp-cart": "BROKEN", "fp-charge": "PASSED"})
    run(p1, 1, ["docs/README.md"], statuses={"fp-cart": "PASSED", "fp-refund": "SKIPPED"})
    # A prior P1 run that shares NO path: refund failed there, which must not count.
    run(p1, 4, ["other/x.py"], statuses={"fp-refund": "FAILED"})
    # A LATER P1 run sharing the path (not prior): cart failed there, which must not count.
    later = run(p1, 0, ["payments/charge.py"], statuses={"fp-cart": "FAILED"})
    later.created_at = now + timedelta(hours=1)
    # The FOREIGN project: identical paths, identical fingerprints, failing every time.
    for days in (1, 2, 3, 4, 5):
        run(p2, days, list(CHANGED), statuses={"fp-charge": "FAILED", "fp-refund": "FAILED", "fp-cart": "FAILED"})
    w.flaky.append(SimpleNamespace(project_id=p2, test_fingerprint="fp-charge", score=0.95, confidence="high"))
    if corpus:  # 30 usable P1 ranges over 29 days, 30 distinct paths: readiness available
        for i in range(30):
            run(p1, 1 + i, [f"corpus/module_{i:02d}.py"])
    return w


@pytest.fixture
def make_world(monkeypatch):
    monkeypatch.setattr(commit_router, "lazy_resolve_commit_range",
                        AsyncMock(side_effect=AssertionError("the likely-affected GET lazily resolved a range")))

    def make(*, corpus=True):
        w = _build_world(datetime.now(timezone.utc) - timedelta(minutes=30), corpus=corpus)
        w.session = _Session(w)

        async def _db():
            yield w.session

        app = FastAPI()
        app.include_router(commit_router.router)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_active_user] = lambda: w.user
        w.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testlookup.test")
        return w

    return make


async def _get(w, run_id=None, **params):
    return await w.http.get(f"/api/v1/runs/{run_id or w.run.id}/likely-affected-tests", params=params)


def _no_writes(w):
    s = w.session
    assert not s.add.called and not s.flush.called and not s.commit.called and not s.rollback.called


def _by_fp(body):
    return {t["test_fingerprint"]: t for t in body["tests"]}


# ── Co-failure, scoped to the project, against the hand count ─────────────────


async def test_ranked_list_with_history_matches_the_hand_count_and_ignores_the_foreign_project(make_world):
    w = make_world()
    resp = await _get(w)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["available"], body["reason"], body["advisory"], body["caveat"]) == (
        True, None, True, tis.ADVISORY_CAVEAT)
    assert (body["source"], body["base_source"], body["base_anchor_is_strong"]) == ("supplied", "green_baseline", True)
    assert (body["changed_files_count"], body["file_detail_coverage"], body["total_candidates"]) == (2, 1.0, 4)
    history = body["history"]
    assert history["available"] is True and history["insufficient_data_reason"] is None
    # 34 prior P1 ranges read (3 sharing + 1 not + 30 corpus); the later run and every P2 range are not.
    assert (history["window_days"], history["runs_considered"], history["scan_capped"]) == (90, 34, False)
    rows = _by_fp(body)
    assert list(rows) == ["fp-charge", "fp-cart"]                 # score desc
    charge, cart = rows["fp-charge"], rows["fp-cart"]
    # Hand count: charge failed in 1 of the 3 sharing prior runs, cart in 2 (FAILED + BROKEN); refund's failure was in
    # a run sharing no path. With the foreign project counted it would be charge 6, cart 7, refund 5.
    assert charge["reason"] == {"overlapping_paths": ["payments/charge.py"], "overlap_score": 1.0,
                                "co_failure_runs": 1, "history_runs_considered": 3, "known_flaky": False}
    assert charge["score"] == 0.68 and charge["status_in_run"] == "FAILED"
    assert cart["reason"]["co_failure_runs"] == 2 and cart["reason"]["overlapping_paths"] == []
    assert cart["score"] == 0.16 and cart["status_in_run"] == "PASSED"
    _no_writes(w)


async def test_every_read_is_bound_to_the_runs_project_and_never_the_foreign_one(make_world):
    w = make_world()
    assert (await _get(w)).status_code == 200
    service = [(sql, p) for sql, p in w.session.statements
               if any(t in sql for t in ("FROM run_commit_ranges", "FROM test_cases", "FROM flaky_score"))]
    assert len(service) == 6  # range, readiness, candidates, prior ranges, co-failure, flaky
    for sql, params in service:
        assert _pids(params) and set(_pids(params)) == {w.p1}, sql[:120]


async def test_a_known_flaky_test_in_this_project_earns_no_co_failure_credit(make_world):
    w = make_world()
    w.flaky.append(SimpleNamespace(project_id=w.p1, test_fingerprint="fp-cart", score=0.8, confidence="medium"))
    rows = _by_fp((await _get(w)).json())
    assert "fp-cart" not in rows                                  # its co-failures were its only evidence
    assert rows["fp-charge"]["reason"]["known_flaky"] is False    # P2's flaky row for charge is not read


# ── Honesty gates and history states ──────────────────────────────────────────


async def test_no_range_row_is_no_commit_range_never_an_empty_list(make_world):
    w = make_world()
    w.ranges = [r for r in w.ranges if r.run_id != w.run.id]
    body = (await _get(w)).json()
    assert (body["available"], body["reason"], body["tests"]) == (False, "no_commit_range", [])
    assert (body["source"], body["base_source"], body["base_anchor_is_strong"], body["file_detail_coverage"]) == (
        None, None, None, None)
    assert body["total_candidates"] == 4 and body["caveat"] == tis.ADVISORY_CAVEAT
    _no_writes(w)


@pytest.mark.parametrize("source, commits, coverage", [
    ("unavailable", [], None),                                    # the honest unavailable row
    ("connector", [{"sha": "a", "files": []}, {"sha": "b", "files": []}], 0.0),  # commits without files
])
async def test_a_range_without_files_is_no_changed_files(make_world, source, commits, coverage):
    w = make_world()
    (row,) = [r for r in w.ranges if r.run_id == w.run.id]
    row.source, row.commits, row.base_source = source, commits, "last_completed_run"
    body = (await _get(w)).json()
    assert (body["available"], body["reason"], body["tests"], body["changed_files_count"]) == (
        False, "no_changed_files", [], 0)
    assert (body["source"], body["base_source"], body["base_anchor_is_strong"]) == (
        source, "last_completed_run", False)
    assert body["file_detail_coverage"] == coverage and body["total_candidates"] == 4


async def test_history_not_ready_gives_the_path_only_list_with_nulls(make_world):
    w = make_world(corpus=False)
    body = (await _get(w)).json()
    history = body["history"]
    assert history["available"] is False
    assert history["insufficient_data_reason"].startswith("only 6 of 6 runs carry a usable commit range")
    assert (history["usable_runs"], history["runs_considered"], history["scan_capped"]) == (6, 0, False)
    assert body["available"] is True and [t["test_fingerprint"] for t in body["tests"]] == ["fp-charge"]
    (charge,) = body["tests"]
    assert charge["score"] == 1.0                                 # = the overlap
    assert charge["reason"]["co_failure_runs"] is None and charge["reason"]["history_runs_considered"] is None
    assert not any("SELECT run_commit_ranges.run_id" in sql for sql, _p in w.session.statements)  # no prior scan


@pytest.mark.parametrize("corpus", [False, True])
async def test_available_with_no_match_is_an_empty_measured_list(make_world, corpus):
    w = make_world(corpus=corpus)
    (row,) = [r for r in w.ranges if r.run_id == w.run.id]
    row.commits = [{"sha": "z", "files": ["infra/terraform/main.tf"]}]
    body = (await _get(w)).json()
    assert (body["available"], body["reason"], body["tests"]) == (True, None, [])
    assert body["history"]["available"] is corpus and body["total_candidates"] == 4


# ── Guard, limit, read-only ───────────────────────────────────────────────────


async def test_a_non_member_gets_the_guards_refusal_before_any_service_read(make_world):
    w = make_world()
    w.member = False
    resp = await _get(w)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    assert not any("run_commit_ranges" in sql or "test_cases" in sql for sql, _p in w.session.statements)
    _no_writes(w)


@pytest.mark.parametrize("limit, status", [("0", 422), ("51", 422), ("abc", 422), ("1", 200), ("50", 200)])
async def test_limit_is_1_to_50(make_world, limit, status):
    w = make_world()
    resp = await _get(w, limit=limit)
    assert resp.status_code == status, resp.text
    if limit == "1":
        assert [t["test_fingerprint"] for t in resp.json()["tests"]] == ["fp-charge"]


async def test_the_get_never_writes_and_never_lazily_resolves(make_world):
    w = make_world()
    assert (await _get(w)).status_code == 200
    _no_writes(w)
    commit_router.lazy_resolve_commit_range.assert_not_called()
