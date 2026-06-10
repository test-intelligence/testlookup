"""Phase 5 — granular step enrichment for reports / coverage / my-failures.

Unit coverage for the SHARED batched step-read helpers in
``services.runs_service`` that back the Phase 5 surfaces:

  * ``first_failed_step_by_canonical`` — ``{canonical_id -> first FAILED/BROKEN
    step name}`` (lowest ordinal wins), absent when no failing step.
  * ``first_failed_step_by_fingerprint`` — resolves ``test_fingerprint`` →
    project-scoped ``CanonicalTestCase`` anchor, then reuses the canonical
    helper. The project filter is what scopes the tenant (fingerprint is not
    salted).
  * ``step_success_by_canonical`` — ``{canonical_id -> (passed, total)}`` over
    the snapshot, absent when no steps.

The helpers issue real SQLAlchemy ``select`` statements; we drive them with a
content-dispatching fake async DB that inspects each compiled statement and
returns the matching rows. The fake also COUNTS ``execute`` calls so the
"no N+1 / batched" guarantee is asserted directly (one query per helper, two
for the fingerprint helper: anchor-resolve + step-fetch).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import runs_service as svc  # noqa: E402


# ─────────────────────────── Fake async DB ───────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Dispatches on the rendered SQL of each ``select`` statement.

    ``steps`` rows are tuples shaped per the SELECT the helper under test
    issues. ``canon`` maps (project_id, fingerprint) -> canonical_id so the
    fingerprint helper's anchor-resolve query is honoured project-scoped.
    """

    def __init__(self, *, steps=None, canon=None):
        # steps: list of SimpleNamespace(canonical_test_case_id, ordinal, name, status)
        self.steps = steps or []
        # canon: dict[(project_id_str, fingerprint)] -> canonical_id
        self.canon = canon or {}
        self.execute_calls: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        self.execute_calls.append(sql)

        # Anchor-resolve query (fingerprint helper): SELECT from canonical_test_cases.
        if "canonical_test_cases" in sql and "test_steps" not in sql:
            # Extract the bound project_id + fingerprint set from the statement.
            params_map = stmt.compile().params
            pid = str(params_map.get("project_id_1") or params_map.get("project_id"))
            fps = _flatten_param_values(params_map, "test_fingerprint")
            rows = [
                SimpleNamespace(id=cid, test_fingerprint=fp)
                for (cpid, fp), cid in self.canon.items()
                if cpid == pid and fp in fps
            ]
            # tuple-shaped (id, test_fingerprint)
            return _Result([(r.id, r.test_fingerprint) for r in rows])

        # Step-success aggregation: GROUP BY with count(test_steps.id).
        if "test_steps" in sql and "group by" in sql:
            wanted = self._wanted_canonical_ids(stmt)
            agg: dict[uuid.UUID, list[int]] = {}
            for s in self.steps:
                if s.canonical_test_case_id not in wanted:
                    continue
                a = agg.setdefault(s.canonical_test_case_id, [0, 0])
                a[1] += 1
                if s.status == "PASSED":
                    a[0] += 1
            return _Result([(cid, p, t) for cid, (p, t) in agg.items()])

        # First-failed-step: SELECT canonical, ordinal, name WHERE status IN (...).
        if "test_steps" in sql:
            wanted = self._wanted_canonical_ids(stmt)
            rows = sorted(
                (
                    s for s in self.steps
                    if s.canonical_test_case_id in wanted
                    and s.status in ("FAILED", "BROKEN")
                ),
                key=lambda s: (str(s.canonical_test_case_id), s.ordinal),
            )
            return _Result([(s.canonical_test_case_id, s.ordinal, s.name) for s in rows])

        return _Result([])

    @staticmethod
    def _wanted_canonical_ids(stmt) -> set:
        params_map = stmt.compile().params
        return _flatten_param_values(params_map, "canonical_test_case_id")


def _flatten_param_values(params_map: dict, prefix: str) -> set:
    """Collect bound values for ``prefix*`` params, flattening expanding-IN lists.

    SQLAlchemy renders ``col.in_([...])`` as a single bind whose value is the
    list (expanding), so a naive ``{v for ...}`` chokes on the unhashable list.
    """
    out: set = set()
    for k, v in params_map.items():
        if not k.startswith(prefix):
            continue
        if isinstance(v, (list, tuple, set)):
            out.update(v)
        else:
            out.add(v)
    return out


# ─────────────────────────────── Fixtures ───────────────────────────────


def _step(cid, ordinal, name, status):
    return SimpleNamespace(
        canonical_test_case_id=cid, ordinal=ordinal, name=name, status=status,
    )


# ─────────────────────────────── Tests ───────────────────────────────


@pytest.mark.asyncio
async def test_first_failed_step_resolution_lowest_ordinal_wins():
    cid = uuid.uuid4()
    db = _FakeDB(steps=[
        _step(cid, 0, "open login page", "PASSED"),
        _step(cid, 1, "submit bad creds", "FAILED"),
        _step(cid, 2, "assert error toast", "BROKEN"),
    ])
    out = await svc.first_failed_step_by_canonical(db, [cid])
    # First *failing* step in ordinal order is the FAILED one at ordinal 1.
    assert out == {cid: "submit bad creds"}


@pytest.mark.asyncio
async def test_first_failed_step_none_when_no_failing_step():
    cid = uuid.uuid4()
    db = _FakeDB(steps=[
        _step(cid, 0, "step a", "PASSED"),
        _step(cid, 1, "step b", "PASSED"),
    ])
    out = await svc.first_failed_step_by_canonical(db, [cid])
    assert out == {}  # absent → caller defaults to None


@pytest.mark.asyncio
async def test_first_failed_step_empty_input_no_query():
    db = _FakeDB()
    out = await svc.first_failed_step_by_canonical(db, [])
    assert out == {}
    assert db.execute_calls == []  # short-circuits — no DB round-trip


@pytest.mark.asyncio
async def test_first_failed_step_batched_single_query():
    c1, c2, c3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _FakeDB(steps=[
        _step(c1, 0, "c1 ok", "PASSED"),
        _step(c1, 1, "c1 fail", "FAILED"),
        _step(c2, 0, "c2 broken", "BROKEN"),
        _step(c3, 0, "c3 ok", "PASSED"),
    ])
    out = await svc.first_failed_step_by_canonical(db, [c1, c2, c3])
    assert out == {c1: "c1 fail", c2: "c2 broken"}  # c3 has no failing step
    assert len(db.execute_calls) == 1  # ONE query for the whole batch — no N+1


@pytest.mark.asyncio
async def test_first_failed_step_by_fingerprint_project_scoped():
    pid = uuid.uuid4()
    other_pid = uuid.uuid4()
    cid = uuid.uuid4()
    other_cid = uuid.uuid4()
    fp = "deadbeef"
    db = _FakeDB(
        canon={
            (str(pid), fp): cid,
            (str(other_pid), fp): other_cid,  # same fingerprint, other tenant
        },
        steps=[
            _step(cid, 0, "in-project fail", "FAILED"),
            _step(other_cid, 0, "other-tenant fail", "FAILED"),
        ],
    )
    out = await svc.first_failed_step_by_fingerprint(db, pid, [fp])
    # Only the in-project anchor is resolved → only its step surfaces.
    assert out == {fp: "in-project fail"}
    # Two queries: anchor-resolve + step-fetch (both batched), never per-test.
    assert len(db.execute_calls) == 2


@pytest.mark.asyncio
async def test_first_failed_step_by_fingerprint_none_when_no_anchor():
    pid = uuid.uuid4()
    db = _FakeDB(canon={}, steps=[])
    out = await svc.first_failed_step_by_fingerprint(db, pid, ["nope"])
    assert out == {}
    # Anchor-resolve runs once and finds nothing → no second query.
    assert len(db.execute_calls) == 1


@pytest.mark.asyncio
async def test_step_success_by_canonical_counts():
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    db = _FakeDB(steps=[
        _step(c1, 0, "a", "PASSED"),
        _step(c1, 1, "b", "PASSED"),
        _step(c1, 2, "c", "FAILED"),
        _step(c2, 0, "d", "PASSED"),
    ])
    out = await svc.step_success_by_canonical(db, [c1, c2])
    assert out == {c1: (2, 3), c2: (1, 1)}
    assert len(db.execute_calls) == 1  # batched


@pytest.mark.asyncio
async def test_step_success_none_when_no_steps():
    cid = uuid.uuid4()
    db = _FakeDB(steps=[])  # canonical has no captured steps
    out = await svc.step_success_by_canonical(db, [cid])
    assert out == {}  # absent → caller leaves step_success None
