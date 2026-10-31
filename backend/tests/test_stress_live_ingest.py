"""HYG-06 (b) — the live-ingest stress harness's unit-tested parts (``backend/scripts/stress_live_ingest.py``).

No network, ever: the host / port refusals (exit 2 BEFORE any request), the percentile and frame-window maths, the
verdict against the backlog's thresholds (finalize p95 > 30 s, > 10 frames/s per client; within 20 % below =
re-measure; a missing number is never a pass), the batches, and the report rendering. The script imports no
``app.*`` module and imports its network clients lazily. Times are built INSIDE each test.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import time
from pathlib import Path

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
