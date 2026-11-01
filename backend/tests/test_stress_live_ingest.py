"""HYG-06 (b) — the live-ingest stress harness's unit-tested parts (``backend/scripts/stress_live_ingest.py``).

No network, ever: the host / port refusals (exit 2 BEFORE any request), the percentile and frame-window maths, the
verdict against the backlog's thresholds (finalize p95 > 30 s, > 10 frames/s per client; within 20 % below =
re-measure; a missing number is never a pass), the batches, and the report rendering. The script imports no
``app.*`` module and imports its network clients lazily. Times are built INSIDE each test.

W25e E-0 (contract §2.0): a fake async client (records ``post`` / ``delete``, scripted statuses, a fake clock that
moves 1 s per request) drives ``_drive_run`` — the SDKs' DELETE after the ``run_complete`` batch, its 204 stamping
``closed_at``, retries honouring ``Retry-After``, the ``finally`` close of a partial run, ``--no-close``; a fake
socket drives the watchers' auth frames; the finalize clock, the frame content, ``acceptance()`` and the new report
sections are pure.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import platform
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stress_live_ingest.py"
PROJECT = "7f3c2a10-5d4e-4b6a-9c8d-1e2f3a4b5c6d"
REFUSED_HOST = (
    "refused: --base-url: the host is not loopback — the stress test runs only against a throwaway local stack\n"
)


@pytest.fixture(scope="module")
def stress():
    spec = importlib.util.spec_from_file_location("_stress_live_ingest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their annotations through sys.modules
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def no_network(monkeypatch, stress):
    """Any attempt to build a client or start the run fails the test: refusals happen before any request."""
    import httpx

    def _boom(*_a, **_k):
        raise AssertionError("a request was attempted")

    async def _no_run(_args):
        raise AssertionError("run_stress was reached")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    monkeypatch.setattr(stress, "run_stress", _no_run)


def _fake_report(stress):
    return stress.summarise(
        config={"runs": 3, "concurrency": 3, "tests_per_run": 10, "batch": 5, "ws_clients": 1,
                "finalize_timeout_s": 900.0, "fail_every": 20, "target_port": 18001},
        finalize_lags_s=[1.0, 2.0, 3.0], censored=0, frame_windows=[1, 2, 3], ws_closes={},
        stream_max=None, pool_max=None, http_errors={}, transport_errors={}, runs_completed=3, wall_s=4.2,
    )


# ── Refusals: exit 2 before any request ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://homelab.example", REFUSED_HOST),
        ("http://192.168.1.10:8000", REFUSED_HOST),
        ("http://0.0.0.0:18001", REFUSED_HOST),
        (
            "http://127.0.0.1:8000",
            "refused: --base-url: port 8000 is the shared dev stack — the stress test runs only against a throwaway "
            "local stack (pass --allow-dev-stack only if this really is a disposable server)\n",
        ),
        (
            "ftp://127.0.0.1:18001",
            "refused: --base-url: an http(s) URL is required — the stress test runs only against a throwaway local "
            "stack\n",
        ),
    ],
    ids=["homelab", "lan-8000", "unspecified", "dev-stack-8000", "not-http"],
)
def test_a_non_local_target_is_refused_with_exit_2_before_any_request(stress, no_network, capsys, url, expected):
    assert stress.main(["--base-url", url, "--project-id", PROJECT]) == 2
    captured = capsys.readouterr()
    assert captured.err == expected
    assert captured.out == ""


@pytest.mark.parametrize(
    ("flag", "url", "expected"),
    [
        ("--dsn", "postgresql://stress:pw@10.0.0.5:5432/df", "refused: --dsn: the host is not loopback"),
        ("--redis-url", "redis://192.168.1.10:6379/0", "refused: --redis-url: the host is not loopback"),
        ("--dsn", "mysql://127.0.0.1:3306/df", "refused: --dsn: expected a postgresql / postgres / postgresql+asyncpg URL"),
    ],
    ids=["dsn-lan", "redis-lan", "dsn-scheme"],
)
def test_a_non_local_store_is_refused_with_exit_2(stress, no_network, capsys, flag, url, expected):
    argv = ["--base-url", "http://127.0.0.1:18001", "--project-id", PROJECT, flag, url]
    assert stress.main(argv) == 2
    assert capsys.readouterr().err == f"{expected} — the stress test runs only against a throwaway local stack\n"


def test_bad_load_numbers_are_refused_before_any_request(stress, no_network, capsys):
    assert stress.main(["--base-url", "http://127.0.0.1:18001", "--project-id", PROJECT, "--batch", "1001"]) == 2
    assert capsys.readouterr().err.startswith("refused: ")


def test_a_project_id_that_is_not_a_uuid_is_an_argument_error(stress, no_network):
    with pytest.raises(SystemExit) as exc:
        stress.main(["--base-url", "http://127.0.0.1:18001", "--project-id", "my-project"])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("url", "extra"),
    [
        ("http://127.0.0.1:18001", []),
        ("http://localhost:18001", []),
        ("http://[::1]:18001", []),
        ("http://127.0.0.1:8000", ["--allow-dev-stack"]),
    ],
    ids=["ipv4-18001", "localhost", "ipv6", "dev-stack-with-flag"],
)
def test_a_loopback_target_is_accepted_and_the_reports_are_written(stress, monkeypatch, capsys, tmp_path, url, extra):
    seen: list[str] = []

    async def _fake_run(args):
        seen.append(args.base_url)
        return _fake_report(stress)

    monkeypatch.setattr(stress, "run_stress", _fake_run)
    out_json, out_md = tmp_path / "stress.json", tmp_path / "stress.md"
    argv = ["--base-url", url, "--project-id", PROJECT, "--output", str(out_json), "--markdown", str(out_md), *extra]

    assert stress.main(argv) == 0

    assert seen == [url]
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["verdict"]["decision"] == "not_needed"
    assert out_md.read_bytes().decode("utf-8") == stress.render_markdown(report)
    assert b"\r\n" not in out_md.read_bytes()
    assert capsys.readouterr().out.startswith("# W25a stress report")


# ── The verdict: the backlog's thresholds ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("finalize_p95", "frames_p95", "decision", "sentence"),
    [
        (31.0, 4.0, "finalize_coalescer", "build the finalize coalescer"),
        (12.0, 11.0, "frame_coalescer", "build the frame coalescer"),
        (12.0, 4.0, "not_needed", "not needed — HYG-06 coalescers close NOT_A_GAP with these numbers"),
        (31.0, 11.0, "both_coalescers", "build the finalize coalescer and the frame coalescer"),
        (25.0, 4.0, "borderline", "borderline — re-measure on the homelab-shaped stack before building"),
        (12.0, 8.5, "borderline", "borderline — re-measure on the homelab-shaped stack before building"),
        (30.0, 10.0, "borderline", "borderline — re-measure on the homelab-shaped stack before building"),
        (23.9, 7.9, "not_needed", "not needed — HYG-06 coalescers close NOT_A_GAP with these numbers"),
    ],
    ids=["finalize", "frames", "neither", "both", "finalize-near", "frames-near", "at-thresholds", "just-clear"],
)
def test_the_verdict_follows_the_backlog_thresholds(stress, finalize_p95, frames_p95, decision, sentence):
    v = stress.verdict(finalize_p95, frames_p95)
    assert (v.decision, v.sentence) == (decision, sentence)


def test_a_missing_measurement_is_never_a_pass(stress):
    v = stress.verdict(None, 4.0)
    assert v.decision == "incomplete"
    assert v.notes == ("finalize p95 was not measured",)
    assert stress.verdict(12.0, None).decision == "incomplete"
    assert stress.verdict(None, None).decision == "incomplete"


def test_a_crossed_threshold_still_decides_when_the_other_number_is_missing_or_near(stress):
    v = stress.verdict(31.0, None)
    assert v.decision == "finalize_coalescer"
    assert v.notes == ("WS frames/s per client p95 was not measured",)
    near = stress.verdict(31.0, 9.0)
    assert near.decision == "finalize_coalescer"
    assert near.notes == ("WS frames/s per client p95 9.0 is within 20 % of the 10 threshold",)


# ── The maths ───────────────────────────────────────────────────────────────────────────────────────────


def test_percentile_is_nearest_rank_and_none_for_nothing(stress):
    values = [float(n) for n in range(1, 101)]
    assert stress.percentile(values, 95) == 95.0
    assert stress.percentile(values, 50) == 50.0
    assert stress.percentile([7.5], 95) == 7.5
    assert stress.percentile([], 95) is None


def test_frames_are_counted_per_whole_second_including_empty_windows(stress):
    start = time.perf_counter()
    stamps = [start + 0.1, start + 0.2, start + 1.5, start + 3.9, start - 1.0, start + 4.0]
    assert stress.frames_per_second_windows(stamps, start, start + 4.0) == [2, 1, 0, 1]
    assert stress.frames_per_second_windows(stamps, start, start) == []


def test_the_batches_open_with_run_start_and_close_with_run_complete_alone(stress):
    now_ms = int(time.time() * 1000)
    batches = stress.build_batches(450, 200, 20, now_ms)
    assert [len(b) for b in batches] == [200, 200, 51, 1]
    assert batches[0][0] == {"event_type": "run_start", "timestamp_ms": now_ms}
    assert batches[-1] == [{"event_type": "run_complete", "timestamp_ms": now_ms + 451}]
    results = [e for b in batches for e in b if e["event_type"] == "test_result"]
    assert len(results) == 450
    assert sum(e["status"] == "FAILED" for e in results) == 22
    assert results[0]["test_name"] == "test_stress_0001"
    assert [len(b) for b in stress.build_batches(10, 5000, 0, now_ms)] == [11, 1]


# ── The report ──────────────────────────────────────────────────────────────────────────────────────────


def test_an_unmeasured_report_reads_not_measured_and_incomplete(stress):
    report = stress.summarise(
        config={"runs": 500, "concurrency": 500, "tests_per_run": 200, "batch": 200, "ws_clients": 0,
                "finalize_timeout_s": 900.0, "fail_every": 20, "target_port": 18001},
        finalize_lags_s=None, censored=0, frame_windows=None, ws_closes={}, stream_max=None, pool_max=None,
        http_errors={"429": 3}, transport_errors={"ReadTimeout": 1}, runs_completed=497, wall_s=61.04,
    )
    assert report["finalize_lag_s"]["measured"] is False and report["ws_frames_per_s"]["measured"] is False
    assert report["verdict"]["decision"] == "incomplete"
    text = stress.render_markdown(report)
    assert "**Verdict: incomplete — re-run with every measurement before deciding.**" in text
    assert "| not measured | not measured | not measured | p95 > 30 s |" in text
    assert "- runs completed: 497 of 500" in text
    assert "- HTTP 429: 3" in text and "- ReadTimeout: 1" in text
    json.dumps(report)  # serialisable as written


def test_a_measured_report_carries_the_numbers_and_the_censored_count(stress):
    report = stress.summarise(
        config={"runs": 4, "concurrency": 4, "tests_per_run": 10, "batch": 5, "ws_clients": 2,
                "finalize_timeout_s": 900.0, "fail_every": 20, "target_port": 18001},
        finalize_lags_s=[2.0, 3.0, 4.0, 900.0], censored=1, frame_windows=[0, 1, 2, 12], ws_closes={"4401": 1},
        stream_max={"live_events": 40}, pool_max={"active": 7, "waiting_lock": 0}, http_errors={},
        transport_errors={}, runs_completed=4, wall_s=12.0,
    )
    assert report["finalize_lag_s"] == {"measured": True, "runs": 4, "censored_at_timeout": 1,
                                        "p50": 3.0, "p95": 900.0, "max": 900.0}
    assert report["verdict"]["decision"] == "both_coalescers"
    text = stress.render_markdown(report)
    assert "- live_events: 40" in text and "- active: 7" in text and "- WebSocket closed 4401: 1" in text


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


def test_the_report_holds_no_credential_or_address_field(stress):
    keys = {k.lower() for k in _all_keys(_fake_report(stress))}
    assert not {k for k in keys if any(word in k for word in ("token", "key", "dsn", "url", "password", "secret"))}


# ── The module's shape ──────────────────────────────────────────────────────────────────────────────────


def test_the_script_imports_no_app_module_and_its_clients_lazily(stress):
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    assert "app" not in top
    assert not top & {"httpx", "websockets", "asyncpg", "redis"}, top
    every = {alias.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
    every |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "app" not in every


def test_the_finalize_predicate_is_the_outbox_leaving_waiting(stress):
    sql = " ".join(stress.FINALIZED_SQL.split())
    assert sql == (
        "SELECT o.run_id::text FROM run_downstream_outbox o WHERE o.run_id = ANY($1::uuid[]) GROUP BY o.run_id "
        "HAVING count(*) FILTER (WHERE o.status = 'waiting') = 0"
    )


# ── W25e E-0: the run closes like the SDKs (fake async client; no network) ──────────────────────────────

SESSION = "5e551011-0000-4000-8000-000000000001"  # the run's id IS the session id
SESSION_2 = "5e551011-0000-4000-8000-000000000002"
CLOSE_URL = f"/api/v1/stream/sessions/{SESSION}"
AUTH = {"X-API-Key": "zz-sentinel-api-q"}  # the credential that opens (and closes) the session
SESSION_HEADER_VALUE = "zz-sentinel-session-q"
SESSIONS_PATH = "/api/v1/stream/sessions"
BATCH_PATH = "/api/v1/stream/events/batch"


class _FakeClock:
    """Stands in for the script's ``time`` module: ``perf_counter`` is this clock, moved by the fake client."""

    def __init__(self) -> None:
        self.now = 1000.0

    def perf_counter(self) -> float:
        return self.now

    @staticmethod
    def time() -> float:
        return time.time()


class _RecordedSleepAsyncio:
    """Stands in for the script's ``asyncio`` module: ``sleep`` records its delay and returns at once."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float, *_args) -> None:
        self.sleeps.append(seconds)

    def __getattr__(self, name: str):
        return getattr(asyncio, name)


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body


class _FakeClient:
    """Records every request in order and answers from a script; each request moves the fake clock 1 s on."""

    def __init__(self, clock: _FakeClock, *, batch_statuses=(), close_script=(), sessions=(SESSION,)) -> None:
        self.clock = clock
        self.calls: list[tuple[str, str, dict]] = []
        self._batch = list(batch_statuses)
        self._close = list(close_script)
        self._sessions = list(sessions)

    async def post(self, path, json=None, headers=None, params=None):
        self.clock.now += 1.0
        self.calls.append(("POST", path, dict(headers or {})))
        if path == SESSIONS_PATH:
            session = self._sessions.pop(0)
            return _FakeResponse(201, {"session_id": session, "session_token": SESSION_HEADER_VALUE, "run_id": session})
        if path == BATCH_PATH:
            return _FakeResponse(self._batch.pop(0) if self._batch else 202)
        return _FakeResponse(404)  # dev-login: refused

    async def delete(self, path, headers=None):
        self.clock.now += 1.0
        self.calls.append(("DELETE", path, dict(headers or {})))
        answer = self._close.pop(0) if self._close else 204
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, _FakeResponse) else _FakeResponse(answer)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def fake_world(monkeypatch, stress):
    """The script's clock and sleeps, faked for this test only (module-local: the real modules are untouched)."""
    clock, recorder = _FakeClock(), _RecordedSleepAsyncio()
    monkeypatch.setattr(stress, "time", clock)
    monkeypatch.setattr(stress, "asyncio", recorder)
    return clock, recorder


def _args(stress, *extra: str):
    return stress.build_parser().parse_args(
        ["--base-url", "http://127.0.0.1:18001", "--project-id", PROJECT, "--tests-per-run", "3", "--batch", "2",
         *extra]
    )


async def _drive(stress, client, args, tel) -> None:
    await stress._drive_run(client, 0, args, dict(AUTH), tel, asyncio.Semaphore(1))


def _requests(client) -> list[tuple[str, str]]:
    return [(method, path) for method, path, _headers in client.calls]


async def test_a_run_posts_its_batches_then_closes_with_the_sdks_delete(stress, fake_world):
    clock, recorder = fake_world
    client, tel = _FakeClient(clock), stress._Telemetry()

    await _drive(stress, client, _args(stress), tel)

    assert _requests(client) == [
        ("POST", SESSIONS_PATH),
        ("POST", BATCH_PATH), ("POST", BATCH_PATH), ("POST", BATCH_PATH),  # run_start + 3 results by 2, run_complete
        ("DELETE", CLOSE_URL),
    ]
    assert stress.CLOSE_PATH.format(session_id=SESSION) == CLOSE_URL
    assert client.calls[-1][2] == AUTH  # the opening credential — never the session token
    assert SESSION_HEADER_VALUE not in json.dumps(client.calls[-1])
    assert tel.completed_at == {SESSION: 1004.0}  # the run_complete batch's 202 (4th request)
    assert tel.closed_at == {SESSION: 1005.0}  # the DELETE's 204 (5th request): the finalize clock
    assert (tel.closed_partial, tel.close_failed, dict(tel.close_errors), recorder.sleeps) == (0, 0, {}, [])


@pytest.mark.parametrize(
    ("first", "slept", "error"),
    [
        (lambda: _FakeResponse(409, headers={"Retry-After": "1"}), [1.0], "409"),
        (lambda: _FakeResponse(429, headers={"Retry-After": "2"}), [2.0], "429"),
        (lambda: _FakeResponse(503), [0.5], "503"),
        (lambda: httpx.ReadTimeout("zz-sentinel-timeout"), [0.5], "ReadTimeout"),
    ],
    ids=["409-admission-busy", "429-retry-after", "503-no-header", "timeout"],
)
async def test_a_refused_or_timed_out_close_is_retried_honouring_retry_after(stress, fake_world, first, slept, error):
    clock, recorder = fake_world
    client, tel = _FakeClient(clock, close_script=[first(), 204]), stress._Telemetry()

    await _drive(stress, client, _args(stress), tel)

    assert [path for method, path in _requests(client) if method == "DELETE"] == [CLOSE_URL, CLOSE_URL]
    assert recorder.sleeps == slept
    assert dict(tel.close_errors) == {error: 1}
    assert tel.closed_at == {SESSION: 1006.0}  # stamped at the SECOND DELETE's 204
    assert tel.close_failed == 0


@pytest.mark.parametrize(
    ("script", "deletes", "errors"),
    [([404], 1, {"404": 1}), ([_FakeResponse(503)] * 6, 6, {"503": 6})],
    ids=["not-retried", "retries-spent"],
)
async def test_a_close_that_fails_for_good_is_counted_and_censored_never_fast(stress, fake_world, script, deletes,
                                                                               errors):
    clock, _recorder = fake_world
    client, tel = _FakeClient(clock, close_script=script), stress._Telemetry()

    await _drive(stress, client, _args(stress), tel)

    assert sum(method == "DELETE" for method, _path in _requests(client)) == deletes
    assert (tel.close_failed, dict(tel.close_errors), tel.closed_at) == (1, errors, {})
    tel.finalized_at[SESSION] = clock.now + 3.0  # even if the reaper finalizes it later
    assert stress.finalize_sample(tel, close_mode=stress.CLOSE_MODE_DELETE, finalize_timeout=900.0) == ([900.0], 1)


async def test_a_run_whose_batch_failed_is_still_closed_and_kept_out_of_the_finalize_sample(stress, fake_world):
    clock, _recorder = fake_world
    client, tel = _FakeClient(clock, batch_statuses=[202, 500]), stress._Telemetry()

    await _drive(stress, client, _args(stress), tel)

    assert _requests(client) == [("POST", SESSIONS_PATH), ("POST", BATCH_PATH), ("POST", BATCH_PATH),
                                 ("DELETE", CLOSE_URL)]
    assert dict(tel.http_errors) == {"500": 1}
    assert tel.closed_partial == 1
    assert tel.completed_at == {} and tel.closed_at == {}
    assert stress.finalize_sample(tel, close_mode=stress.CLOSE_MODE_DELETE, finalize_timeout=900.0) == ([], 0)
    assert stress.close_summary(tel, stress.CLOSE_MODE_DELETE)["closed_partial"] == 1


@pytest.mark.parametrize("batch_statuses", [[], [202, 500]], ids=["complete", "partial"])
async def test_no_close_sends_no_delete_and_keeps_the_w25a_clock(stress, fake_world, batch_statuses):
    clock, _recorder = fake_world
    client, tel = _FakeClient(clock, batch_statuses=batch_statuses), stress._Telemetry()

    await _drive(stress, client, _args(stress, "--no-close"), tel)

    assert {method for method, _path in _requests(client)} == {"POST"}
    assert tel.closed_at == {} and tel.closed_partial == 0 and tel.close_failed == 0
    if not batch_statuses:
        assert tel.completed_at == {SESSION: 1004.0}
        tel.finalized_at[SESSION] = 1014.0
        assert stress.finalize_sample(tel, close_mode=stress.CLOSE_MODE_NONE, finalize_timeout=900.0) == ([10.0], 0)


def test_the_w25e_flags_default_to_ten_protocol_2_and_two_legacy_watchers_closing_by_delete(stress):
    base = ["--base-url", "http://127.0.0.1:18001", "--project-id", PROJECT]
    args = stress.build_parser().parse_args(base)
    assert (args.ws_clients, args.ws_legacy_clients, args.no_close) == (10, 2, False)
    args = stress.build_parser().parse_args([*base, "--no-close", "--ws-clients", "3", "--ws-legacy-clients", "0"])
    assert (args.ws_clients, args.ws_legacy_clients, args.no_close) == (3, 0, True)
    assert stress.WS_PROTOCOL_BATCH == 2 and stress.WS_PROTOCOL_LEGACY == 1
    assert stress.CLOSE_PATH == "/api/v1/stream/sessions/{session_id}"
    assert stress._CLOSE_RETRY_STATUSES == {409, 429, 503}


def test_the_refusal_still_fires_first_with_the_w25e_flags(stress, no_network, capsys):
    argv = ["--base-url", "http://example.com", "--project-id", PROJECT, "--no-close", "--ws-legacy-clients", "3"]
    assert stress.main(argv) == 2
    assert capsys.readouterr().err == REFUSED_HOST


async def test_run_stress_closes_every_run_and_reports_the_close(stress, monkeypatch, fake_world):
    clock, _recorder = fake_world
    client = _FakeClient(clock, batch_statuses=[202, 202, 202, 202, 500], sessions=(SESSION, SESSION_2))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
    monkeypatch.setenv("TESTLOOKUP_STRESS_API_KEY", AUTH["X-API-Key"])
    monkeypatch.delenv("TESTLOOKUP_STRESS_TOKEN", raising=False)

    report = await stress.run_stress(_args(stress, "--runs", "2", "--concurrency", "1"))

    assert _requests(client).count(("DELETE", CLOSE_URL)) == 1
    assert _requests(client).count(("DELETE", f"/api/v1/stream/sessions/{SESSION_2}")) == 1  # the partial run's
    assert report["config"]["close_mode"] == "delete" and report["config"]["ws_legacy_clients"] == 2
    assert report["close"] == {"measured": True, "mode": "delete", "closed": 1, "closed_partial": 1, "failed": 0,
                               "errors": {}, "close_gap_s": {"p50": 1.0, "p95": 1.0, "max": 1.0}}
    assert report["http"]["runs_completed"] == 1 and report["http"]["errors_by_status"] == {"500": 1}
    assert report["frame_content"] == {"measured": False}  # no JWT, so no watcher
    assert report["acceptance"]["pass"] is False

    client = _FakeClient(clock, sessions=(SESSION,))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
    report = await stress.run_stress(_args(stress, "--runs", "1", "--no-close"))
    assert {method for method, _path in _requests(client)} == {"POST"}
    assert (report["close"]["mode"], report["close"]["closed"]) == ("none", 0)


# ── W25e E-0: the finalize clock, the frame content, the acceptance (pure) ──────────────────────────────


def test_finalize_lags_start_at_the_delete_204_not_the_run_complete_202(stress):
    tel = stress._Telemetry()
    tel.completed_at = {"run-p": 100.0, "run-q": 200.0, "run-r": 300.0}
    tel.closed_at = {"run-p": 110.0, "run-q": 210.0, "run-r": 310.0}
    tel.finalized_at = {"run-p": 115.0, "run-q": 212.5}

    assert stress.finalize_sample(tel, close_mode=stress.CLOSE_MODE_DELETE, finalize_timeout=900.0) == (
        [5.0, 2.5, 900.0], 1)
    # --no-close (W25a's reaper reading) starts at the run_complete 202
    assert stress.finalize_sample(tel, close_mode=stress.CLOSE_MODE_NONE, finalize_timeout=900.0) == (
        [15.0, 12.5, 900.0], 1)
    assert stress.close_summary(tel, stress.CLOSE_MODE_DELETE) == {
        "mode": "delete", "closed": 3, "closed_partial": 0, "failed": 0, "errors": {}, "gaps_s": [10.0, 10.0, 10.0]}


def test_the_watchers_count_every_data_frame_once_and_its_content(stress):
    tel = stress._Telemetry()
    listed = [{"test_name": "test_stress_0001", "status": "PASSED", "sequence_id": "1-0"}]
    batch = {"type": "live_batch", "protocol": 2, "sequence_id": "9-0", "events": [
        {"type": "live_run_started", "run_id": "run-p"},
        {"type": "live_test_result", "run_id": "run-p", "coalesced": 39, "results": listed * 30, "results_dropped": 9},
        {"type": "live_test_result", "run_id": "run-q", "coalesced": 2, "results": listed * 2, "results_dropped": 0},
        {"type": "live_warning", "run_id": "run-p", "coalesced": 4},
        "not-an-entry",
    ], "counts": {"events": 46, "entries": 4, "results_listed": 32, "results_dropped": 9}}
    junk_batch = {"type": "live_batch", "events": [{"results": "zz-not-a-list", "results_dropped": True}]}

    assert stress.tally_frame(json.dumps(batch), 2, tel) is True
    assert stress.tally_frame(json.dumps({"type": "live_test_result", "run_id": "run-r"}), 2, tel) is True
    assert stress.tally_frame(json.dumps(junk_batch), 2, tel) is True
    assert stress.tally_frame(json.dumps({"type": "reconcile_required", "sequence_id": "9-1", "dropped": 120}), 2,
                              tel) is False
    assert stress.tally_frame(json.dumps({"type": "connected", "protocol": 2, "frame_interval_ms": 200}), 2,
                              tel) is False
    # a legacy watcher: one frame each, its content is not summed; its reconciles are counted
    assert stress.tally_frame(json.dumps({"type": "live_test_result", "coalesced": 7}), 1, tel) is True
    assert stress.tally_frame(json.dumps(batch), 1, tel) is True
    assert stress.tally_frame(json.dumps({"type": "reconcile_required", "dropped": 40}), 1, tel) is False
    assert stress.tally_frame("zz-not-json", 1, tel) is True  # not a JSON object: a data frame, as before W25e
    assert stress.tally_frame(json.dumps(["zz-a-list"]), 2, tel) is True

    assert stress.frame_content_summary(tel) == {
        "protocol_2_watchers": 0, "results_listed": 33, "results_dropped": 9, "batch_frames": 2,
        "reconciles": 2, "reconciles_by_protocol": {"1": 1, "2": 1}, "reconcile_dropped": 120,
    }


class _FakeSocket:
    def __init__(self, frames: list[str], stop: asyncio.Event) -> None:
        self.frames, self.stop, self.sent = list(frames), stop, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def recv(self) -> str:
        if self.frames:
            return self.frames.pop(0)
        self.stop.set()
        raise asyncio.TimeoutError


async def test_protocol_2_watchers_ask_for_batches_and_legacy_watchers_send_todays_frame(stress, monkeypatch):
    sockets: list[_FakeSocket] = []
    stop = asyncio.Event()
    frames = [
        json.dumps({"type": "connected", "protocol": 2, "frame_interval_ms": 200}),
        json.dumps({"type": "live_batch", "events": [
            {"type": "live_test_result", "results": [{"test_name": "t", "status": "PASSED"}] * 3,
             "results_dropped": 1}]}),
        json.dumps({"type": "live_test_result", "run_id": "run-p"}),
    ]

    def _connect(_url, **_kwargs):
        sockets.append(_FakeSocket(frames, stop))
        return sockets[-1]

    fake = types.SimpleNamespace(connect=_connect, ConnectionClosed=type("ZzClosed", (Exception,), {}),
                                 WebSocketException=type("ZzSocketError", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "websockets", fake)
    tel = stress._Telemetry()
    url, token = "ws://127.0.0.1:18001/ws/live/" + PROJECT, "zz-sentinel-jwt-q"

    await stress._ws_client(url, token, tel, stop, stress.WS_PROTOCOL_BATCH)
    stop.clear()
    await stress._ws_client(url, token, tel, stop, stress.WS_PROTOCOL_LEGACY)

    assert sockets[0].sent == [{"type": "auth", "token": token, "protocol": 2}]
    assert sockets[1].sent == [{"type": "auth", "token": token}]
    assert [len(stamps) for stamps in tel.frames] == [2, 2]  # `connected` is control; each data frame is one
    assert tel.frames_by_protocol[2] == [tel.frames[0]] and tel.frames_by_protocol[1] == [tel.frames[1]]
    assert (tel.results_listed, tel.results_dropped, tel.batch_frames) == (4, 1, 1)  # protocol 2 only
    assert stress.frame_content_summary(tel)["protocol_2_watchers"] == 1


@pytest.mark.parametrize(
    ("finalize_p95", "frames_p95", "expected"),
    [
        (12.0, {1: 4.0, 2: 5.0}, (True, {"1": True, "2": True}, True)),
        (30.0, {1: 10.0, 2: 10.0}, (True, {"1": True, "2": True}, True)),  # the ceilings are inclusive
        (30.1, {1: 4.0, 2: 5.0}, (False, {"1": True, "2": True}, False)),
        (12.0, {1: 11.0, 2: 5.0}, (True, {"1": False, "2": True}, False)),
        (12.0, {1: 4.0, 2: 10.5}, (True, {"1": True, "2": False}, False)),
        (None, {1: 4.0, 2: 5.0}, (None, {"1": True, "2": True}, False)),
        (12.0, {1: None, 2: 5.0}, (True, {"1": None, "2": True}, False)),
        (12.0, {2: 5.0}, (True, {"1": None, "2": True}, False)),  # a protocol with no watcher is not measured
        (None, {}, (None, {"1": None, "2": None}, False)),
    ],
    ids=["all-under", "at-ceilings", "finalize-over", "legacy-over", "batch-over", "finalize-none",
         "legacy-none", "legacy-absent", "nothing"],
)
def test_acceptance_is_a_pass_only_when_every_number_is_measured_and_under(stress, finalize_p95, frames_p95,
                                                                           expected):
    result = stress.acceptance(finalize_p95, frames_p95)
    assert (result["finalize_p95_le_30s"], result["frames_p95_le_10"], result["pass"]) == expected
    assert set(result) == {"finalize_p95_le_30s", "frames_p95_le_10", "pass"}


_PRE_W25E_KEYS = {"generated_at", "host", "config", "finalize_lag_s", "ws_frames_per_s", "redis_stream_depth_max",
                  "db_pool_max", "http", "wall_s", "verdict"}
_W25E_KEYS = {"ws_frames_per_s_by_protocol", "close", "frame_content", "acceptance"}


def _summary_inputs() -> dict:
    return dict(
        config={"runs": 4, "concurrency": 4, "tests_per_run": 10, "batch": 5, "ws_clients": 10,
                "ws_legacy_clients": 2, "close_mode": "delete", "finalize_timeout_s": 900.0, "fail_every": 20,
                "target_port": 18001},
        finalize_lags_s=[2.0, 3.0, 4.0, 4.0], censored=0, frame_windows=[1, 2, 5, 12], ws_closes={"4401": 1},
        stream_max={"live_events": 40}, pool_max={"active": 7}, http_errors={"429": 2},
        transport_errors={"ReadError": 1}, runs_completed=4, wall_s=12.04,
    )


def _w25e_inputs() -> dict:
    return dict(
        frame_windows_by_protocol={1: [3, 12, 12, 0], 2: [1, 2, 5, 4]},
        close_stats={"mode": "delete", "closed": 4, "closed_partial": 1, "failed": 0, "errors": {"409": 2},
                     "gaps_s": [0.012, 0.02, 0.031, 0.5]},
        frame_content={"protocol_2_watchers": 10, "results_listed": 380, "results_dropped": 20, "batch_frames": 60,
                       "reconciles": 3, "reconciles_by_protocol": {"1": 3}, "reconcile_dropped": 0},
    )


def test_summarise_without_the_w25e_arguments_keeps_every_pre_w25e_key(stress):
    old = stress.summarise(**_summary_inputs())

    assert set(old) == _PRE_W25E_KEYS | _W25E_KEYS
    assert {key: old[key] for key in _PRE_W25E_KEYS - {"generated_at"}} == {
        "host": {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
        "config": _summary_inputs()["config"],
        "finalize_lag_s": {"measured": True, "runs": 4, "censored_at_timeout": 0, "p50": 3.0, "p95": 4.0, "max": 4.0},
        "ws_frames_per_s": {"measured": True, "windows": 4, "p95": 12.0, "max": 12, "closes": {"4401": 1}},
        "redis_stream_depth_max": {"measured": True, "live_events": 40},
        "db_pool_max": {"measured": True, "active": 7},
        "http": {"runs_completed": 4, "errors_by_status": {"429": 2}, "transport_errors": {"ReadError": 1}},
        "wall_s": 12.0,
        "verdict": {"decision": "frame_coalescer", "sentence": "build the frame coalescer", "notes": []},
    }
    not_measured = {"measured": False, "windows": 0, "p95": None, "max": None}
    assert old["ws_frames_per_s_by_protocol"] == {"1": not_measured, "2": not_measured}
    assert old["close"] == {"measured": False, "mode": None, "closed": None, "closed_partial": None, "failed": None,
                            "errors": {}, "close_gap_s": {"p50": None, "p95": None, "max": None}}
    assert old["frame_content"] == {"measured": False}
    assert old["acceptance"] == {"finalize_p95_le_30s": True, "frames_p95_le_10": {"1": None, "2": None},
                                 "pass": False}

    new = stress.summarise(**_summary_inputs(), **_w25e_inputs())
    assert {key: new[key] for key in _PRE_W25E_KEYS - {"generated_at"}} == {
        key: old[key] for key in _PRE_W25E_KEYS - {"generated_at"}}


def test_summarise_reports_each_protocol_the_close_and_the_acceptance(stress):
    report = stress.summarise(**_summary_inputs(), **_w25e_inputs())

    assert report["ws_frames_per_s_by_protocol"] == {
        "1": {"measured": True, "windows": 4, "p95": 12.0, "max": 12},
        "2": {"measured": True, "windows": 4, "p95": 5.0, "max": 5},
    }
    assert report["close"] == {"measured": True, "mode": "delete", "closed": 4, "closed_partial": 1, "failed": 0,
                               "errors": {"409": 2}, "close_gap_s": {"p50": 0.02, "p95": 0.5, "max": 0.5}}
    assert report["frame_content"] == {"measured": True, **_w25e_inputs()["frame_content"]}
    assert report["acceptance"] == {"finalize_p95_le_30s": True, "frames_p95_le_10": {"1": False, "2": True},
                                    "pass": False}
    assert report["verdict"]["decision"] == "frame_coalescer"  # over EVERY watcher, unchanged
    json.dumps(report)

    keys = {k.lower() for k in _all_keys(report)}
    assert not {k for k in keys if any(word in k for word in ("token", "key", "dsn", "url", "password", "secret"))}

    no_close = {**_w25e_inputs(), "close_stats": {**_w25e_inputs()["close_stats"], "mode": "none"}}
    reaper = stress.summarise(**_summary_inputs(), **no_close)
    assert reaper["acceptance"]["finalize_p95_le_30s"] is None  # the reaper path is not the acceptance measure
    assert reaper["finalize_lag_s"]["p95"] == 4.0  # still reported, for the side-by-side reading


def test_render_markdown_shows_the_per_protocol_rows_the_close_and_the_acceptance(stress):
    text = stress.render_markdown(stress.summarise(**_summary_inputs(), **_w25e_inputs()))
    lines = text.split("\n")

    assert text.startswith("# W25a stress report — HYG-06 (b) live ingest\n\n**Verdict: build the frame coalescer.**")
    assert ("| WS frames/s per client — protocol 2 watchers (10 clients), 4 one-second windows | | 5.0 | 5 "
            "| p95 > 10 |") in lines
    assert ("| WS frames/s per client — legacy (protocol 1) watchers (2 clients), 4 one-second windows | | 12.0 "
            "| 12 | p95 > 10 |") in lines
    acceptance = lines[lines.index("## Acceptance (W25e)"):][:7]
    assert acceptance == [
        "## Acceptance (W25e)",
        "",
        "- finalize lag p95 ≤ 30 s (DELETE 204 → finalized): PASS (4.0 s)",
        "- WS frames/s per client p95 ≤ 10, protocol 2 watchers: PASS (5.0)",
        "- WS frames/s per client p95 ≤ 10, legacy (protocol 1) watchers: FAIL (12.0)",
        "- **Overall: FAIL**",
        "",
    ]
    assert text.index("## Measurements") < text.index("## Acceptance (W25e)") < text.index("## Redis stream depth")
    assert ("- closed complete runs: 4; closed partial runs (kept out of the finalize sample): 1; closes that failed "
            "for good: 0") in lines
    assert "- close gap (run_complete 202 → DELETE 204), s: p50 0.02, p95 0.5, max 0.5" in lines
    assert "- DELETE 409: 2" in lines
    assert any(line.startswith("- mode: delete — ") for line in lines)
    assert ("- protocol-2 watchers: 10; results listed: 380; results folded but not listed (results_dropped): 20; "
            "live_batch frames: 60") in lines
    assert ("- reconcile_required frames: 3 (legacy 3, protocol 2 0); events dropped into the protocol-2 "
            "reconciles: 0") in lines
    assert "| 4 | 4 | 10 | 5 | 10 + 2 | 900.0 | delete |" in lines


def test_render_markdown_never_reads_a_missing_number_or_the_reaper_path_as_a_pass(stress):
    unmeasured = stress.render_markdown(stress.summarise(**{**_summary_inputs(), "finalize_lags_s": None}))
    assert "- finalize lag p95 ≤ 30 s (DELETE 204 → finalized): not measured" in unmeasured
    assert "- WS frames/s per client p95 ≤ 10, protocol 2 watchers: not measured" in unmeasured
    assert "- **Overall: not a pass — not every threshold was measured**" in unmeasured
    assert "## Close (W25e)\n\nnot measured\n" in unmeasured and "## Frame content (W25e)\n\nnot measured\n" in unmeasured

    inputs = _w25e_inputs()
    inputs["frame_windows_by_protocol"] = {1: [1, 2], 2: [1, 2]}
    inputs["close_stats"] = {**inputs["close_stats"], "mode": "none"}
    reaper = stress.render_markdown(stress.summarise(**_summary_inputs(), **inputs))
    assert ("- finalize lag p95 ≤ 30 s (DELETE 204 → finalized): not measured (--no-close: the clock started at the "
            "run_complete 202 — the reaper path, not the acceptance measure)") in reaper
    assert "- **Overall: not a pass — not every threshold was measured**" in reaper
    assert "- mode: none (--no-close) — " in reaper
