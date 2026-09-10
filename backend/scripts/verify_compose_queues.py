#!/usr/bin/env python3
"""Prove that a running Compose worker fleet consumes every expected queue."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


BASE_QUEUES = ("default", "critical", "ingestion", "ai_analysis", "agent_children")
PROBE_TASK = "app.worker.celery_app.queue_delivery_probe"
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^\s/@]+@", re.IGNORECASE)


def queues_for(*, explicit: Iterable[str], shard_count: int | None) -> list[str]:
    queues = [value.strip() for value in explicit if value.strip()]
    if queues and shard_count is not None:
        raise ValueError("use either --queue or --shard-count, not both")
    if not queues:
        if shard_count is None:
            raise ValueError("provide at least one --queue or --shard-count")
        if shard_count < 0:
            raise ValueError("--shard-count must be non-negative")
        queues = [*BASE_QUEUES, *(f"ingestion.shard.{index}" for index in range(shard_count))]
    if len(set(queues)) != len(queues):
        raise ValueError("queue names must be unique")
    if any(not queue or any(ch.isspace() for ch in queue) for queue in queues):
        raise ValueError("queue names must be non-empty and contain no whitespace")
    return queues


def _sanitize(message: object, secrets: Iterable[str]) -> str:
    text = str(message)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted-url>")
    return _URL_CREDENTIALS.sub(r"\g<scheme><redacted>@", text)[:1000]


def _worker_subscriptions(app: Any, timeout: float) -> dict[str, list[str]]:
    """Return queue -> worker names; inspection is supporting evidence only."""
    try:
        replies = app.control.inspect(timeout=min(timeout, 5.0)).active_queues() or {}
    except Exception:  # broker delivery below remains the authoritative proof
        return {}
    subscriptions: dict[str, list[str]] = {}
    for worker, rows in replies.items():
        for row in rows or []:
            name = row.get("name") if isinstance(row, dict) else None
            if name:
                subscriptions.setdefault(str(name), []).append(str(worker))
    return {queue: sorted(set(workers)) for queue, workers in subscriptions.items()}


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def probe_queues(
    app: Any,
    *,
    topology: str,
    queues: list[str],
    timeout: float,
    evidence_path: Path,
    source_sha: str,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    started = clock()
    deadline = started + timeout
    subscriptions = _worker_subscriptions(app, timeout)
    pending: list[tuple[str, str, Any, float]] = []
    records: list[dict[str, Any]] = []
    secrets = (str(app.conf.broker_url or ""), str(app.conf.result_backend or ""))

    for queue in queues:
        task_id = uuid.uuid4().hex
        sent_at = clock()
        try:
            result = app.send_task(
                PROBE_TASK, args=[task_id], queue=queue, task_id=task_id
            )
            pending.append((queue, task_id, result, sent_at))
        except Exception as exc:
            records.append({
                "queue": queue,
                "task_id": task_id,
                "status": "send_error",
                "workers": subscriptions.get(queue, []),
                "error": _sanitize(exc, secrets),
            })

    for queue, task_id, result, sent_at in pending:
        remaining = deadline - clock()
        if remaining <= 0:
            records.append({
                "queue": queue, "task_id": task_id, "status": "timeout",
                "workers": subscriptions.get(queue, []), "error": "overall probe deadline exceeded",
            })
            continue
        try:
            value = result.get(timeout=remaining, propagate=True)
            valid = (
                isinstance(value, dict)
                and value.get("correlation_id") == task_id
                and value.get("routing_key") == queue
                and bool(value.get("worker"))
            )
            status = "passed" if valid else "unexpected_result"
            record = {
                "queue": queue,
                "task_id": task_id,
                "status": status,
                "workers": sorted(set([
                    *subscriptions.get(queue, []),
                    *([str(value.get("worker"))] if isinstance(value, dict) and value.get("worker") else []),
                ])),
                "latency_seconds": round(clock() - sent_at, 3),
            }
            if status != "passed":
                record["error"] = _sanitize(
                    f"expected matching probe attribution, received {value!r}", secrets
                )
            records.append(record)
        except Exception as exc:
            records.append({
                "queue": queue,
                "task_id": task_id,
                "status": "delivery_error",
                "workers": subscriptions.get(queue, []),
                "latency_seconds": round(clock() - sent_at, 3),
                "error": _sanitize(exc, secrets),
            })

    by_queue = {record["queue"]: record for record in records}
    complete = set(by_queue) == set(queues) and len(records) == len(queues)
    passed = complete and all(by_queue[queue]["status"] == "passed" for queue in queues)
    evidence = {
        "schema_version": 1,
        "topology": topology,
        "source_sha": source_sha,
        "expected_queues": queues,
        "probe_task": PROBE_TASK,
        "timeout_seconds": timeout,
        "elapsed_seconds": round(clock() - started, 3),
        "result": "passed" if passed else "failed",
        "queues": records,
    }
    _atomic_json(evidence_path, evidence)
    return 0 if passed else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", required=True)
    parser.add_argument("--queue", action="append", default=[])
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--broker-url", required=True)
    parser.add_argument("--result-url", required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        expected = queues_for(explicit=args.queue, shard_count=args.shard_count)
        from celery import Celery

        app = Celery("testlookup-queue-probe", broker=args.broker_url, backend=args.result_url)
        return probe_queues(
            app,
            topology=args.topology,
            queues=expected,
            timeout=args.timeout,
            evidence_path=args.evidence,
            source_sha=args.source_sha,
        )
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "topology": args.topology,
            "source_sha": args.source_sha,
            "result": "failed",
            "error": _sanitize(exc, (args.broker_url, args.result_url)),
        }
        try:
            _atomic_json(args.evidence, failure)
        except Exception as write_exc:
            print(f"queue probe evidence write failed: {_sanitize(write_exc, ())}", file=sys.stderr)
        print(f"queue probe failed: {failure['error']}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
