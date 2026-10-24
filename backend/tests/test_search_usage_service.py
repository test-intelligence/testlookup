"""``services/search_usage_service`` (W12 SRCH-05): usage rows with no query text.

* ``query_hash`` is an HMAC keyed from ``APP_SECRET_KEY`` (not ``sha256(q)``,
  which a dictionary reverses), stable, equal for two spellings of one
  normalised query, different under another secret.
* ``build_event`` holds no key named ``q`` / ``query`` / ``text`` and no value
  equal to the query; it never raises (counts clamped, vocabularies cut to
  their columns).
* ``usage_summary`` is ONE statement (``count`` + two ``FILTER`` counts + two
  ``percentile_cont``) whose WHERE carries the resolved scope; an empty
  membership set answers ``measured: False`` with no statement; no row in the
  window is ``measured: False`` with every rate ``None`` (never 0 %).
* ``purge_expired_batch`` stages ONE bounded delete and never commits.

The SQL semantics (``percentile_cont``, ``FILTER``, ``ANY``) run against
PostgreSQL in ``tests/integration/test_search_usage_postgres.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.core.search_vocab import USAGE_HASH_DOMAIN, USAGE_PURGE_BATCH, USAGE_RETENTION_DAYS
from app.models.postgres import SearchQueryEvent
from app.models.schemas import SearchUsageRead
from app.services import search_usage_service as svc

NOW = datetime(2026, 10, 24, 14, 5, 30, tzinfo=timezone.utc)
TERM = "zq-sentinel-7 checkout"
PROJECT = uuid.uuid4()


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Result:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def one(self):
        return self._row


class _Session:
    """Records every statement; answers with the canned row / rowcount."""

    def __init__(self, row=None, rowcount=0):
        self.statements: list = []
        self.row = row
        self.rowcount = rowcount
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.row, self.rowcount)

    async def commit(self):  # pragma: no cover - the service never calls it
        self.commits += 1


def _row(searches=0, zero_results=0, fallbacks=0, p50=None, p95=None):
    return SimpleNamespace(searches=searches, zero_results=zero_results, fallbacks=fallbacks, p50=p50, p95=p95)


# ── the keyed hash ───────────────────────────────────────────────────────────


def test_query_hash_is_an_hmac_keyed_from_the_app_secret(monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "secret-one")
    digest = svc.query_hash("Checkout Flow")
    key = hashlib.sha256(f"{USAGE_HASH_DOMAIN}:secret-one".encode()).digest()
    assert digest == hmac.new(key, b"checkout flow", hashlib.sha256).hexdigest()
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert digest != hashlib.sha256(b"checkout flow").hexdigest()
    assert digest != hashlib.sha256(b"Checkout Flow").hexdigest()


def test_query_hash_is_stable_normalised_and_secret_dependent(monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "secret-one")
    first = svc.query_hash(" Checkout  Flow")
    assert first == svc.query_hash("checkout flow") == svc.query_hash("CHECKOUT\tflow\n")
    assert first != svc.query_hash("checkout flows")
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "secret-two")
    assert svc.query_hash("checkout flow") != first, "a rotated secret starts a new hash domain"


def test_normalize_query():
    assert svc.normalize_query("  Status:Failed   Checkout ") == "status:failed checkout"
    assert svc.normalize_query("") == ""


# ── build_event ──────────────────────────────────────────────────────────────


def _event(**overrides) -> dict:
    kwargs = dict(
        q=f"  {TERM}  ", word_count=2, filter_count=0, project_id=PROJECT, entity_scope="all",
        mode_requested="semantic", mode_used="keyword", fallback_reason="semantic_unavailable",
        result_count=7, counts_exact=True, latency_ms=41, now=NOW,
    )
    kwargs.update(overrides)
    return svc.build_event(**kwargs)


def test_build_event_holds_the_columns_and_no_query_text():
    event = _event()
    columns = {c.name for c in SearchQueryEvent.__table__.columns} - {"id"}
    assert set(event) == columns
    assert not {"q", "query", "text", "query_text", "user_id"} & set(event)
    for value in event.values():
        assert not (isinstance(value, str) and ("zq-sentinel-7" in value or "checkout" in value.lower())), value
    assert event["q_hash"] == svc.query_hash(TERM)
    assert event["q_length"] == len(TERM)
    assert (event["word_count"], event["filter_count"], event["result_count"], event["latency_ms"]) == (2, 0, 7, 41)
    assert (event["entity_scope"], event["mode_requested"], event["mode_used"]) == ("all", "semantic", "keyword")
    assert event["fallback_reason"] == "semantic_unavailable" and event["counts_exact"] is True
    assert event["project_id"] == PROJECT and event["created_at"] == NOW
    row = SearchQueryEvent(**event)
    assert row.q_hash == event["q_hash"]


def test_build_event_never_raises_and_fits_its_columns():
    event = _event(
        word_count=-3, filter_count=10**9, result_count=-1, latency_ms="12", project_id=str(PROJECT),
        entity_scope="x" * 50, mode_requested="semantic-very-long", fallback_reason="", counts_exact=0,
        now=datetime(2026, 10, 24, 9, 0),
    )
    assert event["word_count"] == 0 and event["filter_count"] == 32767 and event["result_count"] == 0
    assert event["latency_ms"] == 12 and event["project_id"] == PROJECT
    assert len(event["entity_scope"]) == 20 and len(event["mode_requested"]) == 10
    assert event["fallback_reason"] is None and event["counts_exact"] is False
    assert event["created_at"].tzinfo is timezone.utc
    assert _event(project_id="not-a-uuid")["project_id"] is None
    assert _event(project_id=None)["project_id"] is None
    assert _event(latency_ms=None)["latency_ms"] == 0
    assert _event(now=None)["created_at"].tzinfo is not None


# ── the window ───────────────────────────────────────────────────────────────


def test_the_usage_window_and_the_cutoff():
    assert svc.usage_window_start(1, NOW) == datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert svc.usage_window_start(7, NOW) == NOW - timedelta(days=7)
    assert svc.usage_window_start(30, NOW) == NOW - timedelta(days=30)
    local = datetime(2026, 10, 24, 1, 30, tzinfo=timezone(timedelta(hours=5)))   # 2026-10-23 20:30 UTC
    assert svc.usage_window_start(1, local) == datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert svc.usage_cutoff(NOW) == NOW - timedelta(days=USAGE_RETENTION_DAYS)


# ── usage_summary ────────────────────────────────────────────────────────────


async def test_one_statement_pinned_to_the_project():
    db = _Session(_row(searches=3, zero_results=1, fallbacks=1, p50=40.0, p95=120.5))
    body = await svc.usage_summary(db, project_id=PROJECT, allowed_project_ids=None, days=1, now=NOW)

    (stmt,) = db.statements
    sql = _sql(stmt)
    assert sql.count("SELECT") == 1, "ONE statement, no subquery"
    assert "percentile_cont(%(percentile_cont_1)s) WITHIN GROUP (ORDER BY search_query_events.latency_ms)" in sql
    assert "count(*) FILTER (WHERE search_query_events.result_count = %(result_count_1)s AND search_query_events.counts_exact IS true)" in sql
    assert "count(*) FILTER (WHERE search_query_events.fallback_reason IS NOT NULL)" in sql
    assert "search_query_events.created_at >= %(created_at_1)s" in sql
    assert "search_query_events.project_id = %(project_id_1)s" in sql
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert params["created_at_1"] == datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert params["project_id_1"] == PROJECT
    assert db.commits == 0

    assert body == {
        "project_id": PROJECT, "days": 1, "since": datetime(2026, 10, 24, tzinfo=timezone.utc),
        "measured": True, "searches": 3, "zero_result_searches": 1, "zero_result_rate": pytest.approx(1 / 3),
        "p50_latency_ms": 40.0, "p95_latency_ms": 120.5, "fallback_searches": 1, "retention_days": 30,
    }
    SearchUsageRead(**body)


async def test_a_members_projects_ride_any_and_admin_reads_everything():
    a, b = uuid.uuid4(), uuid.uuid4()
    db = _Session(_row(searches=1, p50=5, p95=5))
    await svc.usage_summary(db, project_id=None, allowed_project_ids={a, b}, days=7, now=NOW)
    sql = _sql(db.statements[0])
    assert "search_query_events.project_id = ANY (%(allowed_project_ids)s::UUID[])" in sql
    assert set(db.statements[0].compile(dialect=postgresql.dialect()).params["allowed_project_ids"]) == {a, b}

    admin = _Session(_row(searches=1, p50=5, p95=5))
    body = await svc.usage_summary(admin, project_id=None, allowed_project_ids=None, days=30, now=NOW)
    assert "project_id" not in _sql(admin.statements[0]).split("WHERE", 1)[1], "ADMIN: no project predicate"
    assert body["since"] == NOW - timedelta(days=30) and body["project_id"] is None


async def test_no_membership_issues_no_statement_and_is_not_measured():
    db = _Session()
    body = await svc.usage_summary(db, project_id=None, allowed_project_ids=set(), days=1, now=NOW)
    assert db.statements == []
    assert body["measured"] is False and body["searches"] == 0
    assert body["zero_result_rate"] is None and body["p50_latency_ms"] is None and body["p95_latency_ms"] is None


async def test_no_row_in_the_window_is_not_measured_never_zero_percent():
    db = _Session(_row(searches=0, p50=None, p95=None))
    body = await svc.usage_summary(db, project_id=PROJECT, allowed_project_ids=None, days=1, now=NOW)
    assert body["measured"] is False
    assert body["zero_result_rate"] is None, "absence is not a 0 % zero-result rate"
    assert body["p50_latency_ms"] is None and body["p95_latency_ms"] is None
    assert body["searches"] == 0 and body["zero_result_searches"] == 0
    SearchUsageRead(**body)


async def test_days_outside_the_vocabulary_is_refused():
    with pytest.raises(ValueError):
        await svc.usage_summary(_Session(), project_id=PROJECT, allowed_project_ids=None, days=2, now=NOW)


# ── the purge batch ──────────────────────────────────────────────────────────


async def test_purge_expired_batch_stages_one_bounded_delete():
    db = _Session(rowcount=17)
    deleted = await svc.purge_expired_batch(db, now=NOW)
    assert deleted == 17
    (stmt,) = db.statements
    sql = " ".join(_sql(stmt).split())
    assert sql.startswith("DELETE FROM search_query_events WHERE search_query_events.id IN (SELECT search_query_events.id")
    assert "WHERE search_query_events.created_at < %(created_at_1)s ORDER BY search_query_events.created_at LIMIT %(param_1)s" in sql
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert params["created_at_1"] == NOW - timedelta(days=30)
    assert params["param_1"] == USAGE_PURGE_BATCH
    assert "project_id" not in sql, "every project and the All-Projects rows alike"
    assert db.commits == 0

    small = _Session(rowcount=0)
    assert await svc.purge_expired_batch(small, now=NOW, batch=10) == 0
    assert small.statements[0].compile(dialect=postgresql.dialect()).params["param_1"] == 10


def test_the_service_is_commit_free_and_logs_nothing():
    source = inspect.getsource(svc)
    assert ".commit(" not in source and ".rollback(" not in source
    assert "logger" not in source and "logging" not in source, "a usage row never reaches a log line"
