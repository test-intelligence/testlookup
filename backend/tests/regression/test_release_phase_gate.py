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

from app.services import release_phase_gate_service as gate
from app.services.release_rollup_service import ReleaseRollup, STATUSES


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
    def __init__(self, phase=None, phases=None):
        self._phase = phase
        self._phases = phases or []

    async def execute(self, stmt, *a, **kw):
        sql = " ".join(str(stmt).split())
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
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
        )

        # The whole point. "No failures" off zero runs is an approval for work
        # nobody did, and it looks identical to a real pass.
        assert result["verdict"] == "NOT_EVALUATED"
        assert result["may_exit"] is False

    def test_a_phase_where_everything_was_skipped_is_not_evaluated(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "SKIPPED" for i in range(40)}))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
        )

        # 40 tests, none run. A gate keyed on "nothing failed" says GO.
        assert result["verdict"] == "NOT_EVALUATED"
        assert result["may_exit"] is False

    def test_a_thin_phase_is_not_evaluated(self, monkeypatch):
        _stub(monkeypatch, _rollup(a="PASSED", b="PASSED"))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
        )

        assert result["verdict"] == "NOT_EVALUATED"
        assert result["scorecard"]["measured"] is False

    def test_a_missing_phase_is_not_evaluated_rather_than_passed(self, monkeypatch):
        _stub(monkeypatch, _rollup())

        result = asyncio.run(
            gate.evaluate_phase(_Session(None), "rel", uuid.uuid4(), record=False)
        )

        # An id that resolves to nothing must not fall through to a GO.
        assert result["verdict"] == "NOT_EVALUATED"
        assert result["recorded"] is False


class TestAPopulatedPhaseDecidesNormally:
    def test_enough_evidence_and_no_failures_may_exit(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
        )

        assert result["verdict"] == "GO"
        assert result["may_exit"] is True

    def test_a_failure_blocks_the_exit(self, monkeypatch):
        _stub(monkeypatch, _rollup(**{f"t{i}": "PASSED" for i in range(9)}, bad="FAILED"))
        phase = _phase()

        result = asyncio.run(
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
        )

        assert result["verdict"] == "NO_GO"
        assert result["may_exit"] is False
        assert result["blocking_reasons"]


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
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
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
            gate.evaluate_phase(_Session(phase), "rel", phase.id, record=False)
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

        results = asyncio.run(gate.evaluate_all_phases(_Session(phases[0], phases), "rel"))

        # Appending a row per phase per look would bury the real decisions in
        # noise, and the history is the point of that table.
        assert len(results) == 2
        assert all(r["recorded"] is False for r in results)
