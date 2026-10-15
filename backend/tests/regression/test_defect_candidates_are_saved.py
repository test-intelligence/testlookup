"""The background defect-candidate build saves what it builds, and says when it can't.

Re-testing the run page on the homelab (2026-10-14), three of four runs got no
candidates and the log said nothing. Diagnosed in the pod with the commit
rolled back:

* The LLM sometimes answered ``"description"`` with an object
  (``{"what": ..., "why": ...}``); asyncpg refused the INSERT
  ("expected str, got dict"), the build's single end-of-run commit rolled
  back EVERY candidate of the run without a log line, and the build still
  returned them as built.
* A run analysed twice lists a cluster id once per pipeline run.
  ``get_defect_candidate`` looked the cluster up with ``scalar_one_or_none``
  and raised ``MultipleResultsFound`` (logged at DEBUG), and the build would
  have built the cluster twice.

Now ticket fields are rendered as text and clipped to their columns, each
candidate is committed on its own (a refused one is logged and not
returned), the newest of a repeated cluster is used, and each cluster id is
built once.
"""
from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import MultipleResultsFound

import app.db.postgres as pg
from app.services import defect_promotion_service as dps
from app.services import run_intelligence_service as ris

RUN_ID = uuid.uuid4()


def _cluster(**overrides):
    base = dict(cluster_id="cl_001", label="AssertionError: expected total 84.15 but got 89.10",
                size=2, representative_error="expected total", member_test_ids=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def _llm_answering(payload):
    async def get_llm(*args, **kwargs):
        return SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload))))
    return get_llm


# ── 1. The model's ticket JSON becomes storable text ─────────────────────────


@pytest.mark.asyncio
async def test_a_description_written_as_an_object_is_saved_as_text(monkeypatch):
    monkeypatch.setattr(dps, "get_llm", _llm_answering({
        "title": "Incorrect discount stacking",
        "description": {"what": "The total is 89.10, not 84.15", "why": "Discounts stack"},
        "severity": "HIGH",
        "component": "Checkout",
        "owner_team": "payments",
        "labels": ["checkout", {"extra": "x"}],
    }))
    content = await dps._generate_jira_content(_cluster(), [])
    assert isinstance(content["description"], str)
    assert "The total is 89.10, not 84.15" in content["description"]
    assert "Discounts stack" in content["description"]
    assert "{" not in content["description"]
    assert all(isinstance(label, str) for label in content["labels"])


@pytest.mark.asyncio
async def test_every_text_field_fits_its_column(monkeypatch):
    monkeypatch.setattr(dps, "get_llm", _llm_answering({
        "title": "t" * 900,
        "description": ["first step", "second step"],
        "severity": "High - blocks every checkout in production",
        "component": "c" * 400,
        "owner_team": {"name": "payments", "channel": "#pay"},
    }))
    content = await dps._generate_jira_content(_cluster(), [])
    for field in ("title", "description", "severity", "component", "owner_team"):
        assert isinstance(content[field], str), field
    assert len(content["title"]) <= 500
    assert len(content["severity"]) <= 20
    assert len(content["component"]) <= 255
    assert content["description"] == "- first step\n- second step"


@pytest.mark.asyncio
async def test_missing_fields_take_the_deterministic_text(monkeypatch):
    monkeypatch.setattr(dps, "get_llm", _llm_answering({"title": "", "description": None, "labels": "x"}))
    cluster = _cluster()
    content = await dps._generate_jira_content(cluster, [])
    fallback = dps._deterministic_jira_content(cluster)
    assert content["title"] == fallback["title"]
    assert content["description"] == fallback["description"]
    assert content["labels"] == fallback["labels"]


# ── 2. One refused candidate does not lose the others ────────────────────────


class _WriteSession:
    """A write session whose commit refuses the candidate for ``bad_cluster``."""

    def __init__(self, bad_cluster: str, saved: list):
        self._bad = bad_cluster
        self._pending: list = []
        self._saved = saved

    def add(self, row):
        self._pending.append(row)

    async def commit(self):
        if any(row.cluster_id == self._bad for row in self._pending):
            self._pending.clear()
            raise RuntimeError("DataError: invalid input for query argument (expected str, got dict)")
        self._saved.extend(row.cluster_id for row in self._pending)
        self._pending.clear()

    async def rollback(self):
        self._pending.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _candidate(cluster_id):
    return {"title": f"ticket {cluster_id}", "description": "d", "severity": "HIGH",
            "composite_score": 0.5, "failure_category": "PRODUCT_BUG", "member_count": 1}


@pytest.mark.asyncio
async def test_a_refused_candidate_does_not_lose_the_others(monkeypatch, caplog):
    saved: list = []
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _WriteSession("cl_002", saved))
    monkeypatch.setattr(dps, "get_defect_candidate", AsyncMock(side_effect=lambda run, cid, db: _candidate(cid)))
    clusters = [{"cluster_id": c} for c in ("cl_001", "cl_002", "cl_003")]
    with caplog.at_level(logging.WARNING, logger="services.run_intelligence"):
        built = await ris._build_and_persist_defect_candidates(RUN_ID, clusters)
    assert saved == ["cl_001", "cl_003"]
    assert [b["cluster_id"] for b in built] == ["cl_001", "cl_003"], "a refused candidate is not reported as built"
    assert any("cl_002" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_a_cluster_that_fails_to_build_is_logged_as_a_warning(monkeypatch, caplog):
    saved: list = []
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _WriteSession("none", saved))

    async def build(run, cid, db):
        if cid == "cl_001":
            raise MultipleResultsFound("Multiple rows were found when one or none was required")
        return _candidate(cid)

    monkeypatch.setattr(dps, "get_defect_candidate", build)
    with caplog.at_level(logging.WARNING, logger="services.run_intelligence"):
        built = await ris._build_and_persist_defect_candidates(RUN_ID, [{"cluster_id": "cl_001"}, {"cluster_id": "cl_002"}])
    assert [b["cluster_id"] for b in built] == ["cl_002"]
    assert any("cl_001" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


# ── 3. A repeated cluster id ─────────────────────────────────────────────────


def test_the_build_takes_five_distinct_clusters():
    clusters = [{"cluster_id": c} for c in ("cl_001", "cl_003", "cl_001", "cl_002", "cl_003", "cl_004", "cl_005", "cl_006")]
    assert [c["cluster_id"] for c in ris._first_unique_clusters(clusters)] == ["cl_001", "cl_003", "cl_002", "cl_004", "cl_005"]


@pytest.mark.asyncio
async def test_the_build_builds_a_repeated_cluster_once(monkeypatch):
    saved: list = []
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _WriteSession("none", saved))
    calls: list = []

    async def build(run, cid, db):
        calls.append(cid)
        return _candidate(cid)

    monkeypatch.setattr(dps, "get_defect_candidate", build)
    clusters = [{"cluster_id": c} for c in ("cl_001", "cl_003", "cl_001", "cl_002")]
    await ris._build_and_persist_defect_candidates(RUN_ID, clusters)
    assert calls == ["cl_001", "cl_003", "cl_002"]
    assert saved == ["cl_001", "cl_003", "cl_002"]


class _Rows:
    """A Result over ``rows`` that refuses scalar_one_or_none on more than one, like SQLAlchemy's."""

    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        if len(self._rows) > 1:
            raise MultipleResultsFound("Multiple rows were found when one or none was required")
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(first=lambda: self._rows[0] if self._rows else None, all=lambda: list(self._rows))


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_a_cluster_stored_twice_is_read_as_its_newest_row(monkeypatch):
    newest = _cluster(label="newest")
    older = _cluster(label="older")
    statements: list = []

    async def execute(stmt, *args, **kwargs):
        statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        sql = statements[-1]
        if "FROM failure_clusters" in sql:
            # The database's answer to "newest first, one row": what the
            # query asked for decides which row a duplicate yields.
            return _Rows([newest, older] if "ORDER BY" in sql and "LIMIT" in sql else [older, newest])
        if "FROM test_runs" in sql:
            return _Rows([SimpleNamespace(project_id=uuid.uuid4())])
        return _Rows([])

    seen: list = []

    async def ticket(cluster, analyses):
        seen.append(cluster.label)
        raise _Stop()

    monkeypatch.setattr(dps, "_generate_jira_content", ticket)
    with pytest.raises(_Stop):
        await dps.get_defect_candidate(str(RUN_ID), "cl_001", SimpleNamespace(execute=execute, new=(), dirty=(), deleted=(), close=AsyncMock()))
    assert seen == ["newest"]
    cluster_sql = next(s for s in statements if "FROM failure_clusters" in s)
    assert "ORDER BY failure_clusters.created_at DESC" in cluster_sql
