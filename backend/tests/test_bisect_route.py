"""W23b FAIL-12: ``GET /api/v1/runs/{run_id}/bisect?fingerprint=`` and its online compare (§2.1 B-3/B-4, §6.3 G-B).

The real router, the real ``require_run_access`` guard, the real
``bisect_service`` and the real ``commit_attribution_service`` fetchers run on a
session double that answers the guard, the run load, the fingerprint check, the
ONE narrowing statement (rows from the world) and the locator read. The git
host is the shared contract fake (``tests/fakes/fake_integrations.py``) behind
the ``_gh_get`` seam through ``httpx.ASGITransport``; Redis is a dict double.

What these pin: the QUERY fingerprint is scoped to the PATH run (another run's,
another project's -- the same md5 -- or an unknown fingerprint answer the same
404 body, and the statement never runs); the guard refuses another project's
bound key; VIEWER reads. Offline and the gate states touch neither Redis nor
HTTP; the GitHub three-dot compare (``good...bad``) and the GitLab ``from`` /
``to`` compare go through the fake; a cache hit and a cached failure make no
HTTP call; Redis down / slow -> ``cache_unavailable`` and no HTTP; a timeout ->
``timed_out`` and nothing cached; an SSRF-blocked base -> ``fetch_failed``; the
read connection is handed back before Redis and HTTP; no add / flush / commit;
the ``bisect_answered`` line carries no test name, branch or SHA; the narrowed
list is a subset of, and shorter than, the run-level range.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

from app.core.config import settings
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.routers import commit_attribution as commit_router
from app.services import bisect_service
from app.services import commit_attribution_service as cas
from app.services import github_checks_service
from app.services.bisect_vocab import BISECT_ERRORS, COMPARE_CACHE_KEY
from tests.fakes.fake_integrations import FakeState, create_app, demo_sha

GHE = "https://ghe.fake.test/api/v3"
GITLAB = "https://gitlab.fake.test"
REPO = "acme/payments"
GL_REPO = "group/api-gateway"
TEST_NAME = "checkout_sentinel_test"
BRANCH = "feature/sentinel-pay"


def _fp(test_name: str, class_name: str) -> str:
    # The fingerprint is md5(test_name:class_name) (live_session_drainer.py): NOT project-unique.
    return hashlib.md5(f"{test_name}:{class_name}".encode()).hexdigest()


FP = _fp(TEST_NAME, "payments.authorize.AuthorizeTest")
FP_OTHER_RUN = _fp("cart_sentinel_test", "payments.cart.CartTest")
FP_OTHER_PROJECT = _fp("refund_sentinel_test", "payments.refund.RefundTest")


class _Result:
    def __init__(self, value=None, rows=(), mappings=(), first=None):
        self._value, self._rows, self._mappings, self._first = value, list(rows), list(mappings), first

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._first

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def mappings(self):
        return SimpleNamespace(all=lambda: list(self._mappings))


class _Session:
    """Answers the route's reads from the world; spies every write; records the release."""

    def __init__(self, world):
        self.w = world
        self.new, self.dirty, self.deleted = [], [], []
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.narrow_calls: list[dict] = []

    async def execute(self, stmt, params=None):
        w = self.w
        if isinstance(stmt, TextClause):
            self.narrow_calls.append(dict(params or {}))
            return _Result(mappings=w.narrow_rows.get((params["run_id"], params["fingerprint"]), []))
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, values = str(compiled), compiled.params
        if "FROM project_members" in sql:
            return _Result(uuid.uuid4() if w.member else None)
        if sql.startswith("SELECT test_runs.project_id"):
            run = w.runs.get(values["id_1"])
            return _Result(run.project_id if run else None)
        if "FROM test_runs" in sql:
            return _Result(w.runs.get(values["id_1"]))
        if sql.startswith("SELECT test_cases.test_name, test_cases.status"):
            case = w.cases.get((values["test_run_id_1"], values["test_fingerprint_1"]))
            return _Result(first=(case.test_name, case.status) if case else None)
        if "FROM test_cases" in sql:  # _target_test_cases: the locator of the one test
            case = w.cases.get((values["test_run_id_1"], values["test_fingerprint_1"]))
            return _Result(rows=[case] if case else [])
        raise AssertionError(f"unexpected statement: {sql[:160]}")

    async def close(self):
        self.w.events.append("released")


class _Redis:
    def __init__(self, world, *, fail=None, slow=False):
        self.w, self.fail, self.slow = world, fail, slow
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, str, int]] = []

    async def get(self, key):
        self.w.events.append("redis-get")
        if self.slow:
            await asyncio.sleep(1)
        if self.fail:
            raise self.fail
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.sets.append((key, value, ex))
        self.store[key] = value
        return True


class _Recorder:
    def __init__(self):
        self.lines: list[tuple[str, dict]] = []

    def _rec(self, event, **kw):
        self.lines.append((event, kw))

    info = warning = debug = error = _rec


def _commit(repo, n, author, message, files):
    start = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    return {"sha": demo_sha(repo, n), "author": author, "message": message, "files": files,
            "date": (start + timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _github_history():
    """acme/payments, oldest -> newest: the run-level range is 1..6, the narrowed one 3..5."""
    return [
        _commit(REPO, 0, "Ana Lead", "Bootstrap the payment service", ["payments/app.py"]),
        _commit(REPO, 1, "Ben Dev", "Raise the client timeout", ["payments/client.py"]),
        _commit(REPO, 2, "Ben Dev", "Shrink the DB pool", ["payments/db.py"]),
        _commit(REPO, 3, "Cara QA", "Tidy the README", ["README.md"]),
        _commit(REPO, 4, "Dan Dev", "Retry card authorisation once on 503", ["payments/authorize.py"]),
        _commit(REPO, 5, "Eve Ops", "Bump the base image", ["docker/Dockerfile"]),
        _commit(REPO, 6, "Ben Dev", "Log the gateway latency", ["payments/gateway.py"]),
    ]


def _row(run, status, *, url=None):
    return {"part": "series", "id": run.id, "build_number": run.build_number, "commit_hash": run.commit_hash,
            "branch": run.branch, "created_at": run.created_at, "ci_run_url": url, "test_status": status}


def _case(test_name, class_name, status="FAILED"):
    return SimpleNamespace(test_name=test_name, class_name=class_name, package_name=None, status=status,
                           stack_trace=None, error_message=None)


def _build_world(now):
    p1, p2 = uuid.uuid4(), uuid.uuid4()

    def run(project, build, commit_n, hours_ago, repo=REPO):
        return SimpleNamespace(id=uuid.uuid4(), project_id=project, build_number=build,
                               commit_hash=demo_sha(repo, commit_n), branch=BRANCH,
                               created_at=now - timedelta(hours=hours_ago), ci_repo=repo, status="FAILED")

    # The series, newest first: 106 fail (the run), 105 fail (bad), 104 not run, 103 skipped,
    # 102 pass (good), 101 pass.
    history = [run(p1, str(106 - i), c, i) for i, c in enumerate((6, 5, 4, 4, 2, 1))]
    the_run, other_run, p2_run = history[0], run(p1, "99", 6, 9), run(p2, "7", 6, 0)
    statuses = ("FAILED", "FAILED", None, "SKIPPED", "PASSED", "PASSED")
    urls = (None, "https://ci.example.test/job/pay/105", None, None, "javascript:alert(1)", None)
    fake = FakeState()
    fake.github_commits[REPO] = _github_history()
    return SimpleNamespace(
        now=now, p1=p1, p2=p2, run=the_run, other_run=other_run, p2_run=p2_run, history=history,
        runs={r.id: r for r in (other_run, p2_run, *history)},
        cases={
            (the_run.id, FP): _case(TEST_NAME, "payments.authorize.AuthorizeTest"),
            (other_run.id, FP_OTHER_RUN): _case("cart_sentinel_test", "payments.cart.CartTest"),
            (p2_run.id, FP): _case(TEST_NAME, "payments.authorize.AuthorizeTest"),
            (p2_run.id, FP_OTHER_PROJECT): _case("refund_sentinel_test", "payments.refund.RefundTest"),
        },
        narrow_rows={(the_run.id, FP): [_row(r, s, url=u) for r, s, u in zip(history, statuses, urls)]},
        member=True, user=SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True, username="vera"),
        events=[], fake=fake, github_gate=True, host="github",
        row=SimpleNamespace(source="connector", base_source="green_baseline", commits=_github_history()[1:]),
        integration=SimpleNamespace(project_id=p1, enabled=True, api_base_url=GHE, repo_owner="acme",
                                    repo_name="payments", has_pat=True),
    )


@pytest.fixture
def world(monkeypatch):
    w = _build_world(datetime.now(timezone.utc))
    w.redis = _Redis(w)
    w.slog = _Recorder()
    fake_app = create_app(w.fake)

    async def gh_get(url, headers, params=None, client=None):
        w.events.append("http")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_app)) as c:
            return await c.get(url, headers=headers, params=params)

    async def post_allowed(_db=None, *, project_id=None, **_kw):
        return w.github_gate

    async def connector_target(_db, _run):
        return cas._ConnectorTarget(api_base=GHE, repo=REPO, pat="fake-gh-pat") if w.host == "github" else None

    async def gitlab_target(_db, _run):
        if w.host != "gitlab":
            return None
        return cas.GitLabConnectorTarget(base_url=GITLAB, project_path=GL_REPO, pat="fake-gitlab-pat")

    async def get_row(_db, run_id):
        return w.row if run_id == w.run.id else None

    async def get_integration(_db, project_id):
        return w.integration if str(project_id) == str(w.p1) else None

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(cas, "_gh_get", gh_get)
    monkeypatch.setattr(cas, "_ssrf_block_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(cas, "_connector_target", connector_target)
    monkeypatch.setattr(cas, "_gitlab_connector_target", gitlab_target)
    monkeypatch.setattr(cas, "_get_row", get_row)
    monkeypatch.setattr(cas, "get_integration", get_integration)
    monkeypatch.setattr(github_checks_service, "get_integration", get_integration)
    monkeypatch.setattr(github_checks_service, "_post_allowed", post_allowed)
    monkeypatch.setattr(bisect_service, "get_redis", lambda: w.redis)
    monkeypatch.setattr(bisect_service, "_slog", w.slog)
    monkeypatch.setattr(commit_router, "lazy_resolve_commit_range",
                        AsyncMock(side_effect=AssertionError("the bisect GET lazily resolved a range")))
    w.session = _Session(w)

    async def _db():
        yield w.session

    app = FastAPI()
    app.include_router(commit_router.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: w.user
    w.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testlookup.test")
    w.fake_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_app), base_url="http://fake.test")
    return w


async def _bisect(w, run_id=None, fingerprint=FP):
    return await w.http.get(f"/api/v1/runs/{run_id or w.run.id}/bisect", params={"fingerprint": fingerprint})


async def _git_requests(w):
    log = (await w.fake_client.get("/_fake/requests")).json()
    return [r for r in log if r.get("service") in ("github", "gitlab")]


def _no_writes(w):
    assert not w.session.add.called and not w.session.flush.called and not w.session.commit.called
    assert not w.session.rollback.called


# ── Scoping: the QUERY id belongs to the PATH run; the guard owns the path ──────


async def test_any_fingerprint_not_of_this_run_is_the_same_404(world):
    expected = {"detail": {"code": "fingerprint_not_in_run", "message": BISECT_ERRORS["fingerprint_not_in_run"]}}
    assert FP_OTHER_PROJECT != FP  # another project's test, absent from this run
    for fingerprint in (FP_OTHER_RUN, FP_OTHER_PROJECT, "fp-sentinel-unknown"):
        resp = await _bisect(world, fingerprint=fingerprint)
        assert resp.status_code == 404, fingerprint
        assert resp.json() == expected
    assert world.session.narrow_calls == []  # the walk never ran
    assert world.redis.sets == [] and "http" not in world.events


async def test_the_same_md5_in_another_project_is_not_readable(world):
    # Both projects share the test (the same fingerprint); a P1 member cannot read P2's run.
    world.member = False
    resp = await _bisect(world, run_id=world.p2_run.id, fingerprint=FP)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    assert world.session.narrow_calls == []


async def test_a_key_bound_to_another_project_is_refused_by_the_guard(world):
    key = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", is_active=True, username="ci-bot")
    key._testlookup_api_key_project_id = world.p2
    world.user = key
    resp = await _bisect(world)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    assert world.session.narrow_calls == []


async def test_viewer_reads_and_a_get_writes_nothing(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    resp = await _bisect(world)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["run_id"], body["fingerprint"], body["test_name"]) == (str(world.run.id), FP, TEST_NAME)
    _no_writes(world)
    assert world.session.narrow_calls[0]["project_id"] == world.p1  # the walk is project-scoped
    assert world.session.narrow_calls[0]["series_key"] == BRANCH


@pytest.mark.parametrize("params", [{}, {"fingerprint": ""}, {"fingerprint": "x" * 65}])
async def test_a_missing_or_over_long_fingerprint_is_422(world, params):
    resp = await world.http.get(f"/api/v1/runs/{world.run.id}/bisect", params=params)
    assert resp.status_code == 422


# ── Offline and the gate: no Redis, no HTTP ─────────────────────────────────────


async def test_offline_answers_the_two_builds_and_touches_neither_redis_nor_http(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    body = (await _bisect(world)).json()
    h = world.history
    assert (body["state"], body["anchor"], body["series_branch"]) == ("narrowed", "same_branch", BRANCH)
    assert body["good"]["run_id"] == str(h[4].id) and body["good"]["outcome"] == "pass"
    assert body["bad"]["run_id"] == str(h[1].id) and body["bad"]["commit_hash"] == demo_sha(REPO, 5)
    assert body["bad"]["ci_run_url"] == "https://ci.example.test/job/pay/105"
    assert body["good"]["ci_run_url"] is None  # "javascript:" never reaches an href
    assert [r["run_id"] for r in body["untested_runs"]] == [str(h[2].id), str(h[3].id)]
    assert body["untested_count"] == 2 and body["streak_length"] == 2
    assert (body["runs_read"], body["bounded"], body["narrowed_from"]) == (6, False, "green_baseline")
    compare = body["compare"]
    assert (compare["state"], compare["reason"], compare["commits"]) == ("unavailable", "offline_mode", [])
    assert compare["run_level_commit_count"] == 6
    assert "redis-get" not in world.events and "http" not in world.events
    assert world.redis.sets == [] and await _git_requests(world) == []


@pytest.mark.parametrize("good_commit, expected", [
    (lambda: demo_sha(REPO, 5).upper(), "same_commit"),
    (lambda: None, "no_good_commit"),
])
async def test_gate_states_never_reach_redis(world, good_commit, expected):
    rows = world.narrow_rows[(world.run.id, FP)]
    rows[4] = {**rows[4], "commit_hash": good_commit()}
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"]) == ("not_attempted", expected)
    assert "redis-get" not in world.events and "http" not in world.events


@pytest.mark.parametrize("integration, gate, expected", [
    (None, True, "no_integration"),
    ("present", False, "github_checks_off"),
])
async def test_no_git_host_target_is_unavailable_with_its_reason(world, integration, gate, expected):
    world.host, world.github_gate = None, gate
    if integration is None:
        world.integration = None
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"]) == ("unavailable", expected)
    assert "redis-get" not in world.events and "http" not in world.events


async def test_a_run_whose_test_passed_is_not_failing(world):
    rows = world.narrow_rows[(world.run.id, FP)]
    rows[0] = {**rows[0], "test_status": "PASSED"}
    body = (await _bisect(world)).json()
    assert (body["state"], body["good"], body["bad"]) == ("not_failing", None, None)
    assert (body["compare"]["state"], body["compare"]["reason"]) == ("not_attempted", "not_narrowed")


# ── Online: the host's compare through the fake, cached by the SHA pair ─────────


async def test_github_three_dot_compare_ranked_and_narrower_than_the_run_level_range(world):
    good, bad = demo_sha(REPO, 2), demo_sha(REPO, 5)
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"], compare["host"], compare["cached"]) == (
        "fetched", None, "github", False)
    asked = await _git_requests(world)
    assert asked[0]["path"] == f"/api/v3/repos/{REPO}/compare/{good}...{bad}"
    assert asked[0]["auth"] == "ok"
    narrowed = [c["sha"] for c in compare["commits"]]
    assert set(narrowed) == {demo_sha(REPO, n) for n in (3, 4, 5)}
    run_level = {c["sha"] for c in world.row.commits}
    assert set(narrowed) < run_level and compare["commit_count"] == 3 < compare["run_level_commit_count"] == 6
    top = compare["commits"][0]
    assert top["sha"] == demo_sha(REPO, 4)  # touched payments/authorize.py, the failing test's module
    assert top["rationale"]["overlapping_files"] == ["payments/authorize.py"]
    assert top["commit_url"] == f"https://ghe.fake.test/{REPO}/commit/{demo_sha(REPO, 4)}"
    assert compare["has_location_signal"] is True and compare["caveat"] == cas.MONOREPO_CAVEAT
    assert compare["truncated"] is False
    key = COMPARE_CACHE_KEY.format(project_id=world.p1, host="github", good=good, bad=bad)
    [(stored_key, stored, ttl)] = world.redis.sets
    assert (stored_key, ttl) == (key, 3600)
    assert [c["sha"] for c in json.loads(stored)["commits"]] == [demo_sha(REPO, n) for n in (3, 4, 5)]
    _no_writes(world)


async def test_gitlab_compare_from_to(world):
    world.host, world.github_gate = "gitlab", False
    rows = world.narrow_rows[(world.run.id, FP)]
    rows[1] = {**rows[1], "commit_hash": demo_sha(GL_REPO, 2)}
    rows[4] = {**rows[4], "commit_hash": demo_sha(GL_REPO, 0)}
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["host"], compare["commit_count"]) == ("fetched", "gitlab", 2)
    [asked] = await _git_requests(world)
    assert asked["path"] == "/api/v4/projects/group/api-gateway/repository/compare"  # the fake logs it decoded
    assert asked["query"] == {"from": demo_sha(GL_REPO, 0), "to": demo_sha(GL_REPO, 2)}
    urls = {c["sha"]: c["commit_url"] for c in compare["commits"]}
    assert urls[demo_sha(GL_REPO, 2)] == f"{GITLAB}/{GL_REPO}/-/commit/{demo_sha(GL_REPO, 2)}"


async def test_a_cache_hit_makes_no_http_call(world):
    first = (await _bisect(world)).json()["compare"]
    calls = len(await _git_requests(world))
    world.events.clear()
    second = (await _bisect(world)).json()["compare"]
    assert (second["state"], second["cached"]) == ("fetched", True)
    assert [c["sha"] for c in second["commits"]] == [c["sha"] for c in first["commits"]]
    assert len(await _git_requests(world)) == calls and "http" not in world.events
    assert len(world.redis.sets) == 1


async def test_a_cached_failure_answers_fetch_failed_without_http(world):
    key = COMPARE_CACHE_KEY.format(project_id=world.p1, host="github", good=demo_sha(REPO, 2),
                                   bad=demo_sha(REPO, 5))
    world.redis.store[key] = json.dumps({"failed": True})
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"], compare["cached"]) == ("unavailable", "fetch_failed", True)
    assert "http" not in world.events and world.redis.sets == []


@pytest.mark.parametrize("fault", ["raises", "slow"])
async def test_redis_down_or_slow_is_cache_unavailable_and_no_http(world, monkeypatch, fault):
    monkeypatch.setattr(bisect_service, "CACHE_IO_TIMEOUT_SECONDS", 0.05)
    world.redis.fail = ConnectionError("redis down") if fault == "raises" else None
    world.redis.slow = fault == "slow"
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"]) == ("unavailable", "cache_unavailable")
    assert "http" not in world.events and await _git_requests(world) == []


async def test_a_timeout_is_timed_out_and_nothing_is_cached(world, monkeypatch):
    async def slow_gh_get(url, headers, params=None, client=None):
        world.events.append("http")
        await asyncio.sleep(5)

    monkeypatch.setattr(bisect_service, "COMPARE_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(cas, "_gh_get", slow_gh_get)
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"]) == ("timed_out", "timed_out")
    assert world.redis.sets == []  # a timeout is not a failure


async def test_an_ssrf_blocked_base_is_fetch_failed_remembered_60_s(world, monkeypatch):
    monkeypatch.setattr(cas, "_ssrf_block_reason", AsyncMock(return_value="blocked_target:10.0.0.1"))
    compare = (await _bisect(world)).json()["compare"]
    assert (compare["state"], compare["reason"]) == ("unavailable", "fetch_failed")
    assert "http" not in world.events
    [(_key, stored, ttl)] = world.redis.sets
    assert (json.loads(stored), ttl) == ({"failed": True}, 60)


async def test_the_read_connection_is_released_before_redis_and_http(world):
    await _bisect(world)
    assert world.events[:3] == ["released", "redis-get", "http"]
    _no_writes(world)


async def test_the_answered_line_carries_no_test_name_branch_or_sha(world):
    await _bisect(world)
    [(event, fields)] = [line for line in world.slog.lines if line[0] == "bisect_answered"]
    assert set(fields) == {"run_id", "state", "anchor", "compare_state", "compare_reason", "cached", "runs_read",
                           "untested_count", "commit_count", "duration_ms"}
    assert (fields["state"], fields["compare_state"], fields["commit_count"]) == ("narrowed", "fetched", 3)
    dumped = json.dumps([line[1] for line in world.slog.lines], default=str)
    for secret in (TEST_NAME, BRANCH, FP, *(demo_sha(REPO, n) for n in range(7)),
                   *(demo_sha(REPO, n)[:12] for n in range(7))):
        assert secret not in dumped
