"""Regression: the compliance pack carries the RELEASE-level verdict (S8).

The pack's own docstring promises an artifact that "fully reconstructs a release
decision". Until this slice it reconstructed a RUN's decision that happened to be
linked to the release — ``ReleaseDecision`` is keyed ``test_run_id``, so the join
through ``ReleaseTestRunLink`` finds one run's verdict, not the release's.

"Is 2.4.0 shippable?" was the single question the compliance pack could not
answer, which matters more here than anywhere else in the product: this is the
artifact that LEAVES the tool and gets attached to an audit or a go/no-go
thread. Every other surface is a screen somebody can re-check.

Two properties guarded
----------------------
**The history travels, not just the standing verdict.** ``ReleaseGateDecision``
is append-only precisely so "what did we decide, on what evidence, under which
policy" survives a re-evaluation. A pack carrying only the current verdict could
not show it had ever been anything else — which is the first thing an auditor
asks.

**A never-evaluated release says so explicitly.** An omitted section reads as
"this product has no such concept"; a present one saying ``evaluated: false`` is
a fact a reviewer can act on. Same reasoning as NOT_EVALUATED being a real
verdict rather than an absence.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

from app.services import compliance_pack_service as pack


def _decision(verdict="GO", denominator=40, evidence=38):
    """A REAL ReleaseGateDecision, not a stand-in.

    ``_to_jsonable`` serialises SQLAlchemy rows by walking
    ``__table__.columns``; a SimpleNamespace has no such attribute and falls
    through to ``str()``, so a fake would have made these tests assert against
    a string while the production path produced a dict. The double has to have
    the shape the code actually navigates.
    """
    from app.models.postgres import ReleaseGateDecision

    return ReleaseGateDecision(
        id=uuid.uuid4(),
        release_id=uuid.uuid4(),
        verdict=verdict,
        denominator=denominator,
        evidence_count=evidence,
        run_ids=["r1", "r2"],
        status_rollup={"PASSED": 37, "FAILED": 1},
        attribution_mix={"explicit_name": 2},
        policy_snapshot={"thresholds": {"go": 90}},
        blocking_reasons=[],
        is_current=True,
    )


def _gather(monkeypatch, current, history):
    from app.services import release_gate_decision_service as gd

    async def _current(db, rid, **kw):
        return current

    async def _history(db, rid, **kw):
        return history

    monkeypatch.setattr(gd, "current_decision", _current)
    monkeypatch.setattr(gd, "decision_history", _history)
    return asyncio.run(pack._gather_release_gate(object(), uuid.uuid4()))


class TestTheReleaseVerdictIsInThePack:
    def test_the_current_verdict_is_captured(self, monkeypatch):
        current = _decision(verdict="NO_GO")

        result = _gather(monkeypatch, current, [current])

        assert result["evaluated"] is True
        assert result["current"]["verdict"] == "NO_GO"

    def test_the_snapshot_numbers_travel_with_it(self, monkeypatch):
        current = _decision(denominator=40, evidence=38)

        result = _gather(monkeypatch, current, [current])

        # A verdict without its denominator is unreadable months later: "NO_GO"
        # over two tests is not the claim "NO_GO" over four thousand is.
        assert result["current"]["denominator"] == 40
        assert result["current"]["evidence_count"] == 38
        assert result["current"]["policy_snapshot"] == {"thresholds": {"go": 90}}

    def test_the_full_history_travels_not_just_the_standing_verdict(self, monkeypatch):
        superseded = _decision(verdict="NO_GO")
        current = _decision(verdict="GO")

        result = _gather(monkeypatch, current, [current, superseded])

        # The point of an append-only table. A pack carrying only the current
        # verdict cannot show it was ever anything else — the first thing an
        # auditor asks.
        assert len(result["history"]) == 2
        assert {h["verdict"] for h in result["history"]} == {"GO", "NO_GO"}


class TestANeverEvaluatedReleaseSaysSo:
    def test_it_reports_evaluated_false_rather_than_omitting_the_section(self, monkeypatch):
        result = _gather(monkeypatch, None, [])

        # An omitted section reads as "this product has no such concept". A
        # present one saying so is a fact a reviewer can act on.
        assert result["evaluated"] is False
        assert "no gate decision" in result["reason"]

    def test_history_is_still_present_and_empty_rather_than_absent(self, monkeypatch):
        result = _gather(monkeypatch, None, [])

        # An absent key and an empty list read differently to a consumer
        # walking the JSON.
        assert result["history"] == []


class TestThePackWiresItIn:
    def test_the_file_is_written_into_the_zip(self):
        src = inspect.getsource(pack)

        assert '"release_gate_decision.json": _serialize(release_gate),' in src

    def test_it_is_gathered_as_a_core_snapshot_not_best_effort(self):
        src = inspect.getsource(pack)

        # The core snapshots are deliberately NOT exception-swallowed: a
        # compliance pack must be complete, because a reviewer trusts it as
        # authoritative. A silently-missing verdict is worse than a failed
        # build.
        assert "release_gate = await _gather_release_gate(db, release.id)" in src
        gather = inspect.getsource(pack._gather_release_gate)
        assert "except Exception" not in gather

    def test_the_readme_lists_it(self):
        src = inspect.getsource(pack._build_readme)

        # The pack's README IS its index. A file present in the ZIP but absent
        # from that table is undiscoverable to the person the pack exists for.
        assert "release_gate_decision.json" in src

    def test_the_run_level_decision_is_still_carried(self):
        src = inspect.getsource(pack)

        # Both are kept. The run decision explains one execution; the release
        # gate explains the shipping call. Replacing one with the other would
        # lose evidence rather than add it.
        assert '"decision.json": _serialize(decision_snapshot),' in src
