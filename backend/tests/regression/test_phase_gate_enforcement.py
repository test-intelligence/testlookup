"""Regression: the phase gate refuses, and records who went around it (S6b).

W4 made the gate ANSWERABLE — `GET /releases/{id}/phases/gate` — and obliged
nobody: ``update_phase`` marked a phase completed without ever consulting it,
and ``status="skipped"`` counted as done in the all-phases aggregate exactly
like ``completed``. A release could reach "all phases done" having gated none of
them.

Enforcement was held back because it is a breaking change to a live endpoint and
the epic specified it behind a feature flag that did not exist — no release flag
existed at all. Migration 0158 seeds ``release_phase_gate_enforcement``,
disabled, so a deployment that does nothing sees no change.

Three properties, and the third is what stops the gate being routed around
--------------------------------------------------------------------------
1. Off by default, and fails to off. An enforcement gate that starts refusing
   because Redis blinked blocks releases for a reason nobody can see.
2. On, a non-GO verdict refuses — carrying the gate's OWN blocking reasons, so
   the caller is told what to fix rather than merely that they may not proceed.
3. An override is always available and always audited. A gate with no override
   is one a release manager routes around by marking the phase "skipped"
   instead, and then the gate has achieved nothing except a worse audit trail.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

from app.routers.releases import PhaseUpdate  # noqa: E402
from app.services import release_service as svc  # noqa: E402

RELEASE = uuid.uuid4()
PROJECT = uuid.uuid4()


class _Phase:
    def __init__(self, status="in_progress"):
        self.id = uuid.uuid4()
        self.name = "QA"
        self.status = status


class _Session:
    """Answers the project lookup and the all_done COUNT; records audit rows."""

    def __init__(self, incomplete=0):
        self._incomplete = incomplete
        self.added: list = []

    async def execute(self, stmt=None, *a, **kw):
        project, incomplete = PROJECT, self._incomplete

        class _R:
            def scalar(self_inner):
                # The project lookup uses scalar_one_or_none; the COUNT uses
                # scalar. Both are served, so the fake cannot pass by answering
                # only the one the test happens to reach first.
                return incomplete

            def scalar_one_or_none(self_inner):
                return project

            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return []

        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="qa.lead")


def _patch(mp, *, enabled: bool, verdict: str = "GO", reasons=None):
    async def _flag():
        return enabled

    async def _evaluate(db, release_id, phase_id, record=True, created_by_id=None):
        return {"verdict": verdict, "blocking_reasons": reasons or []}

    mp.setattr(svc, "_enforcement_enabled", _flag)

    from app.services import release_phase_gate_service

    mp.setattr(release_phase_gate_service, "evaluate_phase", _evaluate)

    async def _get_phase(db, release_id, phase_id):
        return _get_phase.phase

    mp.setattr(svc, "get_phase_or_404", _get_phase)
    return _get_phase


# ── Off by default ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_the_flag_off_a_phase_completes_exactly_as_before():
    """The whole reason the flag exists.

    A deployment that has not opted in must see no behaviour change at all —
    including for a phase whose gate would say NO_GO.
    """
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=False, verdict="NO_GO", reasons=["ui::t1 FAILED"])
        gp.phase = _Phase()
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="completed"), actor=_actor(),
        )
    assert phase.status == "completed"
    assert db.added == [], "an audit row was written with enforcement off"


@pytest.mark.asyncio
async def test_a_flag_lookup_failure_does_not_block_a_release():
    """Fails to OFF, deliberately.

    A gate that starts refusing because the flag store is unreachable blocks
    shipping for a reason nobody can see or fix from the UI.
    """
    async def _boom():
        raise RuntimeError("redis down")

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=False)
        gp.phase = _Phase()
        # Restore the REAL helper, then break what it depends on.
        mp.setattr(svc, "_enforcement_enabled", svc._enforcement_enabled)
        mp.setattr("app.services.feature_flags.is_enabled", _boom)
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="completed"), actor=_actor(),
        )
    assert phase.status == "completed"


# ── On: the gate refuses, and says what to fix ───────────────────────────────


@pytest.mark.asyncio
async def test_completing_a_phase_the_gate_blocks_is_refused():
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO", reasons=["ui::checkout is FAILED"])
        gp.phase = _Phase()
        with pytest.raises(HTTPException) as exc:
            await svc.update_phase(
                db, str(RELEASE), str(gp.phase.id),
                PhaseUpdate(status="completed"), actor=_actor(),
            )

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "NO_GO" in detail
    assert "checkout" in detail, (
        "the refusal must carry the gate's OWN reasons — otherwise the caller "
        "is told they may not proceed without being told what to fix"
    )
    assert gp.phase.status == "in_progress", "the row was mutated despite the refusal"


@pytest.mark.asyncio
async def test_an_unevaluated_phase_is_also_refused():
    """NOT_EVALUATED is not a pass.

    A phase nobody ran is the case the evidence floor exists for, and treating
    absence of evidence as approval is how a gate hands out passes for work that
    never happened.
    """
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NOT_EVALUATED", reasons=[])
        gp.phase = _Phase()
        with pytest.raises(HTTPException) as exc:
            await svc.update_phase(
                db, str(RELEASE), str(gp.phase.id),
                PhaseUpdate(status="completed"), actor=_actor(),
            )
    assert exc.value.status_code == 409
    assert "could not evaluate" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_passing_gate_completes_without_ceremony():
    """The control. Enforcement that refused a GO would be caught by nothing
    else here, and would make the feature unusable."""
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="GO")
        gp.phase = _Phase()
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="completed"), actor=_actor(),
        )
    assert phase.status == "completed"
    assert db.added == [], "a clean pass should not write an override audit row"


# ── The override, and the audit that makes it honest ─────────────────────────


@pytest.mark.asyncio
async def test_an_override_completes_the_phase_and_is_recorded():
    db = _Session()
    actor = _actor()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO", reasons=["ui::checkout is FAILED"])
        gp.phase = _Phase()
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="completed", gate_override_reason="hotfix, risk accepted by QA lead"),
            actor=actor,
        )

    assert phase.status == "completed"
    assert len(db.added) == 1, "the override was not recorded"
    row = db.added[0]
    assert row.action == "release.phase_gate_overridden"
    assert row.actor_user_id == actor.id, (
        "an override recorded against nobody answers 'what' and not 'who'"
    )
    assert row.after_value["reason"] == "hotfix, risk accepted by QA lead"
    assert row.after_value["verdict"] == "NO_GO"
    assert row.after_value["blocking_reasons"] == ["ui::checkout is FAILED"], (
        "the audit row must preserve WHAT the gate objected to, since the "
        "verdict is recomputed from data that will have moved on"
    )


@pytest.mark.asyncio
async def test_the_override_reason_is_not_written_onto_the_phase():
    """It is a control field, not a column. Writing it would fail on a model
    that has no such attribute, and would put an audit trail in a mutable row."""
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO")
        gp.phase = _Phase()
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="completed", gate_override_reason="risk accepted"),
            actor=_actor(),
        )
    assert not hasattr(phase, "gate_override_reason")


# ── The quieter route past the gate ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_skipping_a_phase_without_a_reason_is_refused():
    """The second, silent route. ``skipped`` counts as done in the all-phases
    aggregate exactly like ``completed``, so an unexplained skip is
    indistinguishable from work that passed."""
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO")
        gp.phase = _Phase()
        with pytest.raises(HTTPException) as exc:
            await svc.update_phase(
                db, str(RELEASE), str(gp.phase.id),
                PhaseUpdate(status="skipped"), actor=_actor(),
            )
    assert exc.value.status_code == 409
    assert "reason" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_skip_with_a_reason_is_allowed_and_recorded():
    """Allowed, deliberately. A skip is a legitimate decision not to test
    something, and forbidding it would push people to lie about status —
    strictly worse than an honest recorded skip."""
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO")
        gp.phase = _Phase()
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="skipped", skip_reason="vendor environment unavailable"),
            actor=_actor(),
        )
    assert phase.status == "skipped"
    assert len(db.added) == 1
    assert db.added[0].action == "release.phase_skipped"
    assert db.added[0].after_value["reason"] == "vendor environment unavailable"


@pytest.mark.asyncio
async def test_a_skip_does_not_evaluate_the_gate():
    """A skip is not a claim that the phase passed, so asking the gate would be
    asking the wrong question — and would refuse a legitimate skip on a phase
    nobody ran."""
    called = False

    async def _evaluate(*a, **kw):
        nonlocal called
        called = True
        return {"verdict": "NO_GO", "blocking_reasons": []}

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True)
        gp.phase = _Phase()
        from app.services import release_phase_gate_service

        mp.setattr(release_phase_gate_service, "evaluate_phase", _evaluate)
        await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="skipped", skip_reason="not applicable this cycle"),
            actor=_actor(),
        )
    assert not called


# ── Transitions, not re-submissions ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_resubmitting_an_existing_status_is_not_gated():
    """A form that PUTs its whole payload back must not be refused for
    including the status the phase already has. Gating a non-transition would
    make an already-completed phase permanently uneditable."""
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO")
        gp.phase = _Phase(status="completed")
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="completed", notes="typo fix"), actor=_actor(),
        )
    assert phase.status == "completed"
    assert db.added == []


@pytest.mark.asyncio
async def test_other_status_changes_are_not_gated():
    """Only the statuses that END a phase are gated. Moving to in_progress is
    the opposite of claiming the work is done."""
    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        gp = _patch(mp, enabled=True, verdict="NO_GO")
        gp.phase = _Phase(status="pending")
        phase, _ = await svc.update_phase(
            db, str(RELEASE), str(gp.phase.id),
            PhaseUpdate(status="in_progress"), actor=_actor(),
        )
    assert phase.status == "in_progress"
