"""The analysis cache must never fall back to a key shared across tenants.

Re-audit finding M15. ``_scoped_cache_key`` scoped the cached triage analysis
by project -- its own docstring explains why: the cached analysis embeds
project-specific evidence (Splunk and OpenShift excerpts), so an identical
failure in another project must never be served it.

But it fell back to the UNSCOPED key when ``project_id`` was missing::

    return f"{base}:proj:{project_id}" if project_id else base

and both callers pass ``None`` whenever the analysis context carries no
project. Every such call read from and wrote to one namespace shared by all
tenants: the leak the docstring warned about, reached through the fallback
rather than through the key.

With no project there is no safe key, so there is now no cache. A miss costs an
analysis; a shared hit costs another tenant's evidence.
"""
from __future__ import annotations

import json

import pytest

from app.services import agent

pytestmark = pytest.mark.regression

PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
FAILURE = ("test_login", "AssertionError: expected 200, got 500", "Traceback ...")


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets: list[str] = []
        self.sets: list[str] = []

    async def get(self, key):
        self.gets.append(key)
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.sets.append(key)
        self.store[key] = value
        return True


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


ANALYSIS = {
    "root_cause": "service returned 500",
    "failure_category": "product_defect",
    "confidence_score": 0.9,
}


# ── The fallback is gone ─────────────────────────────────────────────────


def test_there_is_no_key_without_a_project():
    assert agent._scoped_cache_key(*FAILURE, None) is None
    assert agent._scoped_cache_key(*FAILURE, "") is None


def test_every_key_that_exists_is_project_scoped():
    key = agent._scoped_cache_key(*FAILURE, PROJECT_A)
    assert key is not None and key.endswith(f":proj:{PROJECT_A}")


def test_two_projects_never_share_a_key():
    assert agent._scoped_cache_key(*FAILURE, PROJECT_A) != agent._scoped_cache_key(
        *FAILURE, PROJECT_B
    )


# ── An unscoped call does not touch the cache at all ─────────────────────


@pytest.mark.asyncio
async def test_an_unscoped_store_writes_nothing(redis):
    await agent._store_analysis_cache(*FAILURE, dict(ANALYSIS), None)
    assert redis.sets == [], (
        "an analysis with no project was written to the shared, unscoped "
        "cache key — any other tenant's unscoped call with the same failure "
        "text would be served its evidence"
    )


@pytest.mark.asyncio
async def test_an_unscoped_lookup_reads_nothing(redis):
    """A read must not reach the shared namespace either.

    Pre-seeded with what the old code would have written there, so a lookup
    that still consulted it would visibly return someone else's analysis.
    """
    legacy_unscoped_key = agent.compute_analysis_cache_key(*FAILURE)
    redis.store[legacy_unscoped_key] = json.dumps({"root_cause": "ANOTHER TENANT"})

    result = await agent._check_analysis_cache(*FAILURE, None)

    assert result is None
    assert redis.gets == [], "the unscoped cache namespace was read"


# ── Scoped behaviour is unchanged ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scoped_analysis_round_trips(redis):
    await agent._store_analysis_cache(*FAILURE, dict(ANALYSIS), PROJECT_A)
    assert redis.sets and redis.sets[0].endswith(f":proj:{PROJECT_A}")

    hit = await agent._check_analysis_cache(*FAILURE, PROJECT_A)
    assert hit is not None
    assert hit["root_cause"] == ANALYSIS["root_cause"]


@pytest.mark.asyncio
async def test_another_project_does_not_see_it(redis):
    await agent._store_analysis_cache(*FAILURE, dict(ANALYSIS), PROJECT_A)
    assert await agent._check_analysis_cache(*FAILURE, PROJECT_B) is None
