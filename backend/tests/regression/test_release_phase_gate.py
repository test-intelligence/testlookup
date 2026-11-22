"""Regression: phase gating must not pass vacuously (S6b).

The tracker's own ordering constraint names this risk: "S3a must populate
``phase_id`` before S6b gates on it, or the gate evaluates every phase against
zero runs and passes vacuously."

That is not a hypothetical. ``match_phase`` returns None freely — phases are
optional and their planned windows need not cover the whole release — so a NULL
``phase_id`` is a common, CORRECT state and many phases legitimately have no
runs. A gate that reads "no failures" off an empty phase hands out an approval
for work nobody did, and that approval looks exactly like a real one.

So most of this file is about the empty and near-empty cases. The populated
happy path is the easy part and gets one test.

The second theme is that three outcomes must stay distinguishable. A release
blocked by a FAILING phase and one blocked by an UNEVALUATED phase need opposite
actions — fix the tests, or go run some — and a boolean cannot carry that.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import release_phase_gate_service as gate
from app.services.release_rollup_service import ReleaseRollup, STATUSES

# The phase is loaded together with its release, so the release id must be a
# real UUID: a value that is not one cannot own a phase.
REL = str(uuid.uuid4())


def _rollup(**by_test):
    r = ReleaseRollup(latest_by_test=dict(by_test))
    r.status_counts = {s: 0 for s in STATUSES}
    for status in r.latest_by_test.values():
        r.status_counts[status] = r.status_counts.get(status, 0) + 1
    return r


def _phase(name="QA", phase_id=None, exit_criteria=None, order=0):
    return SimpleNamespace(
        id=phase_id or uuid.uuid4(),
        name=name,
        exit_criteria=exit_criteria,
        order_index=order,
        project_id=None,
    )


class _Session:
    def __init__(self, phase=None, phases=None, project_id=None, defects=None):
        self._phase = phase
        self._phases = phases or []
        self._project_id = project_id
        self._defects = defects or []

    async def execute(self, stmt, *a, **kw):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT releases.project_id"):
            # D-113: the release-blocker read looks the project up first.
            rows = [self._project_id]
        elif "FROM defects" in sql:
            rows = list(self._defects)
        else:
            rows = self._phases if "ORDER BY" in sql else [self._phase]

        class _R:
            def scalar_one_or_none(_self):
                return rows[0] if rows else None

            def scalars(_self):
                return _self

            def all(_self):
                return rows

        return _R()


def _stub(monkeypatch, rollup, policy=None):
    async def _build(db, rid, phase_id=None):
        return rollup

    async def _policy(db, pid, **kw):
        return policy or {"document": {}, "sources": {}, "layers": [],
                          "effective_level": "hardcoded"}

    monkeypatch.setattr(gate.rollup_svc, "build_rollup", _build)
    monkeypatch.setattr(gate.policy_resolution, "resolve_effective_document", _policy)


class TestAnEmptyPhaseIsNeverAPass:
    def test_a_phase_with_no_runs_is_not_evaluated(self, monkeypatch):
        _stub(monkeypatch, _rollup())
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        # The whole point. "No failures" off zero runs is an approval for work
        # nobody did, and it looks identical to a real pass.
        assert result["verdict"] == "NOT_EVALUATED"
        assert result["may_exit"] is False

    def test_a_phase_where_everything_was_skipped_is_not_evaluated(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "SKIPPED" for i in range(40)}))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        # 40 tests, none run. A gate keyed on "nothing failed" says GO.
        assert result["verdict"] == "NOT_EVALUATED"
        assert result["may_exit"] is False

    def test_a_thin_phase_is_not_evaluated(self, monkeypatch):
        _stub(monkeypatch, _rollup(a="PASSED", b="PASSED"))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        assert result["verdict"] == "NOT_EVALUATED"
        assert result["scorecard"]["measured"] is False

    def test_a_missing_phase_is_refused_rather_than_passed(self, monkeypatch):
        _stub(monkeypatch, _rollup())

        # An id that resolves to nothing must not fall through to a GO. It used
        # to answer 200 NOT_EVALUATED "no such phase"; it is now 404, the same
        # answer a phase of another release gets, so the route does not tell a
        # caller which phase ids exist
        # (tests/regression/test_release_phase_belongs_to_release.py).
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                gate.evaluate_phase(_Session(None), REL, uuid.uuid4(), record=False)
            )

        assert exc.value.status_code == 404
        assert exc.value.detail == "Phase not found"


class TestAPopulatedPhaseDecidesNormally:
    def test_enough_evidence_and_no_failures_may_exit(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        assert result["verdict"] == "GO"
        assert result["may_exit"] is True

    def test_a_failure_blocks_the_exit(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}, bad="FAILED"))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        assert result["verdict"] == "NO_GO"
        assert result["may_exit"] is False
        assert result["blocking_reasons"]

    def test_recorded_phase_snapshots_incomplete_ingestion(self, monkeypatch):
        rollup = _rollup(**{f"t{i}": "PASSED" for i in range(9)})
        rollup.incomplete_runs = {"partial-run": 2}
        _stub(monkeypatch, rollup)
        phase = _phase()
        phase.project_id = uuid.uuid4()
        captured = {}

        async def _record(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr(gate.decisions, "record_decision", _record)

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=True)
        )

        assert result["verdict"] == "NOT_EVALUATED"
        assert captured["ingestion_complete"] is False
        assert captured["incomplete_runs"] == {"partial-run": 2}


class TestThePhasePolicyLayersOverTheProject:
    def test_exit_criteria_override_only_the_keys_they_name(self, monkeypatch):
        _stub(
            monkeypatch,
            _rollup(**{f"t{i}": "PASSED" for i in range(9)}),
            policy={
                "document": {"thresholds": {"go": 90, "no_go": 50}},
                "sources": {"thresholds.go": "project", "thresholds.no_go": "project"},
                "layers": ["project"],
                "effective_level": "project",
            },
        )
        phase = _phase(exit_criteria={"thresholds": {"go": 99}})

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        # Before S7b this replaced the project document wholesale, so a phase
        # setting one criterion silently discarded every threshold the project
        # had configured.
        assert result["policy"]["document"]["thresholds"] == {"go": 99, "no_go": 50}
        assert result["policy"]["sources"]["thresholds.go"] == "phase"
        assert result["policy"]["sources"]["thresholds.no_go"] == "project"

    def test_a_phase_without_criteria_inherits_the_project_policy(self, monkeypatch):
        _stub(
            monkeypatch,
            _rollup(**{f"t{i}": "PASSED" for i in range(9)}),
            policy={
                "document": {"thresholds": {"go": 90}},
                "sources": {"thresholds.go": "project"},
                "layers": ["project"],
                "effective_level": "project",
            },
        )
        phase = _phase(exit_criteria=None)

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), REL, phase.id, record=False)
        )

        assert result["policy"]["effective_level"] == "project"
        assert result["policy"]["document"]["thresholds"]["go"] == 90


class TestTheReleaseSummaryKeepsThreeOutcomesApart:
    def test_a_failing_phase_blocks(self):
        summary = gate.summarise_gate([
            {"phase_name": "QA", "verdict": "NO_GO"},
            {"phase_name": "UAT", "verdict": "GO"},
        ])

        assert summary["status"] == "BLOCKED"
        assert summary["failing_phases"] == ["QA"]

    def test_an_unevaluated_phase_is_incomplete_not_blocked(self):
        summary = gate.summarise_gate([
            {"phase_name": "QA", "verdict": "GO"},
            {"phase_name": "UAT", "verdict": "NOT_EVALUATED"},
        ])

        # A different action from BLOCKED: go run some tests, rather than go fix
        # some. Collapsing the two sends a release manager the wrong way half
        # the time.
        assert summary["status"] == "INCOMPLETE"
        assert summary["unevaluated_phases"] == ["UAT"]

    def test_an_unevaluated_phase_is_emphatically_not_ready(self):
        summary = gate.summarise_gate([{"phase_name": "QA", "verdict": "NOT_EVALUATED"}])

        # The vacuous pass, at the release level this time.
        assert summary["status"] != "READY"

    def test_failing_beats_unevaluated_when_both_are_present(self):
        summary = gate.summarise_gate([
            {"phase_name": "QA", "verdict": "NO_GO"},
            {"phase_name": "UAT", "verdict": "NOT_EVALUATED"},
        ])

        # A known failure is the more actionable fact, and reporting INCOMPLETE
        # would bury it.
        assert summary["status"] == "BLOCKED"
        assert summary["unevaluated_phases"] == ["UAT"]

    def test_all_phases_passing_is_ready(self):
        summary = gate.summarise_gate([
            {"phase_name": "QA", "verdict": "GO"},
            {"phase_name": "UAT", "verdict": "GO"},
        ])

        assert summary["status"] == "READY"

    def test_a_release_with_no_phases_is_neither_ready_nor_blocked(self):
        summary = gate.summarise_gate([])

        # Phases are optional, so this is not a failure — but nothing was
        # gated, so it is not a pass either.
        assert summary["status"] == "NO_PHASES"


class TestReadingIsNotRecording:
    def test_evaluating_every_phase_does_not_write_by_default(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}))

        async def _must_not_record(*a, **kw):  # pragma: no cover
            raise AssertionError("a routine read must not append to the audit trail")

        monkeypatch.setattr(gate.decisions, "record_decision", _must_not_record)
        phases = [_phase("QA", order=0), _phase("UAT", order=1)]

        results = asyncio.run(gate.evaluate_all_phases(_Session(phases[0], phases), REL))

        # Appending a row per phase per look would bury the real decisions in
        # noise, and the history is the point of that table.
        assert len(results) == 2
        assert all(r["recorded"] is False for r in results)


def _defect(severity, title, status="OPEN"):
    return SimpleNamespace(
        id=uuid.uuid4(), title=title, severity=severity, resolution_status=status,
        affects_releases=None, release_id=REL,
    )


class TestReleaseBlockersTravelBesideThePhaseVerdict:
    """D-113: the release gate merges open CRITICAL/HIGH defects; the phase gate
    did not, so an all-passing phase over an open HIGH read GO with nothing
    beside it."""

    def test_an_all_passing_phase_carries_the_open_high_defect(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}))
        phase = _phase("QA")
        db = _Session(phase, [phase], project_id=uuid.uuid4(),
                      defects=[_defect("HIGH", "Checkout double-charges"),
                               _defect("LOW", "Typo on receipt")])

        (result,) = asyncio.run(gate.evaluate_all_phases(db, REL))

        assert [b["title"] for b in result["release_blockers"]] == ["Checkout double-charges"]
        assert result["release_blockers"][0]["severity"] == "HIGH"
        # Scope decision: defects are release-scoped, so the phase verdict and
        # may_exit are unchanged.
        assert result["verdict"] == "GO"
        assert result["may_exit"] is True

    def test_no_open_blocking_defect_is_an_empty_list(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}))
        phase = _phase("QA")
        db = _Session(phase, [phase], project_id=uuid.uuid4(), defects=[])

        (result,) = asyncio.run(gate.evaluate_all_phases(db, REL))

        assert result["release_blockers"] == []

    def test_the_single_phase_evaluate_can_ask_for_them(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}))
        phase = _phase("QA")
        db = _Session(phase, [phase], project_id=uuid.uuid4(),
                      defects=[_defect("CRITICAL", "Data loss on save")])

        result = asyncio.run(gate.evaluate_phase(
            db, REL, phase.id, record=False, include_release_blockers=True
        ))

        assert [b["severity"] for b in result["release_blockers"]] == ["CRITICAL"]


class TestExitCriteriaAreSaidToBeNotes:
    """S-B-2: exit criteria are merged into the policy snapshot but ``decide``
    never reads them. The response must say so rather than imply a check."""

    def test_two_phases_with_different_criteria_get_the_same_verdict_and_say_why(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}, bad="FAILED"))
        strict = _phase("QA", exit_criteria={"thresholds": {"go": 99}})
        lax = _phase("UAT", exit_criteria={"thresholds": {"go": 10}})

        a = asyncio.run(gate.evaluate_phase(_Session(strict), REL, strict.id, record=False))
        b = asyncio.run(gate.evaluate_phase(_Session(lax), REL, lax.id, record=False))

        assert a["verdict"] == b["verdict"] == "NO_GO"
        assert a["exit_criteria_evaluated"] is False
        assert b["exit_criteria_evaluated"] is False
