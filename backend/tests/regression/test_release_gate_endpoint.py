"""Regression: the release gate endpoints and the baseline comparison (S6a-3).

The three questions these answer, and why each is separate
----------------------------------------------------------
``GET /gate`` reads the STORED verdict. It must not recompute: recomputing
would restate a past decision under today's runs and today's policy, which is
precisely what the snapshot columns on ``ReleaseGateDecision`` exist to prevent.

``POST /gate/evaluate`` computes and, by default, APPENDS to the audit history.
That is a privileged write even though it only reads test data, because a
release decision is later justified by that trail.

``GET /gate/baseline`` compares against the predecessor — and refuses to when
the comparison would mislead.

The recurring failure being guarded
-----------------------------------
A number that is arithmetically correct and semantically meaningless, presented
without the caveat that makes it readable. "NO_GO" over two tests is not the
same claim as "NO_GO" over four thousand, and a pass-rate delta measured against
a release nothing ran in is a confident artefact of the denominator. Once either
is a figure on a scorecard, nobody re-derives whether it meant anything.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services import release_gate_service as gate


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows


def _release(release_id, baseline_id=None, project_id="p1"):
    return SimpleNamespace(
        id=release_id, baseline_release_id=baseline_id, project_id=project_id
    )


def _rollup(**by_test):
    from app.services.release_rollup_service import ReleaseRollup, STATUSES

    r = ReleaseRollup(latest_by_test=dict(by_test))
    r.status_counts = {s: 0 for s in STATUSES}
    for status in r.latest_by_test.values():
        r.status_counts[status] = r.status_counts.get(status, 0) + 1
    return r


class _Session:
    """Serves releases by id; the rollup is stubbed by the caller."""

    def __init__(self, releases):
        self._releases = {str(r.id): r for r in releases}
        self.committed = False

    async def execute(self, stmt, *a, **kw):
        sql = " ".join(str(stmt).split())
        if "FROM releases" in sql:
            # Whichever release the caller asked for; the fake is keyed by the
            # test setting `wanted` before the call.
            return _Result([self._wanted])
        return _Result([])

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


class TestEvaluateRecordsOrPreviews:
    def test_a_preview_does_not_append_to_the_history(self, monkeypatch):
        recorded = []

        async def _no_record(*a, **kw):  # pragma: no cover - must not run
            recorded.append(kw)
            raise AssertionError("preview must not write to the audit history")

        monkeypatch.setattr(gate.decisions, "record_decision", _no_record)
        monkeypatch.setattr(
            gate.rollup_svc, "build_rollup",
            lambda db, rid: _async(_rollup(**{f"t{i}": "PASSED" for i in range(9)})),
        )
        session = _Session([_release("r1")])
        session._wanted = _release("r1")

        result = asyncio.run(gate.evaluate_release(session, "r1", record=False))

        # The history is the point of that table, so writing to it must be a
        # deliberate act rather than a side effect of looking.
        assert result["recorded"] is False
        assert result["decision_id"] is None
        assert recorded == []

    def test_recording_returns_the_decision_id(self, monkeypatch):
        made = SimpleNamespace(id=uuid.uuid4())

        async def _record(*a, **kw):
            return made

        monkeypatch.setattr(gate.decisions, "record_decision", _record)
        monkeypatch.setattr(
            gate.rollup_svc, "build_rollup",
            lambda db, rid: _async(_rollup(**{f"t{i}": "PASSED" for i in range(9)})),
        )
        session = _Session([_release("r1")])
        session._wanted = _release("r1")

        result = asyncio.run(gate.evaluate_release(session, "r1"))

        assert result["recorded"] is True
        assert result["decision_id"] == str(made.id)
        assert result["verdict"] == "GO"

    def test_the_verdict_carries_its_scorecard(self, monkeypatch):
        async def _record(*a, **kw):
            return SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr(gate.decisions, "record_decision", _record)
        monkeypatch.setattr(
            gate.rollup_svc, "build_rollup",
            lambda db, rid: _async(_rollup(a="PASSED", b="PASSED")),
        )
        session = _Session([_release("r1")])
        session._wanted = _release("r1")

        result = asyncio.run(gate.evaluate_release(session, "r1"))

        # Two tests. "NOT_EVALUATED" alone would not tell a reader whether the
        # release is thin or the gate is broken.
        assert result["verdict"] == "NOT_EVALUATED"
        assert result["scorecard"]["measured"] is False
        assert result["scorecard"]["denominator"] == 2
        assert result["scorecard"]["insufficient_reason"]


class TestTheBaselineComparisonRefusesToMislead:
    def _compare(self, monkeypatch, release, current, baseline):
        rollups = {"r1": current, "base": baseline}
        monkeypatch.setattr(
            gate.rollup_svc, "build_rollup", lambda db, rid: _async(rollups[str(rid)])
        )
        session = _Session([release])
        session._wanted = release
        return asyncio.run(gate.compare_to_baseline(session, "r1"))

    def test_no_baseline_is_stated_not_zeroed(self, monkeypatch):
        result = self._compare(
            monkeypatch, _release("r1", baseline_id=None), _rollup(a="PASSED"), _rollup()
        )

        # The first release of a project has no predecessor. A zero delta would
        # imply one and read as "no change".
        assert result["comparable"] is False
        assert "no baseline" in result["reason"]

    def test_an_unmeasured_baseline_blocks_the_delta(self, monkeypatch):
        result = self._compare(
            monkeypatch,
            _release("r1", baseline_id="base"),
            _rollup(**{f"t{i}": "PASSED" for i in range(9)}),
            _rollup(a="PASSED"),
        )

        # 100% against a baseline where one test ran is a large, confident,
        # meaningless number that reads as an improvement.
        assert result["comparable"] is False
        assert "evidence floor" in result["reason"]
        assert "baseline" in result["reason"]

    def test_an_unmeasured_current_release_blocks_it_too(self, monkeypatch):
        result = self._compare(
            monkeypatch,
            _release("r1", baseline_id="base"),
            _rollup(a="PASSED"),
            _rollup(**{f"t{i}": "PASSED" for i in range(9)}),
        )

        assert result["comparable"] is False
        assert "this release" in result["reason"]

    def test_two_measured_releases_compare(self, monkeypatch):
        result = self._compare(
            monkeypatch,
            _release("r1", baseline_id="base"),
            _rollup(**{f"t{i}": "PASSED" for i in range(9)}, bad="FAILED"),
            _rollup(**{f"t{i}": "PASSED" for i in range(8)}, bad1="FAILED", bad2="FAILED"),
        )

        assert result["comparable"] is True
        assert result["pass_rate_delta"] == 10.0

    def test_the_denominator_delta_travels_with_the_rate_delta(self, monkeypatch):
        # A pass-rate improvement on a much smaller suite is not an improvement,
        # and a reader given only the rate cannot see that.
        result = self._compare(
            monkeypatch,
            _release("r1", baseline_id="base"),
            _rollup(**{f"t{i}": "PASSED" for i in range(6)}),
            _rollup(**{f"t{i}": "PASSED" for i in range(20)}),
        )

        assert result["comparable"] is True
        assert result["denominator_delta"] == -14


class TestTheStoredVerdictIsReadNotRecomputed:
    def test_never_evaluated_is_distinct_from_not_evaluated(self, monkeypatch):
        async def _none(*a, **kw):
            return None

        monkeypatch.setattr(gate.decisions, "current_decision", _none)

        result = asyncio.run(gate.current_gate(_Session([]), "r1"))

        # None -> the route 404s. A verdict of NOT_EVALUATED means the gate RAN
        # and could not say; collapsing the two loses the difference between
        # "we have not looked" and "we looked and cannot say".
        assert result is None

    def test_the_stored_snapshot_is_returned_and_labelled(self, monkeypatch):
        from datetime import datetime, timezone

        stored = SimpleNamespace(
            verdict="NO_GO",
            blocking_reasons=["ui::t1 is FAILED"],
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            denominator=40,
            evidence_count=38,
            status_rollup={"PASSED": 37, "FAILED": 1},
            attribution_mix={"explicit_name": 4},
            run_ids=["a", "b"],
        )

        async def _stored(*a, **kw):
            return stored

        monkeypatch.setattr(gate.decisions, "current_decision", _stored)
        # If this were called, the read would be recomputing rather than
        # reading the snapshot.
        monkeypatch.setattr(
            gate.rollup_svc, "build_rollup",
            lambda db, rid: (_ for _ in ()).throw(
                AssertionError("current_gate must READ the snapshot, not recompute")
            ),
        )

        result = asyncio.run(gate.current_gate(_Session([]), "r1"))

        assert result["verdict"] == "NO_GO"
        assert result["scorecard"]["denominator"] == 40
        assert result["scorecard"]["measured"] is True
        # Says plainly that these numbers describe the moment the gate ran, not
        # now.
        assert result["from_snapshot"] is True


def _async(value):
    async def _inner():
        return value

    return _inner()


class TestTheRoutesAreGuardedAndReachable:
    """The wiring, which the service tests above cannot see.

    The first draft called ``require_release_access()(release_id=..., db=...,
    current_user=...)`` by hand inside the handler. That guard takes a
    ``Request`` and reads ``request.path_params`` — it has no ``release_id``
    keyword — so every call would have raised TypeError before doing any work.
    A service-level test cannot catch that, because the handler is where it
    lives.
    """

    @staticmethod
    def _routes():
        from app.routers.releases import router

        return {r.path: r for r in router.routes if "gate" in getattr(r, "path", "")}

    def test_all_three_routes_are_registered(self):
        paths = set(self._routes())

        assert paths == {
            "/api/v1/releases/{release_id}/gate",
            "/api/v1/releases/{release_id}/gate/baseline",
            "/api/v1/releases/{release_id}/gate/evaluate",
        }

    def test_every_gate_route_verifies_access_to_THIS_release(self):
        import inspect

        for path, route in self._routes().items():
            src = inspect.getsource(route.endpoint)
            # The authorization ratchet stops at the first scoped parameter a
            # route satisfies, so it cannot tell a guarded release route from an
            # unguarded one once `release_id` is in the path.
            assert "require_release_access" in src, f"{path} does not verify the release"

    def test_the_guard_is_a_dependency_not_a_direct_call(self):
        import inspect

        from app.routers.releases import evaluate_release_gate

        src = inspect.getsource(evaluate_release_gate)

        # `Depends(require_release_access())` — never `require_release_access()(`
        # with arguments, which raises TypeError on every request because the
        # guard reads `request.path_params` rather than taking a keyword.
        assert "Depends(require_release_access())" in src
        assert "require_release_access()(" not in src

    def test_recording_a_verdict_requires_more_than_membership(self):
        import inspect

        from app.routers.releases import evaluate_release_gate

        src = inspect.getsource(evaluate_release_gate)

        # Appending to an append-only audit trail that a release decision is
        # later justified by is a privileged write, even though it only reads
        # test data to compute.
        assert "require_role(UserRole.QA_LEAD)" in src

    def test_reading_the_gate_does_not_require_qa_lead(self):
        import inspect

        from app.routers.releases import get_release_gate

        src = inspect.getsource(get_release_gate)

        # Reading a verdict is not privileged. Requiring QA_LEAD to LOOK would
        # hide the release decision from the people it is made for.
        assert "require_role" not in src

    def test_the_router_owns_the_commit(self):
        import inspect

        from app.routers.releases import evaluate_release_gate

        src = inspect.getsource(evaluate_release_gate)

        # Repo rule: one commit covers the whole unit of work, so the demote of
        # the previous verdict and the insert of the new one cannot half-land.
        assert "await db.commit()" in src
        # And only when something was actually written.
        assert "if record:" in src

    def test_a_thin_stored_verdict_is_reported_as_unmeasured(self, monkeypatch):
        from datetime import datetime, timezone

        stored = SimpleNamespace(
            verdict="NOT_EVALUATED",
            blocking_reasons=[],
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            denominator=3,
            evidence_count=2,
            status_rollup={"PASSED": 2, "SKIPPED": 1},
            attribution_mix={},
            run_ids=["a"],
        )

        async def _stored(*a, **kw):
            return stored

        monkeypatch.setattr(gate.decisions, "current_decision", _stored)

        result = asyncio.run(gate.current_gate(_Session([]), "r1"))

        # The mirror of the well-measured case. Asserting only the True side
        # let a hardcoded `"measured": True` survive — the flag would then claim
        # every stored verdict was well evidenced, including the thin ones it
        # exists to flag.
        assert result["scorecard"]["measured"] is False
        assert result["scorecard"]["evidence_floor"] == 5


class TestTheRouteDistinguishesNeverEvaluatedFromCannotSay:
    def test_reading_an_unevaluated_release_is_a_404(self, monkeypatch):
        import pytest
        from fastapi import HTTPException

        from app.routers import releases as releases_router

        async def _none(*a, **kw):
            return None

        monkeypatch.setattr(releases_router.release_gate_service, "current_gate", _none)

        # Asserted at the ROUTE, not the service. The service returning None is
        # only half the contract; a handler that turned None into `{}` with a
        # 200 would report "evaluated, nothing to say" for a release nobody has
        # ever gated — and the service-level test could not see it.
        with pytest.raises(HTTPException) as exc:
            asyncio.run(releases_router.get_release_gate("r1", db=_Session([]), _=None))

        assert exc.value.status_code == 404
        assert "not been evaluated" in exc.value.detail

    def test_an_evaluated_release_returns_its_gate(self, monkeypatch):
        from app.routers import releases as releases_router

        async def _gate(*a, **kw):
            return {"verdict": "GO", "from_snapshot": True}

        monkeypatch.setattr(releases_router.release_gate_service, "current_gate", _gate)

        result = asyncio.run(releases_router.get_release_gate("r1", db=_Session([]), _=None))

        assert result["verdict"] == "GO"
