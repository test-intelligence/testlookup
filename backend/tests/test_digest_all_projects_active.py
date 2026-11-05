"""W32 F-W30-7: an all-projects digest reads ACTIVE projects only.

``generate_digest(db, None, ...)`` has exactly two statements that are not
keyed on another statement's run ids — the runs read and the previous period's
average pass rate. Both gain the uncorrelated ``project_id IN (SELECT projects.id
... is_active IS true)`` on the all-projects branch; every later statement is
keyed on the runs' ids and narrows with them. The PROJECT path is untouched:
its statements stay byte-identical (``tests/test_digest_scope.py`` pins them,
and (3) below re-checks it).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import digest_content_service as dcs
from tests.test_digest_scope import PRE_W09_SHA256, PRE_W09_STATEMENTS, _CompilingSession

#: ``generate_digest(None, "weekly")`` over ``_CompilingSession``, captured
#: AFTER F-W30-7 with the same ``"\n----\n"`` join as the project pin. A
#: deliberate change to the all-projects path's SQL (a new statement, a column
#: on the whole-row TestRun select, the active-projects predicate) moves it:
#: re-capture it from the all-projects path, never from the project one.
ALL_PROJECTS_STATEMENTS = 6
ALL_PROJECTS_SHA256 = "e39f42451746f4936d7129818779eb1ecc1de33eb808c23029e04cd0840b1270"

_ACTIVE = "test_runs.project_id IN (SELECT projects.id"
_IS_TRUE = "projects.is_active IS true"


async def _compile(project_id):
    session = _CompilingSession()
    with patch(
        "app.services.flaky_debt_review.build_flaky_debt_reviews", AsyncMock(return_value={"teams": []})
    ), patch(
        "app.services.notification_routing.load_team_channels", AsyncMock(return_value={})
    ), patch("app.services.value_metrics_service.compute_headline", AsyncMock(return_value={})):
        await dcs.generate_digest(
            session, project_id, "weekly", since=datetime.now(timezone.utc) - timedelta(days=7)
        )
    return session


@pytest.mark.asyncio
async def test_both_unkeyed_statements_read_active_projects_only():
    session = await _compile(None)

    runs = [s for s in session.sql if s.lstrip().startswith("SELECT test_runs.id")]
    prev = [s for s in session.sql if "avg(CAST(test_runs.pass_rate" in s]
    assert len(runs) == 1 and len(prev) == 1
    for statement in (runs[0], prev[0]):
        assert _ACTIVE in statement and _IS_TRUE in statement, statement


@pytest.mark.asyncio
async def test_the_all_projects_statements_are_pinned():
    session = await _compile(None)

    joined = "\n----\n".join(session.sql)
    assert (len(session.sql), hashlib.sha256(joined.encode()).hexdigest()) == (
        ALL_PROJECTS_STATEMENTS,
        ALL_PROJECTS_SHA256,
    )


@pytest.mark.asyncio
async def test_the_project_path_never_reads_is_active():
    session = await _compile(uuid.UUID(int=1))

    assert not any("projects.is_active" in s for s in session.sql)
    joined = "\n----\n".join(session.sql)
    assert (len(session.sql), hashlib.sha256(joined.encode()).hexdigest()) == (
        PRE_W09_STATEMENTS,
        PRE_W09_SHA256,
    )
