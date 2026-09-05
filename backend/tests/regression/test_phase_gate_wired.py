"""Regression: the phase gate is reachable from a request (W4).

``release_phase_gate_service`` had no router and no caller.
``policy_resolution``'s only importer was that dead module — so the pair looked
wired from the inside while no request could reach either, which is exactly why
"does anything import this" is the wrong question and the reachability guard
asks about routers instead.

The service's own behaviour is covered by ``test_release_phase_gate.py``: the
evidence floor, the three-valued summary, the key-wise criteria merge. This file
covers only what the wiring adds, so the two do not drift into testing the same
thing twice.

Scope, stated in the tests as well as the code
-----------------------------------------------
This makes the gate ANSWERABLE, not ENFORCING. ``update_phase`` still lets a QA
lead mark a phase completed without consulting it. Turning that into a refusal
is a breaking change to a live endpoint, and the epic specified it behind a
feature flag that does not exist — no release feature flag exists at all. The
one part of S6b that is safe without a flag ships here, and the last test says
plainly what has NOT shipped, so nobody reads this file as evidence the gate is
enforced.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

from app.routers import releases as router_mod  # noqa: E402
from app.services import release_phase_gate_service as gate_svc  # noqa: E402

RELEASE = uuid.uuid4()
PHASE = uuid.uuid4()


class _User:
    id = uuid.uuid4()


class _Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _phase_result(name, verdict):
    return {
        "release_id": str(RELEASE),
        "phase_id": str(uuid.uuid4()),
        "phase_name": name,
        "verdict": verdict,
        "blocking_reasons": [],
        "scorecard": None,
        "policy": None,
        "decision_id": None,
        "recorded": False,
        "may_exit": verdict == "GO",
    }


# ── The read endpoint reaches the service ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_gate_endpoint_returns_a_verdict_per_phase():
    async def _all(db, release_id, record=False):
        assert record is False
        return [_phase_result("QA", "GO"), _phase_result("UAT", "NOT_EVALUATED")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_svc, "evaluate_all_phases", _all)
        out = await router_mod.get_release_phase_gate(
            release_id=str(RELEASE), db=_Session(), _=_User()
        )

    assert len(out["phases"]) == 2
    assert out["status"] == "INCOMPLETE"
    assert out["unevaluated_phases"] == ["UAT"]


@pytest.mark.asyncio
async def test_reading_the_gate_does_not_append_to_the_audit_history():
    """Looking at a release is routine. A decision row per phase per look would
    bury the real decisions in noise, so recording is deliberate and lives on
    the POST."""
    seen = {}

    async def _all(db, release_id, record=False):
        seen["record"] = record
        return []

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_svc, "evaluate_all_phases", _all)
        await router_mod.get_release_phase_gate(
            release_id=str(RELEASE), db=db, _=_User()
        )

    assert seen["record"] is False
    assert db.commits == 0, "a read committed"


@pytest.mark.asyncio
async def test_a_release_with_no_phases_is_reported_as_such_not_as_ready():
    """Phases are optional, so having none is not a failure — but nothing was
    gated either, and calling that READY would hand out an approval for a gate
    that never ran."""
    async def _all(db, release_id, record=False):
        return []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_svc, "evaluate_all_phases", _all)
        out = await router_mod.get_release_phase_gate(
            release_id=str(RELEASE), db=_Session(), _=_User()
        )
    assert out["status"] == "NO_PHASES"


@pytest.mark.asyncio
async def test_a_failing_phase_outranks_an_unevaluated_one():
    """"Fix the tests" and "go run some" are different instructions, and a
    boolean sends a release manager to do the wrong one half the time."""
    async def _all(db, release_id, record=False):
        return [_phase_result("QA", "NO_GO"), _phase_result("UAT", "NOT_EVALUATED")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_svc, "evaluate_all_phases", _all)
        out = await router_mod.get_release_phase_gate(
            release_id=str(RELEASE), db=_Session(), _=_User()
        )
    assert out["status"] == "BLOCKED"
    assert out["failing_phases"] == ["QA"]


# ── The evaluate endpoint records, and commits ───────────────────────────────


@pytest.mark.asyncio
async def test_evaluating_one_phase_records_and_commits():
    seen = {}

    async def _one(db, release_id, phase_id, record=True, created_by_id=None):
        seen.update(record=record, created_by_id=created_by_id)
        return _phase_result("QA", "GO")

    db = _Session()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_svc, "evaluate_phase", _one)
        await router_mod.evaluate_release_phase_gate(
            release_id=str(RELEASE), phase_id=str(PHASE), record=True,
            db=db, current_user=_User(), __=_User(),
        )

    assert seen["record"] is True
    assert seen["created_by_id"] == _User.id, "the verdict has no author"
    assert db.commits == 1, "the service stages the row; the router must commit"


def test_recording_is_the_DEFAULT_for_an_explicit_evaluation():
    """Asking the gate to evaluate a phase is the deliberate act; the history
    is the point of that table.

    Checked on the DECLARED default rather than by calling the handler without
    the argument: calling a FastAPI handler directly hands back the ``Query``
    object, not its value, so an omitted argument would prove nothing here.
    Mutation testing found this gap — both behavioural tests below pass
    ``record`` explicitly, so flipping the default to False survived them.
    """
    import inspect

    param = inspect.signature(
        router_mod.evaluate_release_phase_gate
    ).parameters["record"]
    assert param.default.default is True, (
        "evaluating a phase must record by default — a caller that omits the "
        "flag is making a decision, not previewing one"
    )


@pytest.mark.asyncio
async def test_a_preview_does_not_record():
    seen = {}

    async def _one(db, release_id, phase_id, record=True, created_by_id=None):
        seen["record"] = record
        return _phase_result("QA", "GO")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_svc, "evaluate_phase", _one)
        await router_mod.evaluate_release_phase_gate(
            release_id=str(RELEASE), phase_id=str(PHASE), record=False,
            db=_Session(), current_user=_User(), __=_User(),
        )
    assert seen["record"] is False


# ── The one part of S6b that needs no flag ───────────────────────────────────


@pytest.mark.asyncio
async def test_criteria_and_status_cannot_change_in_one_request():
    """Changing what a phase must satisfy and declaring it done are two acts.

    Doing both at once makes the second unanswerable: the gate would be judged
    against criteria that were never in force while the work happened. Refused
    rather than ordered, because no ordering of the two is honest.
    """
    from app.services import release_service

    body = router_mod.PhaseUpdate(status="completed", exit_criteria={"min_pass_rate": 90})
    with pytest.raises(HTTPException) as exc:
        await release_service.update_phase(_Session(), str(RELEASE), str(PHASE), body)
    assert exc.value.status_code == 400
    assert "separate requests" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_each_on_its_own_is_still_allowed(monkeypatch):
    """The refusal must be about the COMBINATION. Blocking either alone would
    make exit criteria uneditable and phases uncompletable."""
    from app.services import release_service

    phase = type("P", (), {"name": "QA", "status": "pending", "exit_criteria": None})()

    async def _get(db, release_id, phase_id):
        return phase

    monkeypatch.setattr(release_service, "get_phase_or_404", _get)

    class _CountingSession(_Session):
        async def execute(self, *a, **kw):
            class _R:
                def scalar(self_inner):
                    return 0
            return _R()

    for body in (
        router_mod.PhaseUpdate(exit_criteria={"min_pass_rate": 90}),
        router_mod.PhaseUpdate(status="in_progress"),
    ):
        await release_service.update_phase(
            _CountingSession(), str(RELEASE), str(PHASE), body
        )


def test_the_gate_is_now_enforced_behind_a_flag():
    """Replaces the test that pinned enforcement as OUTSTANDING.

    That test asserted ``"evaluate_phase" not in inspect.getsource(update_phase)``
    and would have KEPT PASSING through this change, because the enforcement
    went into a helper rather than into ``update_phase`` itself. It did not
    catch its own subject moving — which is why it named its replacement in the
    failure message instead of relying on the assertion alone.

    What is pinned now: the gate is consulted, the consultation is governed by
    a flag, and the flag defaults to off. ``test_phase_gate_enforcement.py``
    covers the behaviour; this is the structural half.
    """
    import inspect

    from app.services import release_service

    enforcement = inspect.getsource(release_service._enforce_phase_gate)
    assert "evaluate_phase" in enforcement, (
        "completing a phase no longer consults the gate — S6b's enforcement "
        "has been removed or bypassed"
    )
    assert "_enforcement_enabled" in enforcement, (
        "the gate is consulted unconditionally; a breaking change to a live "
        "endpoint has to stay behind the flag it shipped with"
    )

    update = inspect.getsource(release_service.update_phase)
    assert "_enforce_phase_gate" in update, (
        "the enforcement helper exists but update_phase does not call it — "
        "the same 'shipped complete with no caller' shape this epic kept "
        "producing, one layer down"
    )

    flag = inspect.getsource(release_service._enforcement_enabled)
    assert "release_phase_gate_enforcement" in flag


def test_the_flag_is_seeded_disabled():
    """A deployment that does nothing must see no behaviour change.

    An enforcement flag that arrived enabled would start refusing phase
    completions on every existing deployment the moment the migration ran,
    which is precisely what holding this back was avoiding.
    """
    from pathlib import Path

    from app.services import release_service

    path = (
        Path(release_service.__file__).resolve().parents[2]
        / "migrations" / "versions" / "0158_phase_gate_enforcement_flag.py"
    )
    text = path.read_text(encoding="utf-8")
    assert "release_phase_gate_enforcement" in text
    assert "false, 100" in text, (
        "the flag is not seeded disabled — enabling enforcement must be a "
        "deliberate act, not a side effect of upgrading"
    )
