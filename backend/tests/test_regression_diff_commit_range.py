"""W10 FAIL-09: the run Changes tab lists the run's STORED per-project commit range, and says why when it cannot.

``GET /runs/{id}/regression-diff`` (``routers/runs.get_regression_diff``) and
``compute_regression_diff`` run for real on a session double that answers the
diff's own reads (the run, its baseline, the failing tests, the base run's
build number); the stored range and the project's GitHub integration come
through the seams ``commit_attribution_service`` and ``github_target`` read.
Every outbound ``httpx`` client of the module is routed to the shared contract
fake, so "no HTTP" is the fake's empty request log.

What these pin:

* offline: the route makes ZERO requests (the lazy resolve is skipped) and the
  status reads ``offline_mode``;
* a stored connector range: the diff lists exactly the Suspects range (the same
  shas and links as ``GET /runs/{id}/commit-range`` over the same row), the
  links on the project's GHE host, the count from the range (not the listed
  length);
* every reason of ``COMMIT_RANGE_REASONS`` from its world, and the status
  never decrypts a PAT (``read_secret`` is a spy that fails the test);
* the env ``GITHUB_TOKEN`` / ``GITHUB_REPO`` change nothing (no request to
  api.github.com);
* the lazy resolve: within the 6 h cooldown a GET resolves nothing; past it,
  the request's read connection is handed back BEFORE the write session opens,
  and offline nothing is resolved at all;
* (follow-up, found in the local demo) a GET never persists a gated outcome:
  while the project's GitHub target is unavailable, or the run has no commit
  hash, a view writes no row; a row written while the gate was shut (no base
  commit although a baseline run exists) reads ``not_resolved`` -- not
  ``no_baseline_commit`` -- and the first view after the gate opens resolves
  it; a row that reached the connector (base commit set) keeps its cooldown.
"""
from __future__ import annotations

import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.routers import commit_attribution as commit_router
from app.routers import runs as runs_router
from app.services import commit_attribution_service as cas
from app.services import github_checks_service, regression_diff_service, secret_service
from tests.fakes.fake_integrations import FakeState, create_app, demo_sha

NOW = datetime.now(timezone.utc)
GHE = "https://ghe.fake.test/api/v3"


class _Rows:
    def __init__(self, value=None, rows=None):
        self._value, self._rows = value, rows or []

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._rows)


class _DiffSession:
    """The diff's own reads, answered from the world; records statements and
    when the request's connection is handed back."""

    def __init__(self, world):
        self.world = world
        self.statements: list[str] = []
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        self.statements.append(sql)
        w = self.world
        if sql.startswith("SELECT test_runs.build_number"):
            return _Rows(w.base_run.build_number if params["id_1"] == w.base_run.id else None)
        if "FROM test_runs" in sql and "test_runs.pass_rate >=" in sql:
            return _Rows(w.baseline)
        if "FROM test_runs" in sql and "coalesce(test_runs.end_time, test_runs.created_at) <" in sql:
            return _Rows(w.anchor)  # resolve_base_anchor: the run a range would start after
        if "FROM test_runs" in sql:
            return _Rows(w.run if params["id_1"] == w.run.id else None)
        if "FROM test_cases" in sql:
            failing = w.failing if params["test_run_id_1"] == w.run.id else []
            return _Rows(rows=[SimpleNamespace(test_fingerprint=f, test_name=f"t-{f}", suite_name="s")
                               for f in failing])
        raise AssertionError(f"unexpected statement: {sql}")

    async def close(self):
        self.world.events.append("request-connection-released")


def _row(source="connector", commits=None, base_commit=None, resolved_at=NOW, head=None, base_run_id=None):
    return SimpleNamespace(
        source=source, base_source="green_baseline" if base_commit else "unavailable",
        commits=commits if commits is not None else [], base_commit=base_commit,
        head_commit=head, base_run_id=base_run_id, resolved_at=resolved_at,
    )


def _commits():
    return [
        {"sha": demo_sha("acme/payments", n), "author": "Ben Dev", "message": f"commit {n}",
         "files": [f"payments/{n}.py"], "committed_at": f"2026-10-20T1{n}:00:00Z"}
        for n in (1, 2, 3)
    ]


@pytest.fixture
def world(monkeypatch):
    project = uuid.uuid4()
    base_run = SimpleNamespace(id=uuid.uuid4(), build_number="41")
    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=project, commit_hash=demo_sha("acme/payments", 3), ci_repo="acme/payments",
        branch="main", pass_rate=80.0, created_at=NOW, build_number="42", start_time=NOW, end_time=NOW,
    )
    baseline = SimpleNamespace(id=base_run.id, pass_rate=100.0, build_number="41", end_time=NOW - timedelta(hours=1))
    w = SimpleNamespace(
        project=project, run=run, base_run=base_run, baseline=baseline, failing=["fp-1"], events=[],
        row=None, flag=True, anchor=None,
        integration=SimpleNamespace(project_id=project, enabled=True, api_base_url=GHE, repo_owner="acme",
                                    repo_name="payments", has_pat=True),
    )
    w.row = _row(commits=_commits(), base_commit=demo_sha("acme/payments", 0), head=run.commit_hash,
                 base_run_id=base_run.id)

    async def get_row(_db, run_id):
        return w.row if run_id == run.id else None

    async def get_integration(_db, project_id):
        return w.integration if str(project_id) == str(project) else None

    async def post_allowed(_db=None, *, project_id=None, **_kw):
        return w.flag

    async def secret_spy(*_args, **_kwargs):
        raise AssertionError("the Changes tab's status decrypted a PAT")

    fake = FakeState()
    real_async_client = httpx.AsyncClient

    def routed_client(*_args, **_kwargs):
        return real_async_client(transport=httpx.ASGITransport(app=create_app(fake)))

    proxy = types.SimpleNamespace(**{k: getattr(httpx, k) for k in dir(httpx) if not k.startswith("_")})
    proxy.AsyncClient = routed_client

    async def resolved(_write_db, _run_id):
        # What resolve_commit_range stores once the gate is open: the
        # connector's range (the PostgreSQL test runs the real one).
        if w.row is None:
            w.row = _row(commits=_commits(), base_commit=demo_sha("acme/payments", 0), head=run.commit_hash,
                         base_run_id=base_run.id)
        return {"source": "connector"}

    resolve = AsyncMock(side_effect=resolved)

    class _WriteSession:
        async def __aenter__(self):
            w.events.append("write-session-opened")
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, stmt):
            sql = str(stmt.compile(dialect=postgresql.dialect()))
            assert sql.startswith("DELETE FROM run_commit_ranges"), sql
            assert "run_commit_ranges.base_commit IS NULL" in sql and "run_commit_ranges.source = " in sql
            w.events.append("gated-row-deleted")
            if w.row is not None and w.row.source == "unavailable" and w.row.base_commit is None:
                w.row = None

        async def commit(self):
            w.events.append("write-session-committed")

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(cas, "_get_row", get_row)
    monkeypatch.setattr(cas, "get_integration", get_integration)
    monkeypatch.setattr(github_checks_service, "get_integration", get_integration)
    monkeypatch.setattr(github_checks_service, "_post_allowed", post_allowed)
    monkeypatch.setattr(cas, "_post_allowed", post_allowed)
    monkeypatch.setattr(secret_service, "read_secret", secret_spy)
    monkeypatch.setattr(cas, "httpx", proxy)
    monkeypatch.setattr(cas, "resolve_commit_range", resolve)
    monkeypatch.setattr(commit_router, "AsyncSessionLocal", _WriteSession)
    w.fake, w.resolve = fake, resolve
    w.session = lambda: _DiffSession(w)
    return w


async def _get(world):
    db = world.session()
    return await runs_router.get_regression_diff(run_id=world.run.id, db=db, _=None), db


@pytest.mark.asyncio
async def test_offline_the_route_makes_no_request_and_says_offline(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    world.row = None  # nothing stored: online this would resolve lazily

    diff, _db = await _get(world)

    assert diff["commit_range"] == [] and diff["commit_count"] == 0
    assert diff["commit_range_status"]["reason"] == "offline_mode"
    assert diff["commit_range_status"]["available"] is False
    world.resolve.assert_not_awaited()
    assert world.fake.requests == []
    assert "write-session-opened" not in world.events


@pytest.mark.asyncio
async def test_a_stored_range_lists_exactly_the_suspects_range_with_ghe_links(world):
    diff, _db = await _get(world)
    suspects = await commit_router.get_run_commit_range(run_id=world.run.id, db=world.session(), _=None)

    listed = [(c["full_sha"], c["commit_url"]) for c in diff["commit_range"]]
    assert listed == [(c["sha"], c["commit_url"]) for c in suspects["commits"]]
    assert [c["commit_url"] for c in diff["commit_range"]] == [
        f"https://ghe.fake.test/acme/payments/commit/{demo_sha('acme/payments', n)}" for n in (1, 2, 3)
    ]
    first = diff["commit_range"][0]
    assert first["sha"] == demo_sha("acme/payments", 1)[:8]
    assert (first["message"], first["author"], first["timestamp"]) == ("commit 1", "Ben Dev", "2026-10-20T11:00:00Z")
    status = diff["commit_range_status"]
    assert (status["available"], status["reason"], status["source"], status["commit_count"]) == (
        True, None, "connector", 3)
    assert (status["base_build_number"], status["base_run_id"]) == ("41", str(world.base_run.id))
    assert status["base_anchor_is_strong"] is True
    assert world.fake.requests == [] and world.resolve.await_count == 0


@pytest.mark.asyncio
async def test_the_count_is_the_ranges_not_the_listed_length(world):
    many = [{"sha": f"{n:040x}", "message": f"c{n}", "author": "a", "committed_at": None} for n in range(70)]
    world.row = _row(commits=many, base_commit="0" * 40, head=world.run.commit_hash, base_run_id=world.base_run.id)

    diff = await regression_diff_service.compute_regression_diff(world.run, world.session())

    assert len(diff["commit_range"]) == 50
    assert diff["commit_count"] == 70 == diff["commit_range_status"]["commit_count"]


def _unavailable(**kw):
    return _row(source="unavailable", **kw)


REASONS = {
    "offline_mode": lambda w: setattr(settings, "AI_OFFLINE_MODE", True),
    "no_commit_hash": lambda w: setattr(w.run, "commit_hash", None),
    "no_integration": lambda w: setattr(w, "integration", None),
    "integration_disabled": lambda w: setattr(w.integration, "enabled", False),
    "repo_mismatch": lambda w: setattr(w.run, "ci_repo", "acme/checkout"),
    "github_checks_off": lambda w: setattr(w, "flag", False),
    "no_token": lambda w: setattr(w.integration, "has_pat", False),
    "not_resolved": lambda w: None,  # no row at all
    "no_baseline_commit": lambda w: setattr(w, "row", _unavailable()),
    "fetch_failed": lambda w: setattr(w, "row", _unavailable(base_commit="a" * 40, head="b" * 40)),
    "no_commits": lambda w: setattr(w, "row", _row(source="connector", base_commit="a" * 40, head="b" * 40)),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(REASONS))
async def test_each_reason_from_its_world(world, monkeypatch, reason):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    world.row = None
    REASONS[reason](world)

    diff = await regression_diff_service.compute_regression_diff(world.run, world.session())

    status = diff["commit_range_status"]
    assert (status["available"], status["reason"]) == (False, reason)
    assert reason in regression_diff_service.COMMIT_RANGE_REASONS
    assert diff["commit_range"] == [] and world.fake.requests == []


def test_the_vocabulary_is_the_contracts():
    assert regression_diff_service.COMMIT_RANGE_REASONS == (
        "offline_mode", "github_checks_off", "no_integration", "integration_disabled", "repo_mismatch",
        "no_token", "no_commit_hash", "no_baseline_commit", "not_resolved", "fetch_failed", "no_commits",
    )
    assert set(REASONS) == set(regression_diff_service.COMMIT_RANGE_REASONS)


@pytest.mark.asyncio
async def test_the_env_github_token_and_repo_change_nothing(world, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_env_token_must_not_be_used")
    monkeypatch.setattr(settings, "GITHUB_REPO", "acme/payments")
    world.integration = None
    world.row = _unavailable(base_commit="a" * 40, resolved_at=NOW - timedelta(hours=1))

    diff, _db = await _get(world)

    assert diff["commit_range"] == []
    assert diff["commit_range_status"]["reason"] == "no_integration"
    assert world.fake.requests == [], "the env token reached GitHub"


@pytest.mark.asyncio
async def test_within_the_cooldown_a_get_resolves_nothing(world):
    """(c) A row that reached the connector (a base commit was anchored and the
    fetch failed) keeps the 6 h cooldown, even with a baseline run present."""
    world.anchor = world.baseline
    world.anchor.commit_hash = demo_sha("acme/payments", 0)
    world.row = _unavailable(base_commit="a" * 40, head="b" * 40, resolved_at=NOW - timedelta(hours=1))

    first, _db = await _get(world)
    await _get(world)

    world.resolve.assert_not_awaited()
    assert world.events == []
    assert first["commit_range_status"]["reason"] == "fetch_failed"


# ── Follow-up: a GET never persists a gated outcome ─────────────────────────

GATED = {
    "github_checks_off": lambda w: setattr(w, "flag", False),
    "no_integration": lambda w: setattr(w, "integration", None),
    "integration_disabled": lambda w: setattr(w.integration, "enabled", False),
    "repo_mismatch": lambda w: setattr(w.run, "ci_repo", "acme/checkout"),
    "no_token": lambda w: setattr(w.integration, "has_pat", False),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(GATED))
async def test_a_view_while_the_target_is_unavailable_writes_no_row(world, reason):
    """(a) The view used to run resolve_commit_range, which staged an
    ``unavailable`` row that then read ``no_baseline_commit`` for 6 h after the
    admin fixed the integration."""
    world.row = None
    world.anchor = world.baseline
    GATED[reason](world)

    diff, _db = await _get(world)

    world.resolve.assert_not_awaited()
    assert world.events == [], "a gated view opened a write session"
    assert world.row is None
    assert diff["commit_range_status"]["reason"] == reason


@pytest.mark.asyncio
async def test_a_view_of_a_run_without_a_commit_hash_writes_no_row(world):
    world.row = None
    world.run.commit_hash = None

    diff, _db = await _get(world)

    world.resolve.assert_not_awaited()
    assert world.events == [] and diff["commit_range_status"]["reason"] == "no_commit_hash"


def _gated_row():
    """What a view or finalize stored while the gate was shut: no base, no head."""
    return _unavailable(base_commit=None, head=None, resolved_at=NOW - timedelta(minutes=5))


@pytest.mark.asyncio
async def test_a_gated_row_reads_not_resolved_not_no_baseline_commit(world):
    world.anchor = world.baseline
    world.anchor.commit_hash = demo_sha("acme/payments", 0)
    world.row = _gated_row()

    diff = await regression_diff_service.compute_regression_diff(world.run, world.session())

    assert diff["commit_range_status"]["reason"] == "not_resolved"


@pytest.mark.asyncio
async def test_once_the_gate_opens_the_next_view_resolves_a_gated_row(world):
    """(b) Within the cooldown, but the row never reached the connector: it is
    deleted (committed, so the service's own read no longer sees it) and the
    range is resolved; the tab lists the commits."""
    world.anchor = world.baseline
    world.anchor.commit_hash = demo_sha("acme/payments", 0)
    world.row = _gated_row()

    diff, _db = await _get(world)

    world.resolve.assert_awaited_once()
    assert world.events == [
        "request-connection-released", "write-session-opened", "gated-row-deleted",
        "write-session-committed", "write-session-committed",
    ]
    assert diff["commit_range_status"]["reason"] is None
    assert [c["full_sha"] for c in diff["commit_range"]] == [demo_sha("acme/payments", n) for n in (1, 2, 3)]


@pytest.mark.asyncio
async def test_a_genuine_no_baseline_row_is_left_alone_within_the_cooldown(world):
    """No anchor exists: the row's ``no_baseline_commit`` is true, and a view
    must not rewrite it on every request."""
    world.anchor = None
    world.row = _gated_row()

    diff, _db = await _get(world)
    await _get(world)

    world.resolve.assert_not_awaited()
    assert world.events == []
    assert diff["commit_range_status"]["reason"] == "no_baseline_commit"


@pytest.mark.asyncio
async def test_past_the_cooldown_the_read_connection_is_handed_back_before_the_write(world):
    world.row = _unavailable(base_commit="a" * 40, head="b" * 40, resolved_at=NOW - timedelta(hours=7))

    await _get(world)

    world.resolve.assert_awaited_once()
    assert world.resolve.await_args.args[1] == world.run.id
    assert world.events == [
        "request-connection-released", "write-session-opened", "write-session-committed",
    ]
