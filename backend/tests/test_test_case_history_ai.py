"""W03 G-B: AI analysis in a test's history and on the canonical page (AIH-05, contract §7.3).

* Each history point gets ``ai`` from ONE lookup keyed by the timeline's own
  ``test_cases.id`` (<= 50, already project-scoped) -- not a join in the ranked
  SELECT, so the ranked query and its hand-built-row fakes are unchanged; a
  point with no analysis gets ``ai: null``; ``ai_changes`` marks where the AI
  changed its mind; no ids, no lookup.
* The response models carry both fields (an older client ignores them).
* The canonical page's rows get ``ai`` from the canonical's own case ids, newest
  first, with ``ai_truncated``.
The same over real rows (another project's same fingerprint excluded,
``version_count`` from real versions) is
``tests/integration/test_ai_history_routes_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import ai_history_read_service as reads
from app.services import test_case_history_service as svc

PROJECT = uuid.uuid4()
NOW = datetime.now(timezone.utc)


class _Res:
    def __init__(self, first=None, all_=None, scalar=None):
        self._first, self._all, self._scalar = first, all_ or [], scalar

    def first(self):
        return self._first

    def all(self):
        return self._all

    def scalar_one_or_none(self):
        return self._scalar


class _DB:
    """The history service's queries; the ranked timeline rows carry their own test_case_id."""

    def __init__(self, tc, run, points):
        self.tc, self.run, self.points = tc, run, points
        self.ranked_sql: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        if "as suite" in sql and "test_runs tr" in sql:
            return _Res(first=SimpleNamespace(suite="checkout"))
        if "test_case_history" in sql and "row_number" in sql:
            self.ranked_sql.append(sql)
            return _Res(all_=self.points)
        if "test_case_history" in sql and "percentile_cont" in sql:
            return _Res(first=SimpleNamespace(n=0, p50=None, p95=None, max_ms=None))
        if "test_case_history" in sql and "count" in sql:
            return _Res(first=SimpleNamespace(total=len(self.points), failed=len(self.points), passed=0))
        if "perf_baselines" in sql or "canonical_test_cases" in sql:
            return _Res(all_=[], scalar=None)
        if "from test_cases join test_runs" in sql:
            return _Res(first=(self.tc, self.run))  # the (run_id, test_id) resolver
        if "from test_cases" in sql:
            return _Res(all_=[])  # EXEC-03 point timings
        return _Res()


def _point(case_id, run_id, hours_ago):
    return SimpleNamespace(run_id=run_id, test_case_id=case_id, status="FAILED", duration_ms=100,
                           created_at=NOW - timedelta(hours=hours_ago), build_number=f"b{hours_ago}",
                           primary_suite_name="checkout", trigger_source="ci")


@pytest.fixture
def world(monkeypatch):
    async def seq(db, run_ids):
        return {str(r): i + 1 for i, r in enumerate(reversed(run_ids))}

    monkeypatch.setattr("app.services.runs_service.fetch_run_seq_map", seq)
    monkeypatch.setattr("app.services.execution_metrics_service.load_baselines", AsyncMock(return_value={}))
    run = SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT, build_number="b0", primary_suite_name="checkout",
                          trigger_source="ci", created_at=NOW)
    tc = SimpleNamespace(id=uuid.uuid4(), test_run_id=run.id, test_fingerprint="fp-pay", test_name="test_pay",
                         owner=None, assigned_to_user_id=None, suite_name="checkout", severity=None, feature=None)
    newest, middle, oldest = tc.id, uuid.uuid4(), uuid.uuid4()
    points = [_point(newest, run.id, 1), _point(middle, uuid.uuid4(), 2), _point(oldest, uuid.uuid4(), 3)]
    return SimpleNamespace(db=_DB(tc, run, points), run=run, tc=tc, ids=(newest, middle, oldest))


def _ai(case_id, category, sha, confidence=80):
    return {"test_id": str(case_id), "category": category, "confidence": confidence, "root_cause_excerpt": "x",
            "root_cause_sha": sha, "analysis_mode": "llm", "model": "llama3.1", "is_flaky": False,
            "requires_human_review": False, "version_count": 2}


@pytest.mark.asyncio
async def test_each_point_gets_its_own_ai_and_the_changes_are_marked(world, monkeypatch):
    newest, middle, oldest = world.ids
    lookup = AsyncMock(return_value={
        newest: _ai(newest, "PRODUCT_BUG", "b" * 16, 85),
        oldest: _ai(oldest, "INFRASTRUCTURE", "a" * 16, 40),
    })
    monkeypatch.setattr(reads, "test_history_ai", lookup)
    out = await svc.get_test_case_history(world.db, world.run.id, world.tc.id)
    lookup.assert_awaited_once()
    assert list(lookup.await_args.args[1]) == [newest, middle, oldest], "the timeline's own ids, nothing else"
    assert [p["ai"] and p["ai"]["category"] for p in out["history"]] == ["PRODUCT_BUG", None, "INFRASTRUCTURE"]
    assert out["ai_changes"] == [{
        "run_id": str(world.run.id), "run_label": out["history"][0]["run_label"],
        "from_category": "INFRASTRUCTURE", "to_category": "PRODUCT_BUG", "from_confidence": 40,
        "to_confidence": 85, "kinds": ["category", "root_cause"],
    }]
    assert all("ai_analysis" not in sql for sql in world.db.ranked_sql), "no join inside the ranked SELECT"

    from app.models.schemas import TestCaseHistoryResponse

    served = TestCaseHistoryResponse.model_validate(out).model_dump(mode="json")
    assert served["history"][0]["ai"]["version_count"] == 2 and served["history"][1]["ai"] is None
    assert served["ai_changes"][0]["kinds"] == ["category", "root_cause"]


@pytest.mark.asyncio
async def test_no_analysis_means_null_ai_and_no_changes(world, monkeypatch):
    monkeypatch.setattr(reads, "test_history_ai", AsyncMock(return_value={}))
    out = await svc.get_test_case_history(world.db, world.run.id, world.tc.id)
    assert [p["ai"] for p in out["history"]] == [None, None, None] and out["ai_changes"] == []


@pytest.mark.asyncio
async def test_points_without_a_case_id_trigger_no_lookup(world, monkeypatch):
    for point in world.db.points:
        point.test_case_id = None
    lookup = AsyncMock()
    monkeypatch.setattr(reads, "test_history_ai", lookup)
    out = await svc.get_test_case_history(world.db, world.run.id, world.tc.id)
    lookup.assert_not_awaited()
    assert out["ai_changes"] == []


def test_an_older_client_payload_without_ai_still_validates():
    from app.models.schemas import TestCaseHistoryPointResponse

    point = TestCaseHistoryPointResponse.model_validate({
        "run_id": None, "run_label": "Run #1", "status": "FAILED", "created_at": NOW,
    })
    assert point.ai is None


# ── The canonical page ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_canonical_rows_carry_ai_from_their_own_case_ids(monkeypatch):
    from app.routers import suites

    canonical = SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT)
    run = SimpleNamespace(build_number="101", branch="main", environment=None, environment_source=None)
    cases = [SimpleNamespace(id=uuid.uuid4(), test_run_id=uuid.uuid4(), status="FAILED", duration_ms=1, suite_name="s",
                             created_at=NOW, retry_count=0, is_flaky_run=False) for _ in range(2)]
    monkeypatch.setattr(suites.svc, "get_canonical_or_404", AsyncMock(return_value=canonical))
    monkeypatch.setattr(suites.svc, "list_runs_for_canonical", AsyncMock(return_value=[(c, run) for c in cases]))
    access = AsyncMock()
    monkeypatch.setattr(suites, "_enforce_project_access", access)
    monkeypatch.setattr(suites, "resolve_environment", lambda r: SimpleNamespace(to_dict=lambda: {}))
    lookup = AsyncMock(return_value=({cases[0].id: {"category": "PRODUCT_BUG", "version_count": 3}}, True))
    monkeypatch.setattr(reads, "canonical_runs_ai", lookup)
    user = SimpleNamespace(id=uuid.uuid4())
    body = await suites.list_canonical_run_history(canonical.id, db=None, current_user=user)
    access.assert_awaited_once_with(None, user, PROJECT)
    assert lookup.await_args.args[1] == [c.id for c in cases]
    assert [item["ai"] for item in body["items"]] == [{"category": "PRODUCT_BUG", "version_count": 3}, None]
    assert body["ai_truncated"] is True and body["total"] == 2
