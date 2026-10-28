"""W22a ACT-04 — the keep-last floor (contract §2.2 F-1, F-2, F-4).

``floor_not_kept`` is module-level so these tests compile the EXACT predicate
the purge uses; the preview's ``floor`` block comes from the two run selects
the purge already makes (window minus purged) plus ONE classification
statement only when the floor kept something.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.models import postgres as models
from app.services import retention_service as svc
from app.services.retention_service import EffectiveRetentionPolicy, RetentionValidationError
from tests.test_retention_purge import PROJECT_ID, _FakeDB, _FakeMongo, _FakeStorage, _policy_row, _Rows


def _sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_the_floor_is_three_anti_joins_and_never_not_in():
    sql = _sql(svc.floor_not_kept(models.TestRun.id, PROJECT_ID, keep_last_runs=10))
    assert sql.count("NOT (EXISTS") == 3
    assert "NOT IN" not in sql
    assert "floor_newest" in sql and "LIMIT 10" in sql
    assert "floor_latest_passed" in sql and "LIMIT 1" in sql
    assert "floor_suite_latest_passed" in sql
    assert sql.count("test_runs.status = 'PASSED'") == 2, "arms 2 and 3 keep PASSED runs only"
    assert "json_typeof(test_runs.suite_names) = 'array'" in sql, "a scalar suite_names must not raise"
    assert "json_build_array(test_runs.primary_suite_name)" in sql
    assert "ORDER BY test_runs.created_at DESC, test_runs.id DESC" in sql


def test_a_zero_floor_keeps_only_the_passed_arms():
    sql = _sql(svc.floor_not_kept(models.TestRun.id, PROJECT_ID, keep_last_runs=0))
    assert sql.count("NOT (EXISTS") == 2
    assert "floor_newest" not in sql
    assert "floor_latest_passed" in sql and "floor_suite_latest_passed" in sql


# ── Policy (F-1) ─────────────────────────────────────────────────────────────


async def test_the_floor_defaults_to_10_without_a_row_and_on_a_fake_without_the_attribute():
    assert EffectiveRetentionPolicy().keep_last_runs == 10
    assert EffectiveRetentionPolicy().as_dict()["keep_last_runs"] == 10
    eff = await svc.get_effective_policy(_FakeDB(policy=None), PROJECT_ID)
    assert eff.keep_last_runs == 10
    eff = await svc.get_effective_policy(_FakeDB(policy=_policy_row()), PROJECT_ID)
    assert eff.keep_last_runs == 10, "a SimpleNamespace row without the column reads the default"
    eff = await svc.get_effective_policy(_FakeDB(policy=_policy_row(keep_last_runs=None)), PROJECT_ID)
    assert eff.keep_last_runs == 10


async def test_a_stored_floor_is_the_effective_floor_including_zero():
    eff = await svc.get_effective_policy(_FakeDB(policy=_policy_row(keep_last_runs=25)), PROJECT_ID)
    assert eff.keep_last_runs == 25 and eff.as_dict()["keep_last_runs"] == 25
    eff = await svc.get_effective_policy(_FakeDB(policy=_policy_row(keep_last_runs=0)), PROJECT_ID)
    assert eff.keep_last_runs == 0, "0 is a value, not 'unset'"


@pytest.mark.parametrize("value", [-1, 1001])
async def test_a_floor_out_of_bounds_is_rejected_and_stages_nothing(value):
    db = _FakeDB(policy=None)
    with pytest.raises(RetentionValidationError) as err:
        await svc.upsert_policy(db, project_id=PROJECT_ID, keep_last_runs=value)
    assert str(err.value) == f"keep_last_runs must be between 0 and 1000 (got {value})"
    assert db.added == []


async def test_a_floor_change_is_audited_like_the_day_fields():
    row = _policy_row(keep_last_runs=10)
    db = _FakeDB(policy=row)
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, keep_last_runs=3)
    assert eff.keep_last_runs == 3 and row.keep_last_runs == 3
    audit = next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")
    assert audit.changed_fields["changes"] == {"keep_last_runs": {"old": 10, "new": 3}}
    assert db.commits == 0


async def test_restating_the_floor_or_omitting_it_is_not_a_change():
    row = _policy_row(keep_last_runs=10)
    db = _FakeDB(policy=row)
    await svc.upsert_policy(db, project_id=PROJECT_ID, keep_last_runs=10)
    await svc.upsert_policy(db, project_id=PROJECT_ID, runs_days=400)
    audits = [o for o in db.added if type(o).__name__ == "SettingsAuditLog"]
    assert audits[0].changed_fields["changes"] == {}
    assert "keep_last_runs" not in audits[1].changed_fields["changes"]
    assert row.keep_last_runs == 10


async def test_a_new_row_stores_the_floor_the_admin_set():
    db = _FakeDB(policy=None)
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, keep_last_runs=0)
    row = next(o for o in db.added if type(o).__name__ == "ProjectRetentionPolicy")
    assert row.keep_last_runs == 0 and eff.keep_last_runs == 0
    audit = next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")
    assert audit.changed_fields["changes"]["keep_last_runs"] == 0


# ── The preview's floor block (F-2, F-4) ─────────────────────────────────────


class _FloorDB(_FakeDB):
    """``_FakeDB`` whose RUNS-CLOCK select (the one carrying the floor) answers
    only ``purge_rows``, and which answers the floor's classification."""

    def __init__(self, *, purge_rows, detail=(False, 0), **kwargs):
        super().__init__(**kwargs)
        self.purge_rows = list(purge_rows)
        self.detail = detail
        self.sql: list[str] = []

    async def execute(self, stmt):
        text = str(stmt)
        self.sql.append(text)
        if "minio_prefix" in text and "floor_latest_passed" in text:
            return _Rows(self.purge_rows)
        if "latest_passed_kept" in text:
            return _Rows([self.detail])
        return await super().execute(stmt)


def _window(now, *, ages=(400, 500, 600), artifacts_days=180):
    runs = [
        (uuid.UUID(f"aaaaaaaa-0000-0000-0000-{i:012d}"), f"minio/run-{i}/", now - timedelta(days=age))
        for i, age in enumerate(ages)
    ]
    policy = _policy_row(keep_last_runs=2, artifacts_days=artifacts_days)
    return runs, policy


async def test_the_floor_block_comes_from_the_two_selects():
    now = datetime.now(timezone.utc)
    runs, policy = _window(now)
    db = _FloorDB(policy=policy, run_rows=runs, purge_rows=runs[2:], detail=(True, 2))
    events: list = []
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="preview", now=now,
        mongo=_FakeMongo(events), storage=_FakeStorage(events),
    )
    assert out["candidates"]["runs"] == 1
    assert out["floor"] == {"keep_last_runs": 2, "runs_kept": 2, "latest_passed_kept": True, "suites_kept": 2}
    detail = [s for s in db.sql if "latest_passed_kept" in s]
    assert len(detail) == 1, "ONE classification statement, only because the floor kept runs"


async def test_nothing_kept_by_the_floor_issues_no_classification():
    now = datetime.now(timezone.utc)
    runs, policy = _window(now)
    db = _FloorDB(policy=policy, run_rows=runs, purge_rows=runs, detail=(True, 9))
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="preview", now=now,
        mongo=_FakeMongo([]), storage=_FakeStorage([]),
    )
    assert out["floor"] == {"keep_last_runs": 2, "runs_kept": 0, "latest_passed_kept": False, "suites_kept": 0}
    assert not [s for s in db.sql if "latest_passed_kept" in s]


async def test_an_empty_window_issues_no_runs_clock_select():
    now = datetime.now(timezone.utc)
    db = _FloorDB(policy=_policy_row(), run_rows=[], purge_rows=[])
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="preview", now=now,
        mongo=_FakeMongo([]), storage=_FakeStorage([]),
    )
    assert out["floor"]["runs_kept"] == 0 and out["floor"]["keep_last_runs"] == 10
    assert not [s for s in db.sql if "floor_latest_passed" in s]


async def test_the_floor_protects_the_run_row_not_its_expired_raw_events():
    """D-21: the RUNS clock only. A floor-kept run still loses its expired raw
    events; its artifacts stay until the artifacts clock expires them."""
    now = datetime.now(timezone.utc)
    runs, policy = _window(now, artifacts_days=3650)
    db = _FloorDB(policy=policy, run_rows=runs, purge_rows=runs[2:])
    events: list = []
    mongo, storage = _FakeMongo(events), _FakeStorage(events)
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    purged, kept = str(runs[2][0]), {str(runs[0][0]), str(runs[1][0])}
    (runs_clock,) = mongo.collections["execution_logs"].delete_filters
    assert runs_clock["test_run_id"]["$in"] == [purged]
    (raw_clock,) = mongo.collections["raw_allure_json"].delete_filters
    assert set(raw_clock["test_run_id"]["$in"]) == kept | {purged}
    assert set(storage.deleted_prefixes) == {f"uploads/{PROJECT_ID}/{purged}/", runs[2][1]}, (
        "only the purged run's objects go — the artifacts clock has not reached the floor-kept runs"
    )
    assert out["floor"]["runs_kept"] == 2
