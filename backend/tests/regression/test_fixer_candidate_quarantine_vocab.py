"""FIX-002 — the Fixer could never select a candidate.

``select_candidates`` filtered ``flaky_quarantine_requests.status`` with
hand-written lowercase literals (``"quarantined"``, ``"recheck_scheduled"``,
``"re_quarantined"``) while the column stores the UPPERCASE
``FlakyQuarantineStatus`` values. The IN clause matched nothing, ever: every
fixer run logged ``candidates=0 ... status=completed error=0``, so a live
QUARANTINED row and an empty project were indistinguishable to an operator.

Two guards, because the bug had two halves:

1. *Vocabulary* — the filter's values must be a SUBSET of the column's enum,
   and must equal the active set the quarantine service enforces elsewhere.
   ``recheck_scheduled`` / ``re_quarantined`` were not merely mis-cased; as
   written they were not stored values at all.
2. *Behavior* — the statement ``select_candidates`` actually issues must match
   a row whose status is what the DB stores, and must NOT match a released one.
   The SQL is exercised through a fake session that filters in-memory rows by
   the compiled bind parameters, so the assertion is on the query the DB would
   run rather than on the constant it was built from.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer import pipeline  # noqa: E402
from app.models.postgres import (  # noqa: E402
    FlakyQuarantineRequest,
    FlakyQuarantineStatus,
)
from app.services import flaky_quarantine_service  # noqa: E402

PROJECT_ID = uuid.uuid4()


# ── 1. Vocabulary ────────────────────────────────────────────────────────────


def test_active_quarantine_states_are_real_enum_values():
    """Every filter value is a value of the column's enum.

    This is the assertion that fails on the original tuple: ``"quarantined"``
    is not a ``FlakyQuarantineStatus`` value — ``"QUARANTINED"`` is.
    """
    vocabulary = {s.value for s in FlakyQuarantineStatus}
    unknown = [v for v in pipeline._ACTIVE_QUARANTINE_STATES if v not in vocabulary]
    assert not unknown, (
        f"fixer candidate filter uses status values that the column can never "
        f"hold: {unknown}. Known values: {sorted(vocabulary)}"
    )


def test_fixer_active_states_match_the_quarantine_service():
    """The pipeline docstring claims it mirrors the quarantine service — hold
    it to that, so a future lifecycle change cannot drift the two apart."""
    assert set(pipeline._ACTIVE_QUARANTINE_STATES) == set(
        flaky_quarantine_service._ACTIVE_QUARANTINE_STATES
    )


# ── 2. Behavior ──────────────────────────────────────────────────────────────


class _Rows:
    """Minimal scalars() result over a pre-filtered row list."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FilteringSession:
    """Fake session that answers the candidate query by applying the compiled
    ``status IN (...)`` bind parameters to ``rows`` — i.e. it matches the way
    Postgres would, using the literals the statement actually carries."""

    def __init__(self, rows):
        self._rows = rows
        self.status_filter: list[str] = []

    async def execute(self, stmt):
        params = stmt.compile().params
        status_values = [
            v for k, v in params.items()
            if k.startswith("status_") and isinstance(v, (list, tuple))
        ]
        if not status_values:
            # The second query in select_candidates is the prior-attempt
            # GROUP BY — no attempts in this fixture.
            return _Rows([])
        wanted = set(status_values[0])
        self.status_filter = sorted(wanted)
        return _Rows([r for r in self._rows if r.status in wanted])


def _quarantine_row(status: str, *, fingerprint: str, flip_rate: float):
    return FlakyQuarantineRequest(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        test_fingerprint=fingerprint,
        test_name=f"test_{fingerprint}",
        suite_name="suite",
        status=status,
        flip_rate=flip_rate,
        flip_window_size=10,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        FlakyQuarantineStatus.QUARANTINED.value,
        FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
        FlakyQuarantineStatus.RE_QUARANTINED.value,
    ],
)
async def test_active_quarantine_row_is_selected(status):
    """A row in each active state — stored exactly as the DB stores it — is
    selected. Pre-fix this returned [] for all three."""
    db = _FilteringSession([_quarantine_row(status, fingerprint="781e3b39d9ed", flip_rate=0.6)])

    candidates = await pipeline.select_candidates(db, PROJECT_ID)

    assert [c.test_fingerprint for c in candidates] == ["781e3b39d9ed"]
    assert candidates[0].prior_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        FlakyQuarantineStatus.PROPOSED.value,
        FlakyQuarantineStatus.APPROVED.value,
        FlakyQuarantineStatus.RELEASED.value,
        FlakyQuarantineStatus.REJECTED.value,
    ],
)
async def test_inactive_quarantine_row_is_not_selected(status):
    """The fix must not widen the selector: un-reviewed proposals, staged
    approvals, and closed rows are still not fixer candidates."""
    db = _FilteringSession([_quarantine_row(status, fingerprint="deadbeef", flip_rate=0.9)])

    assert await pipeline.select_candidates(db, PROJECT_ID) == []


@pytest.mark.asyncio
async def test_selection_query_carries_stored_casing():
    """Pin the literals the statement hands the database."""
    db = _FilteringSession([])

    await pipeline.select_candidates(db, PROJECT_ID)

    assert db.status_filter == sorted(
        (
            FlakyQuarantineStatus.QUARANTINED.value,
            FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
            FlakyQuarantineStatus.RE_QUARANTINED.value,
        )
    )
