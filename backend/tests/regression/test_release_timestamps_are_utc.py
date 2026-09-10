"""Release timestamps must be timezone-aware UTC, whatever the host's clock says.

Re-audit finding M9. ``release_service`` stamped three timestamptz columns with
a bare ``datetime.now()``:

* ``Release.released_at``        -- when a release is marked released
* ``ReleasePhase.actual_start``  -- when a phase moves to in_progress
* ``ReleasePhase.actual_end``    -- when a phase is completed

Measured before fixing, because the consequence decides how it is described:
asyncpg does NOT reject a naive datetime for a timestamptz parameter -- it
treats it as UTC. On the homelab the pod's clock is UTC, so the stored values
were correct there by coincidence. On any host whose local time is not UTC (a
developer laptop, a server set to local time) the release and phase dates were
local wall-clock times labelled as UTC, shifted by the offset, with no error.

So this is a silent skew rather than a crash, and it is invisible to any test
that only checks the value is set -- which is what the existing coverage did.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _assert_utc(value, what: str) -> None:
    assert value is not None, f"{what} was not set"
    assert value.tzinfo is not None, (
        f"{what} is a naive datetime. asyncpg stores it as if it were UTC, so on "
        "any host whose clock is not UTC it is silently off by the local offset."
    )
    assert value.utcoffset() == timedelta(0), f"{what} is not in UTC: {value!r}"


# ── Release.released_at ──────────────────────────────────────────────────


def _release():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="ready",
        name="r1",
        released_at=None,
        version=None,
        is_active=False,
        project_id=uuid.uuid4(),
    )


def _phase_rows(*states):
    rows = [SimpleNamespace(id=uuid.uuid4(), name=f"p{i}", status=s) for i, s in enumerate(states)]
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result.scalars = MagicMock(return_value=scalars)
    return result


@pytest.mark.asyncio
async def test_released_at_is_stamped_in_utc(monkeypatch):
    from app.services import release_service

    release = _release()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_phase_rows("completed", "skipped"))

    async def _lookup(_db, _release_id):
        return release

    monkeypatch.setattr(release_service, "get_release_or_404", _lookup)

    body = SimpleNamespace(model_dump=lambda exclude_none: {"status": "released"})
    result = await release_service.update_release(db, str(release.id), body)

    _assert_utc(result.released_at, "Release.released_at")


# ── ReleasePhase.actual_start / actual_end ───────────────────────────────


@pytest.fixture
def phase_env(monkeypatch):
    from app.services import release_service

    phase = SimpleNamespace(
        status="pending", name="p1", actual_start=None, actual_end=None
    )

    async def _get_phase(_db, _release_id, _phase_id):
        return phase

    async def _no_gate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(release_service, "get_phase_or_404", _get_phase)
    monkeypatch.setattr(release_service, "_enforce_phase_gate", _no_gate)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar=lambda: 1))
    return SimpleNamespace(service=release_service, phase=phase, db=db)


async def _move(env, status: str, **extra):
    fields = {"status": status, **extra}
    body = SimpleNamespace(model_dump=lambda exclude_none: dict(fields))
    return await env.service.update_phase(
        env.db, str(uuid.uuid4()), str(uuid.uuid4()), body
    )


@pytest.mark.asyncio
async def test_actual_start_is_stamped_in_utc(phase_env):
    await _move(phase_env, "in_progress")
    _assert_utc(phase_env.phase.actual_start, "ReleasePhase.actual_start")


@pytest.mark.asyncio
async def test_actual_end_is_stamped_in_utc(phase_env):
    phase_env.phase.status = "in_progress"
    await _move(phase_env, "completed")
    _assert_utc(phase_env.phase.actual_end, "ReleasePhase.actual_end")


@pytest.mark.asyncio
async def test_a_caller_supplied_start_is_not_overwritten(phase_env):
    """Auto-stamping only fills a gap; it must never replace a real value."""
    supplied = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    await _move(phase_env, "in_progress", actual_start=supplied)
    assert phase_env.phase.actual_start == supplied


# ── The class, not the module ────────────────────────────────────────────


def _naive_now_calls(tree: ast.AST) -> list[int]:
    """Line numbers of ``datetime.now()`` with no tz, and any ``utcnow()``.

    AST rather than grep: two of the three textual hits a grep found after the
    fix were inside docstrings that DISCUSS ``datetime.now()``, and a guard that
    flagged those would be switched off rather than obeyed.
    """
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        owner = node.func.value
        is_datetime = (
            isinstance(owner, ast.Name) and owner.id == "datetime"
        ) or (
            isinstance(owner, ast.Attribute) and owner.attr == "datetime"
        )
        if not is_datetime:
            continue
        if attr == "utcnow":
            lines.append(node.lineno)
        elif attr == "now" and not node.args and not node.keywords:
            lines.append(node.lineno)
    return lines


def test_the_scan_can_see_the_pattern():
    """Guards the guard."""
    tree = ast.parse(
        "from datetime import datetime\n"
        "a = datetime.now()\n"
        "b = datetime.utcnow()\n"
        "c = datetime.now(timezone.utc)\n"
        '"""a docstring mentioning datetime.now() is not a call"""\n'
    )
    assert _naive_now_calls(tree) == [2, 3]


def test_no_naive_now_anywhere_in_the_app():
    """Every timestamp column in this schema is timestamptz.

    A naive ``now()`` is stored as though it were UTC, so it is only correct on
    hosts that happen to run in UTC. There were three in release_service; this
    stops the fourth.
    """
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for line in _naive_now_calls(tree):
            offenders.append(f"{path.relative_to(APP).as_posix()}:{line}")

    assert not offenders, (
        "naive datetime.now()/utcnow() in application code — stored as UTC "
        "regardless of the host clock, so wrong on any non-UTC host. Use "
        "datetime.now(timezone.utc):\n  " + "\n  ".join(offenders)
    )
