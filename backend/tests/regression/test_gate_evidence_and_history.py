"""Regression: what counts as evidence, and what survives a delete.

Two design-gate findings that share a theme — the gate answering confidently
about things it should not.

UNKNOWN was counted as evidence
--------------------------------
``EVIDENCE_STATUSES`` excluded SKIPPED with a stated reason ("a skipped test
tells you nothing about the code") and then included UNKNOWN with none.

UNKNOWN is not "ran with an indeterminate outcome". ``ingestion._coerce_status``
returns it for any status string the parser does not recognise, and the
per-case mapper defaults to it when a report omits status entirely — it is the
UNPARSEABLE bucket. Counting it meant a release whose report format nobody could
read still cleared ``MIN_EVIDENCE`` and received a verdict with
``measured: true``. ``_normalise`` maps every unrecognised status to UNKNOWN
too, so garbage propagated straight into the count.

``analytics_service`` already excluded UNKNOWN from all three of its buckets, so
the release gate and the coverage report had diverged on exactly this axis —
which is what the epic's never-written pin was for. It is here now.

Deleting a release erased its verdicts
---------------------------------------
``release_gate_decisions.release_id`` is ``ondelete=CASCADE``, so a plain
DELETE silently erased every GO / NO_GO ever recorded — from an append-only
table whose entire purpose is that history. The epic's rule was "archive a
decided release rather than delete it"; nothing enforced the first half, and
``archived`` was not even documented as a status, so the second half named a
state the model said did not exist.
"""
from __future__ import annotations

import re
import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

# Aliased: pytest tries to COLLECT any module-level class named Test*,
# and warns that it cannot because the enum has a constructor.
from app.models.postgres import TestStatus as Status  # noqa: E402
from app.services import release_rollup_service as rollup_svc  # noqa: E402
from app.services import release_service  # noqa: E402


# ── Evidence ─────────────────────────────────────────────────────────────────


def test_unknown_is_not_evidence():
    """The unparseable bucket cannot vouch for a release."""
    assert Status.UNKNOWN.value not in rollup_svc.EVIDENCE_STATUSES, (
        "an unreadable report would clear the evidence floor and earn a verdict"
    )


def test_skipped_is_still_not_evidence():
    """The exclusion that was already right — kept so a fix to one does not
    quietly widen the other."""
    assert Status.SKIPPED.value not in rollup_svc.EVIDENCE_STATUSES


def test_real_outcomes_are_still_evidence():
    """The control. An empty or over-narrowed set would satisfy both
    assertions above while making every release NOT_EVALUATED."""
    for status in (Status.PASSED, Status.FAILED, Status.BROKEN):
        assert status.value in rollup_svc.EVIDENCE_STATUSES


def test_a_release_of_only_unparseable_results_is_not_evaluated():
    """End to end, because the constant alone proves nothing about the verdict.

    Nine tests, all UNKNOWN, is what an unreadable report looks like. Before
    this it cleared MIN_EVIDENCE (5) and the gate answered as though it had
    measured something.
    """
    rollup = rollup_svc.ReleaseRollup(
        latest_by_test={f"suite::t{i}": "UNKNOWN" for i in range(9)}
    )
    rollup.status_counts = {s: 0 for s in rollup_svc.STATUSES}
    rollup.status_counts["UNKNOWN"] = 9

    verdict, reasons = rollup_svc.decide(rollup)
    assert verdict == "NOT_EVALUATED", (
        "a release whose every result was unparseable received a real verdict"
    )
    assert reasons
    assert rollup_svc.summarise(rollup)["measured"] is False


def test_the_rollup_and_coverage_agree_on_what_counts():
    """The pin the epic required and never got.

    ``coverage_stats`` buckets results as PASSED / FAILED+BROKEN / SKIPPED and
    divides by "evaluated = passed + failed + broken". The rollup's evidence
    set must name the same three, or the same test result is evidence in the
    release gate and nothing in the coverage report — which is precisely how
    the two drifted apart.
    """
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service.coverage_stats)
    # The evaluated denominator, read from the SQL rather than assumed.
    evaluated = set(
        re.findall(r"'(PASSED|FAILED|BROKEN|SKIPPED|UNKNOWN)'", src)
    )
    counted = {s for s in evaluated if s != "SKIPPED"}

    assert counted == set(rollup_svc.EVIDENCE_STATUSES), (
        f"coverage counts {sorted(counted)} as evaluated while the release "
        f"gate counts {sorted(rollup_svc.EVIDENCE_STATUSES)} as evidence — the "
        "same result means different things on two surfaces"
    )


# ── A decided release keeps its decisions ────────────────────────────────────


class _Rel:
    def __init__(self):
        self.id = uuid.uuid4()
        self.is_active = False


class _Session:
    def __init__(self, decisions: int, outcomes: int = 0):
        self._decisions = decisions
        self._outcomes = outcomes
        self._rel = _Rel()
        self.deleted: list = []

    async def execute(self, stmt=None, *a, **kw):
        rel = self._rel
        count = self._outcomes if "release_outcomes" in str(stmt) else self._decisions

        class _R:
            def scalar_one_or_none(self_inner):
                return rel

            def scalar(self_inner):
                return count

            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return []

        return _R()

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_deleting_a_decided_release_is_refused_with_a_way_forward():
    """A 409 that names the alternative, not a bare refusal.

    The FK is CASCADE, so without this the delete succeeds and the history is
    gone — irreversibly, and with nothing reporting that it happened.
    """
    db = _Session(decisions=3)
    with pytest.raises(HTTPException) as exc:
        await release_service.delete_release(db, str(db._rel.id))

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "3 recorded gate decision" in detail, "the count is what makes it actionable"
    assert "archived" in detail, (
        "a refusal that does not name the alternative just blocks the user"
    )
    assert db.deleted == [], "the release was deleted anyway"


@pytest.mark.asyncio
async def test_a_release_with_no_verdicts_still_deletes():
    """The refusal must be about lost HISTORY, not about releases.

    Blocking every delete would be a different product, and would push people
    to delete the project instead — which cascades far more.
    """
    db = _Session(decisions=0)
    await release_service.delete_release(db, str(db._rel.id))
    assert db.deleted, "a release with nothing to preserve could not be deleted"


@pytest.mark.asyncio
async def test_deleting_a_release_with_production_outcomes_is_refused():
    db = _Session(decisions=0, outcomes=2)
    with pytest.raises(HTTPException) as exc:
        await release_service.delete_release(db, str(db._rel.id))

    assert exc.value.status_code == 409
    assert "2 recorded production outcome" in str(exc.value.detail)
    assert "archived" in str(exc.value.detail)
    assert db.deleted == []


def test_archived_is_declared_as_a_status():
    """``TERMINAL_STATUSES`` branched on it while the model's own comment
    listed four statuses that did not include it — so the epic's "archive
    rather than delete" rule named a state the model said did not exist."""
    import inspect

    from app.models import postgres
    from app.services import release_lifecycle_service

    assert "archived" in release_lifecycle_service.TERMINAL_STATUSES
    # The RELEASE class, not the module: another model's status comment also
    # names `archived`, so scanning the whole file passed even with Release's
    # own vocabulary narrowed back — mutation testing caught it.
    src = inspect.getsource(postgres.Release)
    assert re.search(r"# Status: .*archived", src), (
        "the status column does not document 'archived', so nothing tells a "
        "client the state the delete refusal points them at even exists"
    )
