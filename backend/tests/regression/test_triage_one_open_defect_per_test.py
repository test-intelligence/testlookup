"""E2E pass 2026-10-10: one failing test, one OPEN defect -- not one per run.

The triage stage inserted a Defect per failed ``test_case_id`` and deduplicated
on that column. A test case row belongs to ONE run, so the guard only caught a
re-run of the same run's pipeline: on the homelab a test red in 12 runs held 12
OPEN defects, the release-readiness verdict counted "41 open defects" for a
small test project, and with Jira on each run would have filed its own ticket.
The one-click Jira path already deduplicated on the test fingerprint with a
recurrence count; triage now does the same.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.regression

FP = "96bdf14e688055c5"


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Session:
    """Answers the triage statements in order and records what ran."""

    def __init__(self, recurring):
        self.recurring = recurring
        self.statements: list[str] = []
        self.commit = AsyncMock()

    async def execute(self, stmt, params=None):
        sql = _sql(stmt) if hasattr(stmt, "compile") else str(stmt)
        self.statements.append(sql)
        result = MagicMock()
        if sql.startswith("SELECT test_cases.test_name"):
            result.first.return_value = SimpleNamespace(
                test_name="test_always_red", suite_name="s", test_fingerprint=FP,
                created_at=datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc),
            )
        elif "pg_advisory_xact_lock" in sql:
            result.first.return_value = None
        elif sql.startswith("SELECT defects.id, defects.test_case_id"):
            result.first.return_value = self.recurring
        elif sql.startswith("INSERT INTO defects"):
            result.first.return_value = (uuid.uuid4(),)
        else:
            result.first.return_value = None
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def _triage(session, tc_id):
    from app.agents import triage_agent as mod

    with patch.object(mod, "AsyncSessionLocal", return_value=session), \
            patch.object(mod.settings, "JIRA_ENABLED", False), \
            patch("app.services.cache_service.bump_analytics_epoch", AsyncMock()):
        return await mod.DefectTriageAgent()._triage_one(
            tc_id, {"confidence_score": 90, "failure_category": "PRODUCT_BUG"},
            str(uuid.uuid4()), {}, {},
        )


@pytest.mark.asyncio
async def test_the_same_test_failing_in_a_later_run_takes_a_recurrence_not_a_new_defect():
    earlier = SimpleNamespace(id=uuid.uuid4(), test_case_id=uuid.uuid4(), jira_ticket_id=None, jira_ticket_url=None)
    session = _Session(recurring=earlier)
    await _triage(session, str(uuid.uuid4()))

    assert not any(s.startswith("INSERT INTO defects") for s in session.statements), session.statements
    bumps = [s for s in session.statements if s.startswith("UPDATE defects SET recurrence_count")]
    assert len(bumps) == 1, session.statements
    # Idempotent per run (live: two triage calls for ONE run counted twice):
    # only an occurrence later than the last counted one bumps the count.
    assert "defects.last_recurrence_at IS NULL OR defects.last_recurrence_at <" in bumps[0]
    lookup = next(s for s in session.statements if s.startswith("SELECT defects.id, defects.test_case_id"))
    assert "defects.signature_fingerprint =" in lookup and "test_cases.test_fingerprint =" in lookup
    assert "defects.resolution_status =" in lookup
    lock = session.statements.index(next(s for s in session.statements if "pg_advisory_xact_lock" in s))
    assert lock < session.statements.index(lookup), "the lookup must run under the per-test lock"


@pytest.mark.asyncio
async def test_a_rerun_of_the_same_runs_pipeline_is_the_existing_defect_without_a_recurrence():
    tc_id = str(uuid.uuid4())
    own = SimpleNamespace(id=uuid.uuid4(), test_case_id=tc_id, jira_ticket_id="QA-1", jira_ticket_url="u")
    session = _Session(recurring=own)
    result = await _triage(session, tc_id)

    assert result["action"] == "existing"
    assert not any(s.startswith(("INSERT INTO defects", "UPDATE defects")) for s in session.statements)


@pytest.mark.asyncio
async def test_a_first_failure_opens_a_defect_that_records_the_test_fingerprint():
    session = _Session(recurring=None)
    result = await _triage(session, str(uuid.uuid4()))

    insert = next(s for s in session.statements if s.startswith("INSERT INTO defects"))
    assert "signature_fingerprint" in insert
    assert result["action"] == "created"
