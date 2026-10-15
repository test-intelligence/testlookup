"""W05 REL-01 / REL-02: the release payloads carry ``gate`` and ``owner``.

The release page renders a card from ``GET /releases`` and a release's own page
from ``GET /releases/{id}`` ALONE (``/releases/:id`` never sees the list), so
both must carry the CURRENT recorded verdict and the owner themselves; the
create response carries the owner the create resolved and ``gate: null``.
``GET /releases/{id}/gate`` keeps its raw reasons and adds display strings
beside them.

The SQL itself (current release-level row only, constant statement count, the
20-reason cap) is pinned against a real PostgreSQL by G-A's
``tests/integration/test_release_gate_summary_postgres.py`` and by
``tests/integration/test_releases_list_gate_owner_postgres.py``. These are the
wiring, runnable without a database:

* each list item gets ITS release's summary and owner, ``None`` when absent,
  from ONE batch call each, and an empty page makes neither call (the
  ``_CapturingDB`` fakes of the list tests answer nothing else);
* the detail and the create response carry both keys;
* ``blocking_reasons_display`` is present only when there is a reason, capped
  at ``GATE_DETAIL_REASON_CAP``, and costs no call otherwise.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.models.postgres import Release  # noqa: E402
from app.routers import releases as router_mod  # noqa: E402
from app.services import release_gate_service, release_owner_service  # noqa: E402
from app.services import release_service  # noqa: E402

PROJECT = uuid.uuid4()


def _summary(verdict="NO_GO"):
    return {
        "verdict": verdict, "decided_at": "2026-10-15T08:00:00+00:00", "pass_rate": 80.0,
        "denominator": 12, "evidence_count": 10, "run_count": 3,
        "status_counts": {"PASSED": 8, "FAILED": 2}, "measured": True,
        "insufficient_reason": None, "blocking_reasons": ["test_pay (checkout-e2e) is FAILED"],
        "blocking_total": 1,
    }


def _release(name, *, owner=None):
    rel = Release(id=uuid.uuid4(), project_id=PROJECT, name=name, status="in_progress",
                  owner_user_id=owner)
    rel.phases = []
    return rel


def _result(*, scalars=None, rows=None):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: scalars or []),
        fetchall=lambda: rows or [],
    )


def _must_not_run(name):
    async def _raise(*_a, **_kw):
        raise AssertionError(f"{name} ran")

    return _raise


# ── GET /releases ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_list_item_carries_its_own_gate_and_owner(monkeypatch):
    priya = uuid.uuid4()
    judged, never = _release("2026.11", owner=priya), _release("2026.12")
    gates = AsyncMock(return_value={str(judged.id): _summary()})
    owners = AsyncMock(return_value={priya: {"id": str(priya), "name": "Priya Nair"}})
    monkeypatch.setattr(release_gate_service, "current_gate_summaries", gates)
    monkeypatch.setattr(release_owner_service, "owners_by_id", owners)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _result(scalars=[judged, never]),
        _result(rows=[(str(judged.id), 4)]),
    ]))

    body = await release_service.list_releases(db, str(PROJECT))

    items = {item["id"]: item for item in body["items"]}
    assert items[str(judged.id)]["gate"] == _summary()
    assert items[str(never.id)]["gate"] is None, "a never-evaluated release must read null"
    assert items[str(judged.id)]["owner"] == {"id": str(priya), "name": "Priya Nair"}
    assert items[str(never.id)]["owner"] is None
    assert items[str(judged.id)]["test_run_count"] == 4
    # ONE batch call each for the whole page.
    gates.assert_awaited_once_with(db, [judged.id, never.id])
    owners.assert_awaited_once_with(db, [priya, None])
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_an_empty_page_spends_no_batch_read(monkeypatch):
    monkeypatch.setattr(release_gate_service, "current_gate_summaries", _must_not_run("gate batch"))
    monkeypatch.setattr(release_owner_service, "owners_by_id", _must_not_run("owner batch"))
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_result(scalars=[])]))

    assert await release_service.list_releases(db, str(PROJECT)) == {"items": [], "total": 0}
    assert db.execute.await_count == 1


# ── GET /releases/{id} ───────────────────────────────────────────────────────


def _detail_db(release):
    aggregate = SimpleNamespace(_mapping={
        "total_runs": 0, "total_tests": None, "total_passed": None,
        "total_failed": None, "avg_pass_rate": None,
    })
    return SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: release),
        _result(scalars=[]),
        SimpleNamespace(one=lambda: aggregate),
    ]))


@pytest.mark.asyncio
async def test_the_detail_carries_the_gate_and_the_owner(monkeypatch):
    priya = uuid.uuid4()
    rel = _release("2026.11", owner=priya)
    rel.test_run_links = []
    gates = AsyncMock(return_value={str(rel.id): _summary("GO")})
    monkeypatch.setattr(release_gate_service, "current_gate_summaries", gates)
    monkeypatch.setattr(release_owner_service, "owners_by_id",
                        AsyncMock(return_value={priya: {"id": str(priya), "name": "Priya Nair"}}))
    db = _detail_db(rel)

    detail = await release_service.get_release_details(db, str(rel.id))

    assert detail["gate"]["verdict"] == "GO"
    assert detail["owner"] == {"id": str(priya), "name": "Priya Nair"}
    gates.assert_awaited_once_with(db, [rel.id])


@pytest.mark.asyncio
async def test_a_robot_owner_reads_as_no_owner_on_the_detail(monkeypatch):
    """``owners_by_id`` leaves an ineligible owner out; the detail must not fall
    back to the raw id or a placeholder name."""
    rel = _release("2026.11", owner=uuid.uuid4())
    rel.test_run_links = []
    monkeypatch.setattr(release_gate_service, "current_gate_summaries", AsyncMock(return_value={}))
    monkeypatch.setattr(release_owner_service, "owners_by_id", AsyncMock(return_value={}))

    detail = await release_service.get_release_details(_detail_db(rel), str(rel.id))

    assert detail["owner"] is None
    assert detail["owner_user_id"] == str(rel.owner_user_id), "the column itself is still served"


# ── POST /releases (the create response) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_create_response_carries_the_owner_and_a_null_gate(monkeypatch):
    lead = uuid.uuid4()
    rel = _release("2026.11", owner=lead)
    monkeypatch.setattr(release_gate_service, "current_gate_summaries",
                        _must_not_run("a gate read for a release created a moment ago"))
    monkeypatch.setattr(release_owner_service, "owners_by_id",
                        AsyncMock(return_value={lead: {"id": str(lead), "name": "Lead"}}))
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one=lambda: rel),
        _result(scalars=[]),
    ]))

    data = await release_service.serialize_created_release(db, rel)

    assert data["gate"] is None
    assert data["owner"] == {"id": str(lead), "name": "Lead"}


# ── GET /releases/{id}/gate ──────────────────────────────────────────────────


def _gate(reasons):
    return {
        "release_id": "x", "verdict": "NO_GO" if reasons else "GO", "decided_at": None,
        "blocking_reasons": list(reasons), "blocking_total": len(reasons),
        "scorecard": {"denominator": 1, "evidence_count": 1}, "from_snapshot": True,
    }


@pytest.mark.asyncio
async def test_display_reasons_sit_beside_the_raw_ones_capped(monkeypatch):
    release_id = uuid.uuid4()
    raw = [f"checkout-e2e::{i:016x} is FAILED" for i in range(250)]
    monkeypatch.setattr(router_mod.release_gate_service, "current_gate", AsyncMock(return_value=_gate(raw)))
    shown = [f"test_{i} (checkout-e2e) is FAILED" for i in range(release_gate_service.GATE_DETAIL_REASON_CAP)]
    display = AsyncMock(return_value=shown)
    monkeypatch.setattr(router_mod.release_gate_reasons, "display_reasons_for_release", display)
    db = object()

    gate = await router_mod.get_release_gate(release_id=str(release_id), db=db, _=None)

    assert gate["blocking_reasons"] == raw, "the stored reasons are the record and stay untouched"
    assert gate["blocking_reasons_display"] == shown
    display.assert_awaited_once_with(db, release_id, raw[: release_gate_service.GATE_DETAIL_REASON_CAP])
    assert release_gate_service.GATE_DETAIL_REASON_CAP == 200


@pytest.mark.asyncio
async def test_a_verdict_with_no_reason_spends_nothing_and_adds_no_key(monkeypatch):
    monkeypatch.setattr(router_mod.release_gate_service, "current_gate", AsyncMock(return_value=_gate([])))
    monkeypatch.setattr(router_mod.release_gate_reasons, "display_reasons_for_release",
                        _must_not_run("the display lookup"))

    gate = await router_mod.get_release_gate(release_id=str(uuid.uuid4()), db=object(), _=None)

    assert "blocking_reasons_display" not in gate


@pytest.mark.asyncio
async def test_a_never_evaluated_release_is_still_a_404(monkeypatch):
    monkeypatch.setattr(router_mod.release_gate_service, "current_gate", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc:
        await router_mod.get_release_gate(release_id=str(uuid.uuid4()), db=object(), _=None)

    assert (exc.value.status_code, exc.value.detail) == (404, "This release has not been evaluated yet")
