"""VIZ-204 unit tests for ``app.services.analytics_meta`` (no database).

The route-level behaviour -- every analytics route's ``meta`` validating
against contract C2, totals against the seeded truth -- is pinned against real
Postgres in ``tests/integration/test_analytics_envelope_postgres.py``. These
pin the helper's own rules.
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.models.viz_contracts import validate_contract
from app.services import analytics_meta
from app.services.analytics_scope import AnalyticsScope


def _scope(**overrides: Any) -> AnalyticsScope:
    base = AnalyticsScope(
        project_id=uuid.uuid4(), allowed_project_ids=None, release_ids=(),
        suite_names=(), days=30,
    )
    return dataclasses.replace(base, **overrides)


class _Result:
    def __init__(self, rows=(), one=None):
        self._rows, self._one = list(rows), one

    def all(self):
        return self._rows

    def one(self):
        return self._one


def _db(*, projects, totals, releases=()):
    """A session answering the helper's three statements in order."""
    results = [_Result(rows=projects)]
    if releases:
        results.append(_Result(rows=releases))
    results.append(_Result(one=totals))
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    return db


def _totals(**values):
    row = dict(total_runs=10, total_executions=100, matched_runs=4,
               matched_executions=40, in_progress=1)
    row.update(values)
    return SimpleNamespace(**row)


def test_ignored_needs_a_known_dimension_and_a_reason():
    assert analytics_meta.ignored("release", "why") == {"dimension": "release", "reason": "why"}
    with pytest.raises(ValueError):
        analytics_meta.ignored("project", "projects are never ignored")
    with pytest.raises(ValueError):
        analytics_meta.ignored("suite", "   ")


@pytest.mark.asyncio
async def test_a_denied_scope_reads_nothing_and_measures_nothing():
    db = AsyncMock()
    meta = await analytics_meta.build_meta(db, _scope(denied=True, project_id=None))
    db.execute.assert_not_awaited()
    validate_contract("envelope", meta)
    assert meta["scope"]["projects"] == [] and meta["measured"] is False and meta["reason"]
    assert meta["totals"] == dict.fromkeys(
        ("matched_runs", "total_runs", "matched_executions", "total_executions"), 0
    )


@pytest.mark.asyncio
async def test_meta_is_contract_valid_and_echoes_the_applied_scope():
    pid, rid = uuid.uuid4(), uuid.uuid4()
    db = _db(
        projects=[SimpleNamespace(id=pid, name="Checkout")],
        releases=[SimpleNamespace(id=rid, name="2026.09", status="archived")],
        totals=_totals(),
    )
    scope = _scope(project_id=pid, release_ids=(str(rid), "unattributed"), suite_names=("payments",))
    meta = await analytics_meta.build_meta(db, scope, pass_rate_basis="executions")
    validate_contract("envelope", meta)
    assert meta["schema_version"] == analytics_meta.META_SCHEMA_VERSION
    assert meta["scope"]["projects"] == [{"id": str(pid), "name": "Checkout"}]
    # An archived release keeps its name and says so; the sentinel is named.
    assert meta["scope"]["releases"] == [
        {"id": str(rid), "name": "2026.09", "status": "archived"},
        {"id": "unattributed", "name": "Unattributed", "status": "unattributed"},
    ]
    assert meta["scope"]["suites"] == ["payments"]
    assert meta["totals"] == {"matched_runs": 4, "total_runs": 10,
                              "matched_executions": 40, "total_executions": 100}
    assert meta["includes_in_progress"] == 1 and meta["measured"] is True
    assert meta["partial_day"] == meta["scope"]["window"]["to"]
    assert meta["scope"]["window"]["days"] == 30 and meta["scope"]["window"]["timezone"] == "UTC"


@pytest.mark.asyncio
async def test_an_ignored_dimension_leaves_the_scope_and_the_matched_totals():
    pid = uuid.uuid4()
    db = _db(projects=[SimpleNamespace(id=pid, name="P")], totals=_totals())
    scope = _scope(project_id=pid, release_ids=(str(uuid.uuid4()),), suite_names=("a",))
    with patch.object(analytics_meta, "_totals", AsyncMock(return_value={
        "total_runs": 1, "total_executions": 1, "matched_runs": 1,
        "matched_executions": 1, "in_progress": 0,
    })) as totals:
        meta = await analytics_meta.build_meta(
            db, scope, ignored_filters=[analytics_meta.ignored("release", "not a run attribute here")],
        )
    # The totals query was asked for the suite only.
    _db_arg, _scope_arg, _start, releases, suites = totals.await_args.args
    assert releases == () and suites == ("a",)
    assert meta["scope"]["releases"] == [] and meta["scope"]["suites"] == ["a"]
    assert [f["dimension"] for f in meta["ignored_filters"]] == ["release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sent,applied", [
    (("  ",), []),
    (("Orders", "orders", " ORDERS "), ["orders"]),
    (("Orders", "\t", "Payments"), ["orders", "payments"]),
    (("Checkout",), ["checkout"]),
])
async def test_meta_lists_the_normalised_suites_the_query_applied(sent, applied):
    """``scope.suites`` is what the SQL filtered by: the same normaliser
    (``suite_keys``), so a blank name lists nothing (it filters nothing) and
    case/whitespace variants collapse as they do in ``LOWER(TRIM(...))``."""
    from app.services.analytics_scope import suite_filter_sql

    pid = uuid.uuid4()
    db = _db(projects=[SimpleNamespace(id=pid, name="P")], totals=_totals())
    with patch.object(analytics_meta, "_totals", AsyncMock(return_value={
        "total_runs": 1, "total_executions": 1, "matched_runs": 1,
        "matched_executions": 1, "in_progress": 0,
    })) as totals:
        meta = await analytics_meta.build_meta(db, _scope(project_id=pid, suite_names=sent))
    validate_contract("envelope", meta)
    assert meta["scope"]["suites"] == applied
    # ...and exactly what the query binds for the same request.
    params: dict = {}
    suite_filter_sql(params, sent)
    bound = params.get("suite_names", [params["suite_name"]] if "suite_name" in params else [])
    assert meta["scope"]["suites"] == list(bound)
    assert list(totals.await_args.args[4]) == applied


@pytest.mark.asyncio
async def test_a_denied_scope_lists_no_suites_or_releases():
    db = AsyncMock()
    meta = await analytics_meta.build_meta(db, _scope(
        denied=True, project_id=None, suite_names=("Orders",), release_ids=(str(uuid.uuid4()),),
    ))
    validate_contract("envelope", meta)
    assert meta["scope"]["suites"] == [] and meta["scope"]["releases"] == []


@pytest.mark.asyncio
async def test_a_windowless_route_declares_the_window_it_does_not_apply():
    pid = uuid.uuid4()
    db = _db(projects=[SimpleNamespace(id=pid, name="P")], totals=_totals())
    meta = await analytics_meta.build_meta(db, _scope(project_id=pid, days=None), measured=True)
    validate_contract("envelope", meta)
    assert [f["dimension"] for f in meta["ignored_filters"]] == ["window"]
    assert meta["scope"]["window"]["days"] == analytics_meta.REFERENCE_WINDOW_DAYS


@pytest.mark.asyncio
async def test_no_matched_run_is_unmeasured_with_a_reason():
    pid = uuid.uuid4()
    db = _db(projects=[SimpleNamespace(id=pid, name="P")],
             totals=_totals(matched_runs=0, matched_executions=0, in_progress=0))
    meta = await analytics_meta.build_meta(db, _scope(project_id=pid))
    validate_contract("envelope", meta)
    assert meta["measured"] is False and meta["reason"].strip()


@pytest.mark.asyncio
async def test_truncation_carries_the_full_count():
    pid = uuid.uuid4()
    db = _db(projects=[SimpleNamespace(id=pid, name="P")], totals=_totals())
    meta = await analytics_meta.build_meta(
        db, _scope(project_id=pid), truncated=True, truncated_total=73,
    )
    validate_contract("envelope", meta)
    assert (meta["truncated"], meta["truncated_total"]) == (True, 73)


def test_the_header_form_is_ascii_json_that_cannot_split_a_header():
    meta = {"scope": {"suites": ["café\r\nX-Evil: 1", " "]}}
    value = analytics_meta.meta_header(meta)
    assert value.isascii() and "\n" not in value and "\r" not in value
    assert "X-Evil" not in value, "suite names never reach the header"
    assert json.loads(value)["suites"] == {"count": 2}


async def _worst_meta(*, projects: int = 0, releases: int = 0, suites: int = 0,
                      admin: bool = False) -> tuple[dict, list[str]]:
    """A real ``build_meta`` output at the reviewer's worst case, and the
    names in it (none of which may reach the header)."""
    project_rows = [SimpleNamespace(id=uuid.uuid4(), name=f"P{i:03d}-" + "n" * 35)
                    for i in range(projects)]  # 40-character names
    release_rows = [SimpleNamespace(id=uuid.uuid4(), name=f"R{i:02d}" + "r" * 252,
                                    status="active") for i in range(releases)]  # 255
    suite_names = tuple(f"{i:02d}" + "测" * 498 for i in range(suites))  # 500 CJK chars
    db = _db(projects=project_rows, releases=release_rows, totals=_totals())
    scope = _scope(
        project_id=None if admin else (project_rows[0].id if project_rows else uuid.uuid4()),
        allowed_project_ids=None,
        release_ids=tuple(str(r.id) for r in release_rows), suite_names=suite_names,
    )
    meta = await analytics_meta.build_meta(db, scope)
    validate_contract("envelope", meta)
    names = [r.name for r in project_rows] + [r.name for r in release_rows] + list(suite_names)
    return meta, names


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", [
    dict(projects=200, admin=True),
    dict(projects=40, admin=True),
    dict(projects=1, releases=20),
    dict(projects=1, suites=50),
    dict(projects=200, releases=20, suites=50, admin=True),
])
async def test_the_header_is_bounded_and_carries_no_names(shape):
    """nginx's default ``proxy_buffer_size`` is 4 KB: a bigger header is a 502.
    Names are what grows, so the header carries ids and counts only, and
    never more than HEADER_MAX_BYTES (200 projects x 40 chars, 20 releases x
    255 chars, 50 suites x 500 CJK chars)."""
    meta, names = await _worst_meta(**shape)
    value = analytics_meta.meta_header(meta)
    assert len(value.encode("ascii")) <= analytics_meta.HEADER_MAX_BYTES == 2048, len(value)
    assert value.isascii() and "\n" not in value and "\r" not in value
    for name in names:
        assert name not in value and json.dumps(name)[1:-1] not in value
    summary = json.loads(value)
    assert summary["schema_version"] == analytics_meta.META_SCHEMA_VERSION
    assert summary["form"] == "summary"
    assert summary["totals"] == meta["totals"] and summary["measured"] is meta["measured"]
    assert summary["projects"]["count"] == len(meta["scope"]["projects"])
    assert summary["releases"]["count"] == len(meta["scope"]["releases"])
    assert summary["suites"]["count"] == len(meta["scope"]["suites"])
    if summary["header_truncated"]:
        assert "ids" not in summary["projects"]
    else:
        assert summary["projects"]["ids"] == [p["id"] for p in meta["scope"]["projects"]]
        assert summary["releases"]["ids"] == [r["id"] for r in meta["scope"]["releases"]]
        assert summary["window"] == meta["scope"]["window"]


@pytest.mark.asyncio
async def test_a_small_scope_keeps_its_ids_in_the_header():
    meta, _ = await _worst_meta(projects=1, releases=3, suites=2)
    summary = json.loads(analytics_meta.meta_header(meta))
    assert summary["header_truncated"] is False
    assert summary == analytics_meta.header_summary(meta)
    assert summary["releases"]["ids"] == [r["id"] for r in meta["scope"]["releases"]]


def test_with_meta_adds_one_key_and_keeps_the_rest():
    payload = {"items": [1], "total": 1}
    out = analytics_meta.with_meta(payload, {"schema_version": 2})
    assert {k: v for k, v in out.items() if k != "meta"} == payload
    assert payload == {"items": [1], "total": 1}, "the service payload is not mutated"


# ── the dashboard cache carries the envelope's schema version ───────────────


@pytest.mark.asyncio
async def test_dashboard_cache_key_carries_the_schema_only_with_meta():
    from app.services import metrics_service

    cached = {"total_executions_7d": {"value": 1},
              "meta": {"generated_at": "2026-01-01T00:00:00+00:00", "as_of": "2026-01-01T00:00:00+00:00"}}
    get = AsyncMock(return_value=cached)
    with patch("app.services.cache_service.get_analytics_epoch", AsyncMock(return_value=3)), \
         patch("app.services.cache_service.cache_get", get):
        out = await metrics_service.get_dashboard_summary(
            AsyncMock(), "p1", 7, meta_builder=AsyncMock(),
        )
    assert get.await_args.kwargs["schema"] == analytics_meta.META_SCHEMA_VERSION
    # A hit keeps as_of (when the numbers were read) and refreshes generated_at.
    assert out["meta"]["as_of"] == "2026-01-01T00:00:00+00:00"
    assert out["meta"]["generated_at"] != "2026-01-01T00:00:00+00:00"

    get = AsyncMock(return_value={"total_executions_7d": {"value": 1}})
    with patch("app.services.cache_service.get_analytics_epoch", AsyncMock(return_value=3)), \
         patch("app.services.cache_service.cache_get", get):
        await metrics_service.get_dashboard_summary(AsyncMock(), "p1", 7)
    assert "schema" not in get.await_args.kwargs, "a caller without meta keeps the old key"
