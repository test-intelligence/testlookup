#!/usr/bin/env python3
"""HYG-06 (b) — the live-ingest stress harness (D27 b): evidence for W25d's coalescer decision, never CI.

D27 (b): no finalize / frame coalescer is built before a 500-run stress test on a THROWAWAY stack says one is
needed. This script is that test. It drives N live runs through the SDK's own protocol and measures:

1. **finalize lag** per run — from the session's close, the SDKs' ``DELETE /api/v1/stream/sessions/{id}``
   answering 204 (sent after the ``run_complete`` batch, W25e E-0), to the run's finalize being visible in the
   database (read through ``--dsn``). The exact predicate, from ``ingestion_pipeline.finalize_run``: step 1
   commits the run's aggregates TOGETHER with its ``run_downstream_outbox`` child rows (``status = 'waiting'``);
   the last step, ``_activate_finalize_children``, flips every one of them out of ``waiting``. So a run is
   finalized when it has at least one ``run_downstream_outbox`` row and none of them is still ``waiting``
   (``FINALIZED_SQL`` below). Runs not finalized within ``--finalize-timeout`` count AT the timeout (censored,
   reported) — a run that never finalizes is not a fast one; so does a complete run whose close never answered
   204. W25a's harness never closed: every session was closed by the stale-session reaper, so its 565 s measured
   the reaper, not the product path. ``--no-close`` keeps that reading available for comparison only (no DELETE;
   the clock starts at the ``run_complete`` batch's 202).
2. **WS frames/s per client** — sockets on ``/ws/live/{project_id}``, each counting the data frames it receives
   per 1 s window → p95 / max, over every watcher (the verdict's input) and per protocol: ``--ws-clients``
   watchers ask for protocol 2 (``{"type": "auth", "token": <JWT>, "protocol": 2}`` — batched ``live_batch``
   frames), ``--ws-legacy-clients`` watchers send today's frame (``{"type": "auth", "token": <JWT>}``). Every
   data frame is ONE frame whatever it carries; the protocol-2 watchers also count the results a frame lists and
   the results it folded without listing, and every watcher counts its ``reconcile_required`` frames (the frame
   content: nothing is silent).
3. **Redis stream depth** — ``XLEN`` of the live stream keys (``app/streams/__init__.py``) every second → max.
4. **DB pool waits** — ``pg_stat_activity`` every second (active, idle in transaction, waiting on Lock /
   Client) → max.
5. **HTTP errors** by status (and transport errors by exception type).

Verdict (the backlog's thresholds, ``02-backlog-final.md:3983``): finalize p95 > 30 s → build the finalize
coalescer; WS frames/s per client p95 > 10 → build the frame coalescer; otherwise "not needed — HYG-06
coalescers close NOT_A_GAP with these numbers". A value at or above 80 % of a threshold that does not cross it
is "borderline — re-measure on the homelab-shaped stack before building", never a pass; a measurement that
could not be taken (no ``--dsn``, no WebSocket client) makes the verdict "incomplete", never "not needed".

Acceptance (W25e, ``acceptance()``): finalize p95 ≤ 30 s measured from the DELETE's 204, and WS frames/s per
client p95 ≤ 10 for the protocol-2 AND the legacy watchers; a number that was not measured is never a pass. It is
reported beside the verdict, which is unchanged.

Protocol: ``POST /api/v1/stream/sessions`` (``X-API-Key`` from ``TESTLOOKUP_STRESS_API_KEY``, else the bearer
JWT) → ``session_id``, ``session_token``, ``run_id``; then ``POST /api/v1/stream/events/batch`` with
``X-Session-Token``: ``run_start`` + ``test_result`` × N in batches of at most ``--batch`` (≤ 1000), then
``run_complete``. Each batch carries a ``batch_id`` reused on a retry (429 / 503 / timeout), so a retried batch
is admitted once. Then the SDKs' close, ``DELETE /api/v1/stream/sessions/{session_id}`` with the credential that
opened the session (a ``run_complete`` batch alone never closes a session), retried on 409 / 429 / 503 honouring
``Retry-After`` and on a transport error (the close is idempotent: an already-closed session answers 204). A run
whose batches failed is still closed, as the SDK's context manager does, counted ``closed_partial`` and kept out
of the finalize sample (it is not a complete run). The JWT comes from ``TESTLOOKUP_STRESS_TOKEN`` or
``POST /api/v1/auth/dev-login?role=ADMIN`` (the role is a QUERY parameter). The run must finish inside the token's lifetime (a WebSocket closed with 4401
is recorded, not reopened).

SAFETY — this runs ONLY against a throwaway LOCAL stack:
* a ``--base-url`` / ``--dsn`` / ``--redis-url`` host that is not loopback (``localhost``, ``127.0.0.0/8``,
  ``::1``) is refused with exit 2 BEFORE any request (the homelab API OOMs on bulk work);
* port 8000 (the shared ``make dev`` stack) is refused unless ``--allow-dev-stack``;
* finalize stages notifications, webhooks and (with AI on) the agent pipeline for every run: the stack MUST run
  with ``AI_OFFLINE_MODE=true`` and NO SMTP / webhook / integration configured, so 500 finalized runs send no
  mail and call no model. The script cannot check that; the operator does.
* no session token, API key, JWT, DSN or URL is printed, logged or written to the report; the DELETE carries the
  same auth header as the session's creation and its URL holds only the session UUID.

It imports no ``app.*`` module (it runs outside the backend venv too); ``httpx``, ``websockets`` (shipped with
``uvicorn[standard]``), ``asyncpg`` and ``redis`` are imported lazily, and a missing one exits with a sentence.

Usage (the orchestrator, ONCE, on the throwaway stack)::

    python backend/scripts/stress_live_ingest.py --base-url http://127.0.0.1:18001 --project-id <uuid> \\
        --runs 500 --concurrency 500 --tests-per-run 200 --batch 200 --ws-clients 10 --ws-legacy-clients 2 \\
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

#: How the harness ends a session (W25e E-0): the SDKs' DELETE (the finalize clock starts at its 204), or
#: ``--no-close`` — no DELETE, the stale-session reaper closes it (W25a's reading, for comparison only).
CLOSE_MODE_DELETE = "delete"
CLOSE_MODE_NONE = "none"

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


def _within(value: Optional[float], threshold: float) -> Optional[bool]:
    return None if value is None else value <= threshold


def acceptance(finalize_p95_s: Optional[float], frames_p95_by_protocol: dict[int, Optional[float]]) -> dict[str, Any]:
    """W25e's acceptance (D27's thresholds as ceilings): finalize p95 ≤ 30 s from the DELETE's 204, and WS frames/s
    per client p95 ≤ 10 for the protocol-2 AND the legacy watchers. ``None`` = not measured, which is never a pass;
    a protocol missing from the mapping was not measured either. :func:`verdict` is a separate, unchanged reading."""
    frames = {str(protocol): _within(frames_p95_by_protocol.get(protocol), FRAMES_PER_SECOND_THRESHOLD)
              for protocol in (WS_PROTOCOL_LEGACY, WS_PROTOCOL_BATCH)}
    finalize_ok = _within(finalize_p95_s, FINALIZE_P95_THRESHOLD_S)
    return {
        "finalize_p95_le_30s": finalize_ok,
        "frames_p95_le_10": frames,
        "pass": finalize_ok is True and all(ok is True for ok in frames.values()),
    }


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
    frame_windows_by_protocol: Optional[dict[int, Optional[list[int]]]] = None,
    close_stats: Optional[dict[str, Any]] = None,
    frame_content: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Every measurement with ``measured: false`` where it could not be taken, plus the verdict and (W25e) the
    acceptance. The W25e keyword arguments default to None (= not measured), so every key a pre-W25e call
    produced keeps its value; ``frame_windows`` stays the verdict's input over EVERY watcher.

    ``frame_windows_by_protocol``: ``{1: windows | None, 2: windows | None}`` (the legacy and the protocol-2
    watchers). ``close_stats``: ``{"mode", "closed", "closed_partial", "failed", "errors", "gaps_s"}`` with
    ``gaps_s`` the per-run ``run_complete`` 202 → DELETE 204 seconds (:func:`close_summary`). ``frame_content``:
    the watchers' counts (:func:`frame_content_summary`)."""
    finalize_p95 = percentile(finalize_lags_s, 95) if finalize_lags_s else None
    frames_p95 = percentile([float(w) for w in frame_windows], 95) if frame_windows else None
    decided = verdict(finalize_p95, frames_p95)
    by_protocol: dict[str, dict[str, Any]] = {}
    frames_p95_by_protocol: dict[int, Optional[float]] = {}
    for protocol in (WS_PROTOCOL_LEGACY, WS_PROTOCOL_BATCH):
        windows = (frame_windows_by_protocol or {}).get(protocol)
        p95 = percentile([float(w) for w in windows], 95) if windows else None
        frames_p95_by_protocol[protocol] = p95
        by_protocol[str(protocol)] = {
            "measured": p95 is not None,
            "windows": len(windows or []),
            "p95": _rounded(p95),
            "max": max(windows) if windows else None,
        }
    given = close_stats or {}
    gaps = given.get("gaps_s") or []
    close = {
        "measured": close_stats is not None,
        "mode": given.get("mode"),
        "closed": given.get("closed"),
        "closed_partial": given.get("closed_partial"),
        "failed": given.get("failed"),
        "errors": dict(sorted((given.get("errors") or {}).items())),
        "close_gap_s": {
            "p50": _rounded(percentile(gaps, 50), 2),
            "p95": _rounded(percentile(gaps, 95), 2),
            "max": _rounded(max(gaps) if gaps else None, 2),
        },
    }
    # Under --no-close the clock starts at the run_complete 202 (the reaper path): not the acceptance measure.
    accepted_finalize = None if close["mode"] == CLOSE_MODE_NONE else finalize_p95
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
        "ws_frames_per_s_by_protocol": by_protocol,
        "close": close,
        "frame_content": {"measured": frame_content is not None, **(frame_content or {})},
        "acceptance": acceptance(accepted_finalize, frames_p95_by_protocol),
    }


def _cell(value: Any) -> str:
    return "not measured" if value is None else str(value)


def _outcome(ok: Optional[bool], value: Any, unit: str = "") -> str:
    if ok is None:
        return "not measured"
    return f"{'PASS' if ok else 'FAIL'} ({value}{unit})"


def _acceptance_lines(report: dict[str, Any]) -> list[str]:
    """``## Acceptance (W25e)``: one PASS / FAIL / not measured line per threshold, then the overall reading."""
    accepted = report.get("acceptance")
    if accepted is None:
        return []
    close_mode = (report.get("close") or {}).get("mode")
    if close_mode == CLOSE_MODE_NONE:
        finalize = ("not measured (--no-close: the clock started at the run_complete 202 — the reaper path, "
                    "not the acceptance measure)")
    else:
        finalize = _outcome(accepted["finalize_p95_le_30s"], report["finalize_lag_s"]["p95"], " s")
    lines = [
        "## Acceptance (W25e)",
        "",
        f"- finalize lag p95 ≤ {FINALIZE_P95_THRESHOLD_S:g} s (DELETE 204 → finalized): {finalize}",
    ]
    by_protocol = report.get("ws_frames_per_s_by_protocol") or {}
    for protocol, label, _clients_key in _PROTOCOL_ROWS:
        p95 = (by_protocol.get(str(protocol)) or {}).get("p95")
        ok = accepted["frames_p95_le_10"].get(str(protocol))
        lines.append(
            f"- WS frames/s per client p95 ≤ {FRAMES_PER_SECOND_THRESHOLD:g}, {label} watchers: {_outcome(ok, p95)}"
        )
    outcomes = [accepted["finalize_p95_le_30s"], *accepted["frames_p95_le_10"].values()]
    if accepted["pass"]:
        overall = "PASS"
    elif any(ok is False for ok in outcomes):
        overall = "FAIL"
    else:
        overall = "not a pass — not every threshold was measured"
    return lines + [f"- **Overall: {overall}**", ""]


def _close_lines(close: Optional[dict[str, Any]]) -> list[str]:
    if close is None:
        return []
    lines = ["## Close (W25e)", ""]
    if not close.get("measured"):
        return lines + ["not measured", ""]
    if close["mode"] == CLOSE_MODE_NONE:
        lines.append("- mode: none (--no-close) — no DELETE: the stale-session reaper closes the sessions and the "
                     "finalize clock starts at the run_complete 202 (W25a's reading, for comparison only)")
    else:
        lines.append("- mode: delete — every session is closed with the SDKs' DELETE /api/v1/stream/sessions/{id}; "
                     "the finalize clock starts at its 204")
    gap = close["close_gap_s"]
    lines += [
        f"- closed complete runs: {close['closed']}; closed partial runs (kept out of the finalize sample): "
        f"{close['closed_partial']}; closes that failed for good: {close['failed']}",
        f"- close gap (run_complete 202 → DELETE 204), s: p50 {_cell(gap['p50'])}, p95 {_cell(gap['p95'])}, "
        f"max {_cell(gap['max'])}",
    ]
    lines += [f"- DELETE {name}: {n}" for name, n in close["errors"].items()]
    return lines + [""]


def _frame_content_lines(content: Optional[dict[str, Any]]) -> list[str]:
    if content is None:
        return []
    lines = ["## Frame content (W25e)", ""]
    if not content.get("measured"):
        return lines + ["not measured", ""]
    by_protocol = content.get("reconciles_by_protocol") or {}
    lines += [
        f"- protocol-2 watchers: {content.get('protocol_2_watchers')}; results listed: "
        f"{content.get('results_listed')}; results folded but not listed (results_dropped): "
        f"{content.get('results_dropped')}; live_batch frames: {content.get('batch_frames')}",
        f"- reconcile_required frames: {content.get('reconciles')} (legacy {by_protocol.get('1', 0)}, protocol 2 "
        f"{by_protocol.get('2', 0)}); events dropped into the protocol-2 reconciles: {content.get('reconcile_dropped')}",
        "",
    ]
    return lines


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
        "| runs | concurrency | tests per run | batch | WS clients (protocol 2 + legacy) | finalize timeout (s) "
        "| close |",
        "|---|---|---|---|---|---|---|",
        f"| {cfg['runs']} | {cfg['concurrency']} | {cfg['tests_per_run']} | {cfg['batch']} | {cfg['ws_clients']} + "
        f"{cfg.get('ws_legacy_clients', 0)} | {cfg['finalize_timeout_s']} | {cfg.get('close_mode') or '—'} |",
        "",
        "## Measurements",
        "",
        "| measure | p50 | p95 | max | threshold |",
        "|---|---|---|---|---|",
        f"| finalize lag (s), {fin['runs']} runs, {fin['censored_at_timeout']} censored at the timeout "
        f"| {_cell(fin['p50'])} | {_cell(fin['p95'])} | {_cell(fin['max'])} | p95 > {FINALIZE_P95_THRESHOLD_S:g} s |",
        f"| WS frames/s per client, {ws['windows']} one-second windows | | {_cell(ws['p95'])} | {_cell(ws['max'])} "
        f"| p95 > {FRAMES_PER_SECOND_THRESHOLD:g} |",
    ]
    by_protocol = report.get("ws_frames_per_s_by_protocol") or {}
    for protocol, label, clients_key in _PROTOCOL_ROWS:
        row = by_protocol.get(str(protocol))
        if row is None:
            continue
        lines.append(
            f"| WS frames/s per client — {label} watchers ({_cell(cfg.get(clients_key))} clients), {row['windows']} "
            f"one-second windows | | {_cell(row['p95'])} | {_cell(row['max'])} | p95 > {FRAMES_PER_SECOND_THRESHOLD:g} |"
        )
    lines.append("")
    lines += _acceptance_lines(report)
    lines += _close_lines(report.get("close"))
    lines += _frame_content_lines(report.get("frame_content"))
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


# ── The run (network: never run in CI, never run against anything but a throwaway stack; its bookkeeping is ──
# ── unit-tested with a fake client and a fake socket only) ───────────────────────────────────────────────────

_RETRY_STATUSES = {429, 503}  # admission refusals: the request did nothing, so it is safe to send again
_CONTROL_FRAMES = {"connected", "initial_state", "ping", "pong", "refreshed", "reconcile_required"}
WS_PROTOCOL_BATCH = 2  # sent in the auth frame by the --ws-clients watchers (W25e; equals live_frames.LIVE_PROTOCOL_BATCH)
WS_PROTOCOL_LEGACY = 1  # the --ws-legacy-clients watchers send no protocol field (equals live_frames.LIVE_PROTOCOL_LEGACY)
_APP_NAME = "stress_live_ingest"  # our own DB connections, left out of the pool sample

#: The SDKs' close (``client/testlookup_reporter.py`` ``_close_session``): DELETE, 204, idempotent.
CLOSE_PATH = "/api/v1/stream/sessions/{session_id}"
#: 409 = "live batch admission is still in progress; retry close" (``Retry-After: 1``); 429 / 503 = admission.
_CLOSE_RETRY_STATUSES = {409, 429, 503}
_CLOSE_ATTEMPTS = 6  # as many as a batch gets
_BATCH_FRAME = "live_batch"  # equals live_frames.LIVE_BATCH_FRAME
_RESULT_FRAME = "live_test_result"
_RECONCILE_FRAME = "reconcile_required"  # a control frame (never a data frame), counted into the frame content

#: (protocol, Markdown label, the config key holding its watcher count) — the protocol-2 row first.
_PROTOCOL_ROWS = (
    (WS_PROTOCOL_BATCH, "protocol 2", "ws_clients"),
    (WS_PROTOCOL_LEGACY, "legacy (protocol 1)", "ws_legacy_clients"),
)


def _new_frames_by_protocol() -> dict[int, list]:
    return {WS_PROTOCOL_LEGACY: [], WS_PROTOCOL_BATCH: []}


@dataclass
class _Telemetry:
    http_errors: Counter = field(default_factory=Counter)
    transport_errors: Counter = field(default_factory=Counter)
    started_runs: set = field(default_factory=set)
    completed_at: dict = field(default_factory=dict)  # run_id -> perf_counter at the run_complete 202
    closed_at: dict = field(default_factory=dict)  # run_id -> perf_counter at the DELETE 204 (complete runs only)
    closed_partial: int = 0  # runs whose batches failed, closed anyway (never in the finalize sample)
    close_failed: int = 0  # DELETEs that failed for good (retries spent, or a status that is not retried)
    close_errors: Counter = field(default_factory=Counter)  # DELETE status / transport exception type -> count
    finalized_at: dict = field(default_factory=dict)  # run_id -> perf_counter when FINALIZED_SQL first saw it
    finalize_measured: bool = False
    frames: list = field(default_factory=list)  # one list of perf_counter stamps per WebSocket client
    frames_by_protocol: dict = field(default_factory=_new_frames_by_protocol)  # protocol -> the same stamp lists
    results_listed: int = 0  # protocol-2 watchers: results their frames listed
    results_dropped: int = 0  # protocol-2 watchers: results folded into a frame without being listed
    batch_frames: int = 0  # protocol-2 watchers: live_batch frames
    reconciles: int = 0  # every watcher: reconcile_required frames
    reconciles_by_protocol: Counter = field(default_factory=Counter)
    reconcile_dropped: int = 0  # protocol-2 watchers: the "dropped" events their reconcile frames announced
    ws_closes: Counter = field(default_factory=Counter)
    stream_max: dict = field(default_factory=dict)
    pool_max: dict = field(default_factory=dict)


def _count(value: Any) -> int:
    """A non-negative int from a frame field, else 0 (a bool is not a count)."""
    return value if type(value) is int and value >= 0 else 0


def tally_frame(raw: Any, protocol: int, tel: _Telemetry) -> bool:
    """Fold one received WebSocket frame into ``tel``'s frame content; True when it is a DATA frame (stamped).

    Every data frame is ONE frame whatever it carries (the frames/s measure). ``reconcile_required`` stays a
    control frame but is counted (every watcher). A protocol-2 watcher also sums ``len(entry["results"])`` and
    ``entry["results_dropped"]`` over a ``live_batch`` frame's entries, and counts a raw ``live_test_result`` frame
    as one listed result. A frame that is not a JSON object is a data frame, as before W25e."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        message = None
    kind = message.get("type") if isinstance(message, dict) else None
    if kind == _RECONCILE_FRAME:
        tel.reconciles += 1
        tel.reconciles_by_protocol[str(protocol)] += 1
        if protocol == WS_PROTOCOL_BATCH:
            tel.reconcile_dropped += _count(message.get("dropped"))
    if kind in _CONTROL_FRAMES:
        return False
    if protocol == WS_PROTOCOL_BATCH:
        if kind == _BATCH_FRAME:
            tel.batch_frames += 1
            entries = message.get("events")
            for entry in entries if isinstance(entries, list) else ():
                if not isinstance(entry, dict):
                    continue
                results = entry.get("results")
                tel.results_listed += len(results) if isinstance(results, list) else 0
                tel.results_dropped += _count(entry.get("results_dropped"))
        elif kind == _RESULT_FRAME:
            tel.results_listed += 1
    return True


def _clock_starts(tel: _Telemetry, close_mode: str) -> dict:
    """Where each run's finalize clock starts: the DELETE's 204 (the SDK path), or — under ``--no-close`` — the
    ``run_complete`` batch's 202 (W25a's reaper-closed reading)."""
    return tel.completed_at if close_mode == CLOSE_MODE_NONE else tel.closed_at


def finalize_sample(tel: _Telemetry, *, close_mode: str, finalize_timeout: float) -> tuple[list[float], int]:
    """The finalize lags (s) and how many of them are censored AT ``finalize_timeout``.

    lag = ``FINALIZED_SQL`` first seen − the clock start (:func:`_clock_starts`). A run never seen finalized counts
    at the timeout (a run that never finalizes is not a fast one). In delete mode a COMPLETE run whose DELETE never
    answered 204 has no clock start: it counts at the timeout too (a failed close is not a fast finalize). A
    partial run is never in the sample (it is not a complete run)."""
    starts = _clock_starts(tel, close_mode)
    lags: list[float] = []
    censored = 0
    for run_id, start in starts.items():
        seen = tel.finalized_at.get(run_id)
        if seen is None:
            censored += 1
            lags.append(float(finalize_timeout))
        else:
            lags.append(max(0.0, seen - start))
    if close_mode != CLOSE_MODE_NONE:
        unclosed = [run_id for run_id in tel.completed_at if run_id not in tel.closed_at]
        censored += len(unclosed)
        lags += [float(finalize_timeout)] * len(unclosed)
    return lags, censored


def close_summary(tel: _Telemetry, close_mode: str) -> dict[str, Any]:
    """:func:`summarise`'s ``close_stats``: the mode, the counts, and each closed run's ``run_complete`` 202 →
    DELETE 204 gap (informational: it is the close's own latency, not part of the finalize lag)."""
    gaps = [max(0.0, closed - tel.completed_at[run_id])
            for run_id, closed in tel.closed_at.items() if run_id in tel.completed_at]
    return {
        "mode": close_mode,
        "closed": len(tel.closed_at),
        "closed_partial": tel.closed_partial,
        "failed": tel.close_failed,
        "errors": dict(tel.close_errors),
        "gaps_s": gaps,
    }


def frame_content_summary(tel: _Telemetry) -> dict[str, Any]:
    """:func:`summarise`'s ``frame_content``: totals over the watchers (divide by ``protocol_2_watchers`` for one
    watcher's share — each protocol-2 watcher sees the whole project's events)."""
    return {
        "protocol_2_watchers": len(tel.frames_by_protocol.get(WS_PROTOCOL_BATCH, [])),
        "results_listed": tel.results_listed,
        "results_dropped": tel.results_dropped,
        "batch_frames": tel.batch_frames,
        "reconciles": tel.reconciles,
        "reconciles_by_protocol": dict(sorted(tel.reconciles_by_protocol.items())),
        "reconcile_dropped": tel.reconcile_dropped,
    }


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


async def _close_session(client: Any, session_id: str, headers: dict, tel: _Telemetry) -> Optional[float]:
    """The SDKs' close: ``DELETE CLOSE_PATH`` with the credential that opened the session. Retried on
    ``_CLOSE_RETRY_STATUSES`` honouring ``Retry-After`` and on a transport error (the close is idempotent: an
    already-closed session answers 204, so a timed-out close is sent again — a timeout is not a failure). Returns
    ``perf_counter`` taken at the 2xx, or None once the close has failed for good. Statuses and exception TYPES are
    counted in ``close_errors`` — never a body, a header or the URL."""
    import httpx

    path = CLOSE_PATH.format(session_id=session_id)
    delay = 0.5
    for _attempt in range(_CLOSE_ATTEMPTS):
        try:
            resp = await client.delete(path, headers=headers)
        except httpx.HTTPError as exc:
            tel.close_errors[type(exc).__name__] += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)
            continue
        if 200 <= resp.status_code < 300:
            return time.perf_counter()
        tel.close_errors[str(resp.status_code)] += 1
        if resp.status_code not in _CLOSE_RETRY_STATUSES:
            break
        await asyncio.sleep(_retry_delay(resp.headers.get("Retry-After"), delay))
        delay = min(delay * 2, 10.0)
    tel.close_failed += 1
    return None


async def _drive_run(client: Any, index: int, args: argparse.Namespace, auth: dict, tel: _Telemetry,
                     gate: asyncio.Semaphore) -> None:
    """One live run through the SDK protocol: open the session, post the batches (the ``run_complete`` batch's
    202 stamps ``completed_at``), then close it with the SDKs' DELETE (its 204 stamps ``closed_at`` — where the
    finalize clock starts). A run whose batches failed is still closed (the SDK closes in a ``finally``), counted
    ``closed_partial`` and never given a ``closed_at``. ``--no-close`` sends no DELETE (W25a's reaper reading)."""
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
        session_id = str(uuid.UUID(str(created["session_id"])))  # the DELETE's URL holds only the session UUID
        session_header = {"X-Session-Token": created["session_token"]}
        tel.started_runs.add(run_id)
        complete = False
        try:
            for events in build_batches(args.tests_per_run, args.batch, args.fail_every, int(time.time() * 1000)):
                payload = {"session_id": created["session_id"], "run_id": run_id, "batch_id": str(uuid.uuid4()),
                           "events": events}
                if await _post(client, "/api/v1/stream/events/batch", payload, session_header, tel,
                               idempotent=True) is None:
                    return
            tel.completed_at[run_id] = time.perf_counter()
            complete = True
        finally:
            if not args.no_close:
                closed = await _close_session(client, session_id, auth, tel)
                if closed is not None and complete:
                    tel.closed_at[run_id] = closed
                elif closed is not None:
                    tel.closed_partial += 1


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def _asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _poll_finalize(dsn: str, tel: _Telemetry, loading_done: asyncio.Event, args: argparse.Namespace) -> None:
    """Every ``--poll-interval`` s: which runs whose finalize clock has started (closed by the DELETE's 204; under
    ``--no-close`` completed by the ``run_complete`` 202) does ``FINALIZED_SQL`` now see as finalized. Ends once
    the load is over and every such run is finalized or past ``--finalize-timeout`` (censored)."""
    import asyncpg

    starts = _clock_starts(tel, CLOSE_MODE_NONE if args.no_close else CLOSE_MODE_DELETE)
    conn = await asyncpg.connect(_asyncpg_dsn(dsn), server_settings={"application_name": _APP_NAME})
    try:
        while True:
            now = time.perf_counter()
            pending = [rid for rid in starts if rid not in tel.finalized_at]
            if pending:
                rows = await conn.fetch(FINALIZED_SQL, [uuid.UUID(rid) for rid in pending])
                for row in rows:
                    tel.finalized_at.setdefault(row[0], now)
            tel.finalize_measured = True
            if loading_done.is_set():
                open_runs = [rid for rid in starts if rid not in tel.finalized_at
                             and now - starts[rid] < args.finalize_timeout]
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


async def _ws_client(ws_url: str, token: str, tel: _Telemetry, stop: asyncio.Event,
                     protocol: int = WS_PROTOCOL_LEGACY) -> None:
    """One dashboard watcher: authenticates (a protocol-2 watcher adds ``"protocol": 2`` to the auth frame, a
    legacy one sends today's frame), then stamps every data frame and tallies its content until told to stop."""
    import websockets

    stamps: list[float] = []
    tel.frames.append(stamps)
    tel.frames_by_protocol.setdefault(protocol, []).append(stamps)
    auth: dict[str, Any] = {"type": "auth", "token": token}
    if protocol == WS_PROTOCOL_BATCH:
        auth["protocol"] = WS_PROTOCOL_BATCH
    try:
        async with websockets.connect(ws_url, max_size=None, open_timeout=15) as ws:
            await ws.send(json.dumps(auth))
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                received = time.perf_counter()
                if tally_frame(raw, protocol, tel):
                    stamps.append(received)
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
        watchers = {WS_PROTOCOL_BATCH: max(0, args.ws_clients), WS_PROTOCOL_LEGACY: max(0, args.ws_legacy_clients)}
        watching = bool(sum(watchers.values()) and token)
        if watching:
            url = _ws_url(args.base_url, args.project_id)
            for protocol, count in watchers.items():
                background += [asyncio.create_task(_ws_client(url, str(token), tel, stop, protocol))
                               for _ in range(count)]
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

    close_mode = CLOSE_MODE_NONE if args.no_close else CLOSE_MODE_DELETE
    lags: Optional[list[float]] = None
    censored = 0
    if args.dsn and tel.finalize_measured and not finalize_failed and tel.completed_at:
        lags, censored = finalize_sample(tel, close_mode=close_mode, finalize_timeout=args.finalize_timeout)
    windows: Optional[list[int]] = None
    windows_by_protocol: Optional[dict[int, Optional[list[int]]]] = None
    if watching:
        windows = [w for stamps in tel.frames for w in frames_per_second_windows(stamps, load_start, load_end)]
        windows_by_protocol = {
            protocol: [w for stamps in clients for w in frames_per_second_windows(stamps, load_start, load_end)]
            if clients else None
            for protocol, clients in tel.frames_by_protocol.items()
        }
    config = {
        "runs": args.runs, "concurrency": args.concurrency, "tests_per_run": args.tests_per_run, "batch": args.batch,
        "ws_clients": args.ws_clients, "ws_legacy_clients": args.ws_legacy_clients, "close_mode": close_mode,
        "finalize_timeout_s": args.finalize_timeout, "fail_every": args.fail_every,
        "target_port": urllib.parse.urlsplit(args.base_url).port,
    }
    return summarise(
        config=config, finalize_lags_s=lags, censored=censored, frame_windows=windows, ws_closes=dict(tel.ws_closes),
        stream_max=tel.stream_max if args.redis_url and tel.stream_max else None,
        pool_max=tel.pool_max if args.dsn and tel.pool_max else None,
        http_errors=dict(tel.http_errors), transport_errors=dict(tel.transport_errors),
        runs_completed=len(tel.completed_at), wall_s=time.perf_counter() - load_start,
        frame_windows_by_protocol=windows_by_protocol, close_stats=close_summary(tel, close_mode),
        frame_content=frame_content_summary(tel) if watching else None,
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
    p.add_argument("--ws-clients", type=int, default=10,
                   help='protocol-2 WebSocket watchers on /ws/live/{project_id} ("protocol": 2 in the auth frame)')
    p.add_argument("--ws-legacy-clients", type=int, default=2,
                   help="legacy WebSocket watchers (today's auth frame, no protocol field)")
    p.add_argument("--no-close", action="store_true",
                   help="send no DELETE: the stale-session reaper closes the sessions and the finalize clock starts "
                        "at the run_complete 202 (W25a's reading, for comparison only — not the acceptance measure)")
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
