#!/usr/bin/env python3
"""HYG-06 (b) — the live-ingest stress harness (D27 b): evidence for W25d's coalescer decision, never CI.

D27 (b): no finalize / frame coalescer is built before a 500-run stress test on a THROWAWAY stack says one is
needed. This script is that test. It drives N live runs through the SDK's own protocol and measures:

1. **finalize lag** per run — from the ``run_complete`` batch's 202 to the run's finalize being visible in the
   database (read through ``--dsn``). The exact predicate, from ``ingestion_pipeline.finalize_run``: step 1
   commits the run's aggregates TOGETHER with its ``run_downstream_outbox`` child rows (``status = 'waiting'``);
   the last step, ``_activate_finalize_children``, flips every one of them out of ``waiting``. So a run is
   finalized when it has at least one ``run_downstream_outbox`` row and none of them is still ``waiting``
   (``FINALIZED_SQL`` below). Runs not finalized within ``--finalize-timeout`` count AT the timeout (censored,
   reported) — a run that never finalizes is not a fast one.
2. **WS frames/s per client** — ``--ws-clients`` sockets on ``/ws/live/{project_id}`` (the auth handshake
   ``{"type": "auth", "token": <JWT>}``), each counting the frames it receives per 1 s window → p95 / max.
3. **Redis stream depth** — ``XLEN`` of the live stream keys (``app/streams/__init__.py``) every second → max.
4. **DB pool waits** — ``pg_stat_activity`` every second (active, idle in transaction, waiting on Lock /
   Client) → max.
5. **HTTP errors** by status (and transport errors by exception type).

Verdict (the backlog's thresholds, ``02-backlog-final.md:3983``): finalize p95 > 30 s → build the finalize
coalescer; WS frames/s per client p95 > 10 → build the frame coalescer; otherwise "not needed — HYG-06
coalescers close NOT_A_GAP with these numbers". A value at or above 80 % of a threshold that does not cross it
is "borderline — re-measure on the homelab-shaped stack before building", never a pass; a measurement that
could not be taken (no ``--dsn``, no WebSocket client) makes the verdict "incomplete", never "not needed".

Protocol: ``POST /api/v1/stream/sessions`` (``X-API-Key`` from ``TESTLOOKUP_STRESS_API_KEY``, else the bearer
JWT) → ``session_id``, ``session_token``, ``run_id``; then ``POST /api/v1/stream/events/batch`` with
``X-Session-Token``: ``run_start`` + ``test_result`` × N in batches of at most ``--batch`` (≤ 1000), then
``run_complete``. Each batch carries a ``batch_id`` reused on a retry (429 / 503 / timeout), so a retried batch
is admitted once. The JWT comes from ``TESTLOOKUP_STRESS_TOKEN`` or ``POST /api/v1/auth/dev-login?role=ADMIN``
(the role is a QUERY parameter). The run must finish inside the token's lifetime (a WebSocket closed with 4401
is recorded, not reopened).

SAFETY — this runs ONLY against a throwaway LOCAL stack:
* a ``--base-url`` / ``--dsn`` / ``--redis-url`` host that is not loopback (``localhost``, ``127.0.0.0/8``,
  ``::1``) is refused with exit 2 BEFORE any request (the homelab API OOMs on bulk work);
* port 8000 (the shared ``make dev`` stack) is refused unless ``--allow-dev-stack``;
* finalize stages notifications, webhooks and (with AI on) the agent pipeline for every run: the stack MUST run
  with ``AI_OFFLINE_MODE=true`` and NO SMTP / webhook / integration configured, so 500 finalized runs send no
  mail and call no model. The script cannot check that; the operator does.
* no session token, API key, JWT, DSN or URL is printed, logged or written to the report.

It imports no ``app.*`` module (it runs outside the backend venv too); ``httpx``, ``websockets`` (shipped with
``uvicorn[standard]``), ``asyncpg`` and ``redis`` are imported lazily, and a missing one exits with a sentence.

Usage (the orchestrator, ONCE, on the throwaway stack)::

    python backend/scripts/stress_live_ingest.py --base-url http://127.0.0.1:18001 --project-id <uuid> \\
        --runs 500 --concurrency 500 --tests-per-run 200 --batch 200 --ws-clients 10 \\
        --dsn postgresql://<user>:<password>@127.0.0.1:<port>/<db> --redis-url redis://127.0.0.1:<port>/0 \\
        --output stress.json --markdown docs/deferred-scope-2026-10-13/work/W25a-stress-report.md
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import math
import os
import platform
import sys
import time
import urllib.parse
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

#: The shared dev stack (``make dev``) serves on localhost:8000; it is never load-tested.
LOCAL_DEV_STACK_PORT = 8000
REFUSAL_EXIT_CODE = 2
REFUSAL_SENTENCE = "the stress test runs only against a throwaway local stack"

FINALIZE_P95_THRESHOLD_S = 30.0  # 02-backlog-final.md:3983
FRAMES_PER_SECOND_THRESHOLD = 10.0  # per client, p95 over 1 s windows
BORDERLINE_FRACTION = 0.8  # within 20 % below a threshold = re-measure, not a pass

#: Finalize visible (see the module docstring): >= 1 outbox row and none still 'waiting'.
FINALIZED_SQL = """
SELECT o.run_id::text
  FROM run_downstream_outbox o
 WHERE o.run_id = ANY($1::uuid[])
 GROUP BY o.run_id
HAVING count(*) FILTER (WHERE o.status = 'waiting') = 0
"""

POOL_SQL = """
SELECT count(*) FILTER (WHERE state = 'active')              AS active,
       count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction,
       count(*) FILTER (WHERE wait_event_type = 'Lock')      AS waiting_lock,
       count(*) FILTER (WHERE wait_event_type = 'Client')    AS waiting_client,
       count(*)                                              AS backends
  FROM pg_stat_activity
 WHERE datname = current_database() AND application_name <> 'stress_live_ingest'
"""

#: Fixed live stream keys (app/streams/__init__.py); the per-project fan-out key is added at run time.
STREAM_KEYS = (
    "testlookup:stream:live_events",
    "testlookup:stream:live_fanout",
    "testlookup:stream:ingestion_jobs",
)
PROJECT_FANOUT_KEY = "testlookup:stream:live_fanout:{project_id}"
EVIDENCE_KEY = "testlookup:live:evidence:{run_id}"


# ── Target safety (checked before ANY request) ──────────────────────────────────────────────────────────


class UnsafeTarget(ValueError):
    """A target this harness must never touch."""


def _is_loopback(host: str) -> bool:
    if host in ("localhost", "localhost."):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_target(url: str, *, allow_dev_stack: bool = False, what: str = "--base-url") -> None:
    """Refuse a non-HTTP URL, a host that is not loopback, and the shared dev stack's port 8000.

    The message never repeats the URL (it may carry credentials); it names the flag and the rule.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeTarget(f"{what}: an http(s) URL is required — {REFUSAL_SENTENCE}")
    host = (parts.hostname or "").lower()
    if not host or not _is_loopback(host):
        raise UnsafeTarget(f"{what}: the host is not loopback — {REFUSAL_SENTENCE}")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UnsafeTarget(f"{what}: the port is not a number — {REFUSAL_SENTENCE}") from None
    if port == LOCAL_DEV_STACK_PORT and not allow_dev_stack:
        raise UnsafeTarget(
            f"{what}: port {LOCAL_DEV_STACK_PORT} is the shared dev stack — {REFUSAL_SENTENCE} "
            "(pass --allow-dev-stack only if this really is a disposable server)"
        )


def check_store_url(url: str, *, schemes: tuple[str, ...], what: str) -> None:
    """``--dsn`` / ``--redis-url``: the right scheme and a loopback host (the stores are read, never written)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in schemes:
        raise UnsafeTarget(f"{what}: expected a {' / '.join(schemes)} URL — {REFUSAL_SENTENCE}")
    host = (parts.hostname or "").lower()
    if not host or not _is_loopback(host):
        raise UnsafeTarget(f"{what}: the host is not loopback — {REFUSAL_SENTENCE}")


# ── Pure measurement helpers (unit-tested) ──────────────────────────────────────────────────────────────


def percentile(values: list[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; ``None`` for no values (absence is not a zero)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def frames_per_second_windows(timestamps: list[float], start: float, end: float) -> list[int]:
    """Frames received in each whole 1 s window of ``[start, end)`` — empty windows included, as zeros."""
    if end <= start:
        return []
    windows = [0] * max(1, math.ceil(end - start))
    for ts in timestamps:
        if start <= ts < end:
            windows[min(int(ts - start), len(windows) - 1)] += 1
    return windows


@dataclass(frozen=True)
class Verdict:
    decision: str  # finalize_coalescer | frame_coalescer | both_coalescers | borderline | not_needed | incomplete
    sentence: str
    notes: tuple[str, ...] = ()


_NOT_NEEDED = "not needed — HYG-06 coalescers close NOT_A_GAP with these numbers"
_BORDERLINE = "borderline — re-measure on the homelab-shaped stack before building"


def verdict(finalize_p95_s: Optional[float], frames_p95: Optional[float]) -> Verdict:
    """The backlog's thresholds; a near miss is a re-measure and a missing number is never a pass."""
    notes: list[str] = []
    build: list[str] = []
    measured = {"finalize p95": (finalize_p95_s, FINALIZE_P95_THRESHOLD_S, "s"),
                "WS frames/s per client p95": (frames_p95, FRAMES_PER_SECOND_THRESHOLD, "")}
    borderline = False
    for label, (value, threshold, unit) in measured.items():
        if value is None:
            notes.append(f"{label} was not measured")
        elif value > threshold:
            build.append(label)
        elif value >= BORDERLINE_FRACTION * threshold:
            borderline = True
            notes.append(f"{label} {value:.1f}{unit} is within 20 % of the {threshold:g}{unit} threshold")
    finalize_builds = "finalize p95" in build
    frames_build = "WS frames/s per client p95" in build
    if finalize_builds and frames_build:
        return Verdict("both_coalescers", "build the finalize coalescer and the frame coalescer", tuple(notes))
    if finalize_builds:
        return Verdict("finalize_coalescer", "build the finalize coalescer", tuple(notes))
    if frames_build:
        return Verdict("frame_coalescer", "build the frame coalescer", tuple(notes))
    if finalize_p95_s is None or frames_p95 is None:
        return Verdict("incomplete", "incomplete — re-run with every measurement before deciding", tuple(notes))
    if borderline:
        return Verdict("borderline", _BORDERLINE, tuple(notes))
    return Verdict("not_needed", _NOT_NEEDED, tuple(notes))


def build_batches(tests_per_run: int, batch: int, fail_every: int, now_ms: int) -> list[list[dict[str, Any]]]:
    """The events of one run: ``run_start`` + ``test_result`` × N in batches of at most ``batch``, then a batch
    holding only ``run_complete`` (its 202 starts the finalize clock). Test names repeat across runs, as a real
    suite's do; every ``fail_every``-th test fails (0 = none) so failure frames reach the WebSocket clients."""
    batch = max(1, min(batch, 1000))
    events: list[dict[str, Any]] = [{"event_type": "run_start", "timestamp_ms": now_ms}]
    for n in range(1, tests_per_run + 1):
        failed = bool(fail_every) and n % fail_every == 0
        event: dict[str, Any] = {
            "event_type": "test_result",
            "test_name": f"test_stress_{n:04d}",
            "status": "FAILED" if failed else "PASSED",
            "duration_ms": 10 + n % 50,
            "suite_name": "stress_suite",
            "class_name": "StressCase",
            "timestamp_ms": now_ms + n,
        }
        if failed:
            event["error_message"] = "AssertionError: stress harness failure"
        events.append(event)
    batches = [events[i:i + batch] for i in range(0, len(events), batch)]
    batches.append([{"event_type": "run_complete", "timestamp_ms": now_ms + tests_per_run + 1}])
    return batches


# ── The report (pure; unit-tested) ──────────────────────────────────────────────────────────────────────


def _rounded(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def summarise(
    *,
    config: dict[str, Any],
    finalize_lags_s: Optional[list[float]],
    censored: int,
    frame_windows: Optional[list[int]],
    ws_closes: dict[str, int],
    stream_max: Optional[dict[str, int]],
    pool_max: Optional[dict[str, int]],
    http_errors: dict[str, int],
    transport_errors: dict[str, int],
    runs_completed: int,
    wall_s: float,
) -> dict[str, Any]:
    """Every measurement with ``measured: false`` where it could not be taken, plus the verdict."""
    finalize_p95 = percentile(finalize_lags_s, 95) if finalize_lags_s else None
    frames_p95 = percentile([float(w) for w in frame_windows], 95) if frame_windows else None
    decided = verdict(finalize_p95, frames_p95)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
        "config": config,
        "finalize_lag_s": {
            "measured": finalize_p95 is not None,
            "runs": len(finalize_lags_s or []),
            "censored_at_timeout": censored,
            "p50": _rounded(percentile(finalize_lags_s or [], 50)),
            "p95": _rounded(finalize_p95),
            "max": _rounded(max(finalize_lags_s) if finalize_lags_s else None),
        },
        "ws_frames_per_s": {
            "measured": frames_p95 is not None,
            "windows": len(frame_windows or []),
            "p95": _rounded(frames_p95),
            "max": max(frame_windows) if frame_windows else None,
            "closes": dict(sorted(ws_closes.items())),
        },
        "redis_stream_depth_max": {"measured": stream_max is not None, **(stream_max or {})},
        "db_pool_max": {"measured": pool_max is not None, **(pool_max or {})},
        "http": {
            "runs_completed": runs_completed,
            "errors_by_status": dict(sorted(http_errors.items())),
            "transport_errors": dict(sorted(transport_errors.items())),
        },
        "wall_s": round(wall_s, 1),
        "verdict": {"decision": decided.decision, "sentence": decided.sentence, "notes": list(decided.notes)},
    }


def _cell(value: Any) -> str:
    return "not measured" if value is None else str(value)


def render_markdown(report: dict[str, Any]) -> str:
    """The Markdown report W25d reads: config, the five measurements against the thresholds, the verdict."""
    cfg, fin, ws = report["config"], report["finalize_lag_s"], report["ws_frames_per_s"]
    lines = [
        "# W25a stress report — HYG-06 (b) live ingest",
        "",
        f"**Verdict: {report['verdict']['sentence']}.**",
        "",
    ]
    lines += [f"- {note}" for note in report["verdict"]["notes"]]
    lines += [
        "",
        "## Load",
        "",
        "| runs | concurrency | tests per run | batch | WS clients | finalize timeout (s) |",
        "|---|---|---|---|---|---|",
        f"| {cfg['runs']} | {cfg['concurrency']} | {cfg['tests_per_run']} | {cfg['batch']} | {cfg['ws_clients']} "
        f"| {cfg['finalize_timeout_s']} |",
        "",
        "## Measurements",
        "",
        "| measure | p50 | p95 | max | threshold |",
        "|---|---|---|---|---|",
        f"| finalize lag (s), {fin['runs']} runs, {fin['censored_at_timeout']} censored at the timeout "
        f"| {_cell(fin['p50'])} | {_cell(fin['p95'])} | {_cell(fin['max'])} | p95 > {FINALIZE_P95_THRESHOLD_S:g} s |",
        f"| WS frames/s per client, {ws['windows']} one-second windows | | {_cell(ws['p95'])} | {_cell(ws['max'])} "
        f"| p95 > {FRAMES_PER_SECOND_THRESHOLD:g} |",
        "",
    ]
    for title, key in (("Redis stream depth (max XLEN)", "redis_stream_depth_max"), ("DB backends (max)", "db_pool_max")):
        block = report[key]
        lines += [f"## {title}", ""]
        if not block.get("measured"):
            lines += ["not measured", ""]
            continue
        lines += [f"- {name}: {value}" for name, value in block.items() if name != "measured"] + [""]
    http = report["http"]
    lines += ["## HTTP", "", f"- runs completed: {http['runs_completed']} of {cfg['runs']}"]
    lines += [f"- HTTP {status}: {n}" for status, n in http["errors_by_status"].items()]
    lines += [f"- {name}: {n}" for name, n in http["transport_errors"].items()]
    if ws["closes"]:
        lines += [f"- WebSocket closed {code}: {n}" for code, n in ws["closes"].items()]
    host = report["host"]
    lines += [
        "",
        "## Host",
        "",
        f"{host['platform']}, Python {host['python']}, {host['cpus']} CPUs; wall time {report['wall_s']} s; "
        f"generated {report['generated_at']}.",
        "",
    ]
    return "\n".join(lines)


# ── The run (network: never unit-tested, never run in CI, never run against anything but a throwaway stack) ──

_RETRY_STATUSES = {429, 503}  # admission refusals: the request did nothing, so it is safe to send again
_CONTROL_FRAMES = {"connected", "initial_state", "ping", "pong", "refreshed", "reconcile_required"}
_APP_NAME = "stress_live_ingest"  # our own DB connections, left out of the pool sample


@dataclass
class _Telemetry:
    http_errors: Counter = field(default_factory=Counter)
    transport_errors: Counter = field(default_factory=Counter)
    started_runs: set = field(default_factory=set)
    completed_at: dict = field(default_factory=dict)  # run_id -> perf_counter at the run_complete 202
    finalized_at: dict = field(default_factory=dict)  # run_id -> perf_counter when FINALIZED_SQL first saw it
    finalize_measured: bool = False
    frames: list = field(default_factory=list)  # one list of perf_counter stamps per WebSocket client
    ws_closes: Counter = field(default_factory=Counter)
    stream_max: dict = field(default_factory=dict)
    pool_max: dict = field(default_factory=dict)


def _retry_delay(header: Optional[str], fallback: float) -> float:
    try:
        return min(max(float(header), 0.1), 30.0) if header else fallback
    except ValueError:
        return fallback


async def _post(client: Any, path: str, payload: dict, headers: dict, tel: _Telemetry, *, idempotent: bool) -> Any:
    """POST with the SDK's retry rule: 429 / 503 always retried; a transport error (incl. a timeout — a timeout is
    not a failure) retried only when the request is idempotent (a batch reuses its ``batch_id``). Returns the
    response, or None once the request has failed for good (the status is counted, never the body)."""
    import httpx

    delay = 0.5
    for _attempt in range(6):
        try:
            resp = await client.post(path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            tel.transport_errors[type(exc).__name__] += 1
            if not idempotent:
                return None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)
            continue
        if resp.status_code in _RETRY_STATUSES:
            tel.http_errors[str(resp.status_code)] += 1
            await asyncio.sleep(_retry_delay(resp.headers.get("Retry-After"), delay))
            delay = min(delay * 2, 10.0)
            continue
        if resp.status_code >= 400:
            tel.http_errors[str(resp.status_code)] += 1
            return None
        return resp
    return None


async def _drive_run(client: Any, index: int, args: argparse.Namespace, auth: dict, tel: _Telemetry,
                     gate: asyncio.Semaphore) -> None:
    """One live run through the SDK protocol; stamps the run_complete 202."""
    async with gate:
        body = {
            "project_id": args.project_id,
            "client_name": "stress-live-ingest",
            "build_number": f"stress-{args.tag}-{index:04d}",
            "framework": "pytest",
            "total_tests": args.tests_per_run,
            "suite_name": "stress_suite",
        }
        resp = await _post(client, "/api/v1/stream/sessions", body, auth, tel, idempotent=False)
        if resp is None:
            return
        created = resp.json()
        run_id = str(created["run_id"])
        session_header = {"X-Session-Token": created["session_token"]}
        tel.started_runs.add(run_id)
        for events in build_batches(args.tests_per_run, args.batch, args.fail_every, int(time.time() * 1000)):
            payload = {"session_id": created["session_id"], "run_id": run_id, "batch_id": str(uuid.uuid4()),
                       "events": events}
            if await _post(client, "/api/v1/stream/events/batch", payload, session_header, tel, idempotent=True) is None:
                return
        tel.completed_at[run_id] = time.perf_counter()


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def _asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _poll_finalize(dsn: str, tel: _Telemetry, loading_done: asyncio.Event, args: argparse.Namespace) -> None:
    """Every ``--poll-interval`` s: which completed runs does ``FINALIZED_SQL`` now see as finalized. Ends once the
    load is over and every completed run is finalized or past ``--finalize-timeout`` (censored)."""
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(dsn), server_settings={"application_name": _APP_NAME})
    try:
        while True:
            now = time.perf_counter()
            pending = [rid for rid in tel.completed_at if rid not in tel.finalized_at]
            if pending:
                rows = await conn.fetch(FINALIZED_SQL, [uuid.UUID(rid) for rid in pending])
                for row in rows:
                    tel.finalized_at.setdefault(row[0], now)
            tel.finalize_measured = True
            if loading_done.is_set():
                open_runs = [rid for rid in tel.completed_at if rid not in tel.finalized_at
                             and now - tel.completed_at[rid] < args.finalize_timeout]
                if not open_runs:
                    return
            await asyncio.sleep(args.poll_interval)
    finally:
        await conn.close()


async def _sample_pool(dsn: str, tel: _Telemetry, stop: asyncio.Event) -> None:
    """``pg_stat_activity`` every second → the max of each column (our own connections left out)."""
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(dsn), server_settings={"application_name": _APP_NAME})
    try:
        while not stop.is_set():
            row = await conn.fetchrow(POOL_SQL)
            for name in row.keys():
                tel.pool_max[name] = max(tel.pool_max.get(name, 0), int(row[name]))
            await _sleep_or_stop(stop, 1.0)
    finally:
        await conn.close()


async def _sample_streams(redis_url: str, project_id: str, tel: _Telemetry, stop: asyncio.Event) -> None:
    """``XLEN`` of the live stream keys every second → the max per key; the per-run evidence streams summed."""
    import redis.asyncio as aioredis

    keys = {key.rsplit(":", 1)[-1]: key for key in STREAM_KEYS}
    keys["live_fanout:<project>"] = PROJECT_FANOUT_KEY.format(project_id=project_id)
    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        while not stop.is_set():
            runs = sorted(tel.started_runs)
            pipe = client.pipeline(transaction=False)
            for key in keys.values():
                pipe.xlen(key)
            for run_id in runs:
                pipe.xlen(EVIDENCE_KEY.format(run_id=run_id))
            depths = await pipe.execute()
            sample = dict(zip(keys, depths[: len(keys)]))
            sample["live_evidence (sum over runs)"] = sum(depths[len(keys):])
            for name, depth in sample.items():
                tel.stream_max[name] = max(tel.stream_max.get(name, 0), int(depth))
            await _sleep_or_stop(stop, 1.0)
    finally:
        await client.aclose()


async def _ws_client(ws_url: str, token: str, tel: _Telemetry, stop: asyncio.Event) -> None:
    """One dashboard watcher: authenticates, then stamps every non-control frame until told to stop."""
    import websockets

    stamps: list[float] = []
    tel.frames.append(stamps)
    try:
        async with websockets.connect(ws_url, max_size=None, open_timeout=15) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    kind = json.loads(raw).get("type")
                except (ValueError, AttributeError):
                    kind = None
                if kind not in _CONTROL_FRAMES:
                    stamps.append(time.perf_counter())
    except websockets.ConnectionClosed as exc:
        code = getattr(getattr(exc, "rcvd", None), "code", None)
        tel.ws_closes[str(code)] += 1
    except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as exc:
        tel.ws_closes[type(exc).__name__] += 1


async def _dev_login(client: Any) -> Optional[str]:
    """``POST /api/v1/auth/dev-login?role=ADMIN`` (role is a QUERY parameter); None when the stack refuses it."""
    import httpx

    try:
        resp = await client.post("/api/v1/auth/dev-login", params={"role": "ADMIN"})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    access = resp.json().get("access_token")
    return access if isinstance(access, str) and access else None


def _ws_url(base_url: str, project_id: str) -> str:
    parts = urllib.parse.urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit((scheme, parts.netloc, f"/ws/live/{project_id}", "", ""))


async def run_stress(args: argparse.Namespace) -> dict[str, Any]:
    """Drive the load, measure, and return :func:`summarise`'s report. Called only after every target check."""
    try:
        import httpx
    except ImportError:
        raise SystemExit("httpx is not installed — run this with the backend's Python") from None

    tel = _Telemetry()
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(60.0), limits=limits) as client:
        token = os.environ.get("TESTLOOKUP_STRESS_TOKEN") or await _dev_login(client)
        api_key = os.environ.get("TESTLOOKUP_STRESS_API_KEY")
        if not api_key and not token:
            raise SystemExit("no credential: set TESTLOOKUP_STRESS_API_KEY or TESTLOOKUP_STRESS_TOKEN (dev-login refused)")
        auth = {"X-API-Key": api_key} if api_key else {"Authorization": "Bearer " + str(token)}

        stop, loading_done = asyncio.Event(), asyncio.Event()
        background: list[asyncio.Task] = []
        watching = bool(args.ws_clients and token)
        if watching:
            url = _ws_url(args.base_url, args.project_id)
            background += [asyncio.create_task(_ws_client(url, str(token), tel, stop)) for _ in range(args.ws_clients)]
            await asyncio.sleep(2.0)  # let the watchers authenticate before the first run starts
        finalize_task = None
        if args.dsn:
            finalize_task = asyncio.create_task(_poll_finalize(args.dsn, tel, loading_done, args))
            background.append(asyncio.create_task(_sample_pool(args.dsn, tel, stop)))
        if args.redis_url:
            background.append(asyncio.create_task(_sample_streams(args.redis_url, args.project_id, tel, stop)))

        load_start = time.perf_counter()
        gate = asyncio.Semaphore(args.concurrency)
        await asyncio.gather(*(_drive_run(client, i, args, auth, tel, gate) for i in range(args.runs)))
        load_end = time.perf_counter()
        loading_done.set()
        finalize_failed = False
        if finalize_task is not None:
            try:
                await finalize_task
            except Exception as exc:  # noqa: BLE001 — a broken poller means "not measured", never "fast"
                finalize_failed = True
                tel.transport_errors[f"finalize_poll:{type(exc).__name__}"] += 1
        stop.set()
        for outcome in await asyncio.gather(*background, return_exceptions=True):
            if isinstance(outcome, BaseException):
                tel.transport_errors[f"sampler:{type(outcome).__name__}"] += 1

    lags: Optional[list[float]] = None
    censored = 0
    if args.dsn and tel.finalize_measured and not finalize_failed and tel.completed_at:
        lags = []
        for run_id, done in tel.completed_at.items():
            seen = tel.finalized_at.get(run_id)
            if seen is None:
                censored += 1
                lags.append(float(args.finalize_timeout))
            else:
                lags.append(max(0.0, seen - done))
    windows: Optional[list[int]] = None
    if watching:
        windows = [w for stamps in tel.frames for w in frames_per_second_windows(stamps, load_start, load_end)]
    config = {
        "runs": args.runs, "concurrency": args.concurrency, "tests_per_run": args.tests_per_run, "batch": args.batch,
        "ws_clients": args.ws_clients, "finalize_timeout_s": args.finalize_timeout, "fail_every": args.fail_every,
        "target_port": urllib.parse.urlsplit(args.base_url).port,
    }
    return summarise(
        config=config, finalize_lags_s=lags, censored=censored, frame_windows=windows, ws_closes=dict(tel.ws_closes),
        stream_max=tel.stream_max if args.redis_url and tel.stream_max else None,
        pool_max=tel.pool_max if args.dsn and tel.pool_max else None,
        http_errors=dict(tel.http_errors), transport_errors=dict(tel.transport_errors),
        runs_completed=len(tel.completed_at), wall_s=time.perf_counter() - load_start,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────────


def _project_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError("--project-id must be a project UUID") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stress_live_ingest.py",
        description="HYG-06 (b) live-ingest stress harness — a throwaway LOCAL stack only (see the module docstring).",
    )
    p.add_argument("--base-url", required=True, help="the API of the throwaway local stack (loopback only)")
    p.add_argument("--allow-dev-stack", action="store_true",
                   help=f"allow port {LOCAL_DEV_STACK_PORT} (refused by default: the shared dev stack)")
    p.add_argument("--project-id", required=True, type=_project_uuid, help="the project the runs are ingested into")
    p.add_argument("--runs", type=int, default=500)
    p.add_argument("--concurrency", type=int, default=500, help="runs in flight together")
    p.add_argument("--tests-per-run", type=int, default=200)
    p.add_argument("--batch", type=int, default=200, help="events per batch (at most 1000)")
    p.add_argument("--fail-every", type=int, default=20, help="every Nth test fails (0 = none)")
    p.add_argument("--ws-clients", type=int, default=10, help="WebSocket watchers on /ws/live/{project_id}")
    p.add_argument("--dsn", default=None, help="the throwaway database (read-only queries; loopback only)")
    p.add_argument("--redis-url", default=None, help="the throwaway Redis (XLEN only; loopback only)")
    p.add_argument("--finalize-timeout", type=float, default=900.0, help="seconds before a run counts as censored")
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                   help="build-number tag of this run's sessions")
    p.add_argument("--output", default=None, help="write the report as JSON")
    p.add_argument("--markdown", default=None, help="write the report as Markdown")
    return p


def check_args(args: argparse.Namespace) -> None:
    """Every safety check, before any request; raises :class:`UnsafeTarget`."""
    check_target(args.base_url, allow_dev_stack=args.allow_dev_stack)
    if args.dsn:
        check_store_url(args.dsn, schemes=("postgresql", "postgres", "postgresql+asyncpg"), what="--dsn")
    if args.redis_url:
        check_store_url(args.redis_url, schemes=("redis",), what="--redis-url")


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        check_args(args)
    except UnsafeTarget as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return REFUSAL_EXIT_CODE
    if not (1 <= args.batch <= 1000) or args.runs < 1 or args.concurrency < 1 or args.tests_per_run < 0:
        print("refused: --runs / --concurrency >= 1, --tests-per-run >= 0, 1 <= --batch <= 1000", file=sys.stderr)
        return REFUSAL_EXIT_CODE
    report = asyncio.run(run_stress(args))
    markdown = render_markdown(report)
    print(markdown)
    if args.output:
        _write(args.output, json.dumps(report, indent=2) + "\n")
    if args.markdown:
        _write(args.markdown, markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
