"""AGT-14 (W23d): the Mongo store of captured HTTP exchanges (``rest_api_payloads``).

The ONLY writer of the collection. Mongo only: no SQL session, no commit, no
outbound call (Mongo is local), so it is offline-safe either way.

* ``write_exchanges`` re-validates every exchange through ``HttpExchangeIn`` (the
  redaction is idempotent, so a queued object written by an older router is
  redacted here too), applies the per-test and per-run caps, and is idempotent on
  a Celery retry or a CI re-delivery: the docs of the cases in THIS write are
  replaced, never duplicated. It never raises: a Mongo failure is counted on
  ``http_exchanges_ingested_total{outcome="write_failed"}`` and logged once with
  the error TYPE only, and the caller's ingest continues.
* ``list_for_case`` / ``docs_for_case`` / ``drift_baseline`` READ; a read failure
  raises ``ExchangesUnavailable`` (the GET answers 503 — never an empty list that
  reads as "nothing captured").

A log line carries ids, counts and outcomes only — never a URL, a header, a body
or a token.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from pydantic import ValidationError

from app.core import metrics as _metrics
from app.db.mongo import Collections, get_mongo_db
from app.models.schemas import HttpExchangeIn, HttpMessageIn
from app.services.http_exchange_redaction import url_host
from app.services.http_exchange_vocab import (
    CAPTURE_SOURCES,
    DRIFT_BASELINE_DOCS_MAX,
    DRIFT_BASELINE_RUNS,
    EXCHANGE_DOC_VERSION,
    EXCHANGES_PER_RUN_MAX,
    EXCHANGES_PER_TEST_MAX,
    LIST_PAGE_MAX,
    TRUNCATION_MARKER,
)

_slog = structlog.get_logger(__name__)

_HOST_MAX = 255
# "…[truncated: {omitted} bytes not stored]" at the END of a stored body.
_MARKER_RE = re.compile(
    re.escape(TRUNCATION_MARKER).replace(re.escape("{omitted}"), r"(\d+)") + r"\Z"
)
_DRIFT_PROJECTION = {
    "_id": 0, "test_run_id": 1, "build_number": 1, "run_created_at": 1,
    "response_schema": 1, "response_schema_state": 1,
}


@dataclass(frozen=True)
class WriteCounts:
    stored: int
    truncated: int
    dropped: int
    write_failed: bool


class ExchangesUnavailable(Exception):
    """A READ of the exchange store failed (Mongo down / timed out)."""


def _collection():
    return get_mongo_db()[Collections.REST_API_PAYLOADS]


def _count(outcome: str, source: str, n: int) -> None:
    if n > 0:
        _metrics.http_exchanges_ingested_total.labels(source=source, outcome=outcome).inc(n)


def _message_doc(msg: HttpMessageIn) -> dict[str, Any]:
    """One side of an exchange as stored; ``body_bytes`` is the size before the store cut."""
    body = msg.body
    truncated = bool(msg.body_truncated)
    size = 0
    if body is not None:
        size = len(body.encode("utf-8"))
        marker = _MARKER_RE.search(body)
        if marker:
            truncated = True
            size = len(body[: marker.start()].encode("utf-8")) + int(marker.group(1))
    # The model records the size before the store cut itself (the SDK's own cut included).
    reported = getattr(msg, "body_bytes", None)
    if isinstance(reported, int) and not isinstance(reported, bool) and reported > size:
        size = reported
    return {"headers": dict(msg.headers), "body": body, "body_truncated": truncated, "body_bytes": size}


def _host(url: str) -> Optional[str]:
    # url_host, not urlsplit: urlsplit raises on a redacted userinfo ("user:[REDACTED]@host"), which lost the
    # host of every url whose password the redaction masked.
    host = url_host(url)
    return host[:_HOST_MAX] if host else None


def _exchange_doc(ex: HttpExchangeIn, *, seq: int, source: str, base: Mapping[str, Any],
                  now: datetime) -> dict[str, Any]:
    status = ex.status
    return {
        **base,
        "seq": seq,
        "source": source,
        "method": ex.method,
        "endpoint": ex.endpoint or "/",
        "host": _host(ex.url),
        "url": ex.url,
        "status": status,
        "status_class": status // 100 if status is not None else None,
        "duration_ms": ex.duration_ms,
        "started_at": ex.started_at,
        "error": ex.error,
        "request": _message_doc(ex.request),
        "response": _message_doc(ex.response),
        "response_schema": dict(ex.response_schema) if ex.response_schema is not None else None,
        "response_schema_state": ex.response_schema_state,
        "redaction_counts": {
            key: int(ex.redaction_counts.get(key, 0)) for key in ("headers", "query", "body")
        },
        "captured_at": now,
    }


def _revalidate(item: Any) -> Optional[HttpExchangeIn]:
    """Every exchange passes the model again (idempotent redaction + caps)."""
    try:
        if isinstance(item, HttpExchangeIn):
            return HttpExchangeIn.model_validate(item.model_dump())
        if isinstance(item, Mapping):
            return HttpExchangeIn.model_validate(dict(item))
    except ValidationError:
        return None
    return None


def _merge_items(
    items: Sequence[tuple[str, Sequence[Any], int]],
) -> tuple[list[str], dict[str, list[HttpExchangeIn]], dict[str, int]]:
    """Validate, merge a case named twice (result order), cap each case at 20."""
    order: list[str] = []
    kept: dict[str, list[HttpExchangeIn]] = {}
    dropped: dict[str, int] = {}
    for case_id, exchanges, already_dropped in items:
        key = str(case_id)
        if key not in kept:
            order.append(key)
            kept[key] = []
            dropped[key] = 0
        dropped[key] += max(0, int(already_dropped or 0))
        for raw in exchanges or ():
            model = _revalidate(raw)
            if model is None or len(kept[key]) >= EXCHANGES_PER_TEST_MAX:
                dropped[key] += 1
                continue
            kept[key].append(model)
    return order, kept, dropped


async def write_exchanges(
    *,
    project_id: str,
    test_run_id: str,
    build_number: str,
    run_created_at: datetime,
    items: Sequence[tuple[str, Sequence[Mapping[str, Any] | HttpExchangeIn], int]],
    source: str,
) -> WriteCounts:
    """Store the exchanges of one or more test cases of ONE run. Never raises."""
    if source not in CAPTURE_SOURCES:
        raise ValueError(f"unknown capture source {source!r}")
    order, kept, dropped = _merge_items(items)
    if not any(kept.values()):
        total_dropped = sum(dropped.values())
        _count("dropped", source, total_dropped)
        return WriteCounts(stored=0, truncated=0, dropped=total_dropped, write_failed=False)

    run_key = str(test_run_id)
    attempted = sum(len(v) for v in kept.values())
    try:
        coll = _collection()
        # The cap counts OTHER cases only: the cases of this write are replaced
        # below, so a re-delivery of a full run is not refused by its own docs.
        existing = await coll.count_documents(
            {"test_run_id": run_key, "test_case_id": {"$nin": order}}, limit=EXCHANGES_PER_RUN_MAX,
        )
        remaining = max(0, EXCHANGES_PER_RUN_MAX - int(existing))
        now = datetime.now(timezone.utc)
        base = {
            "v": EXCHANGE_DOC_VERSION, "project_id": str(project_id), "test_run_id": run_key,
            "build_number": str(build_number), "run_created_at": run_created_at,
        }
        docs: list[dict[str, Any]] = []
        truncated = 0
        for case_id in order:
            first: Optional[dict[str, Any]] = None
            for seq, model in enumerate(kept[case_id]):
                if remaining <= 0:
                    dropped[case_id] += len(kept[case_id]) - seq
                    break
                doc = _exchange_doc(model, seq=seq, source=source,
                                    base={**base, "test_case_id": case_id}, now=now)
                if doc["request"]["body_truncated"] or doc["response"]["body_truncated"]:
                    truncated += 1
                if first is None:
                    first = doc
                docs.append(doc)
                remaining -= 1
            if first is not None:
                first["dropped_for_test"] = dropped[case_id]
        await coll.delete_many({"test_run_id": run_key, "test_case_id": {"$in": order}})
        if docs:
            await coll.insert_many(docs, ordered=False)
    except Exception as exc:  # noqa: BLE001 — best-effort store; the ingest goes on
        _count("write_failed", source, attempted)
        _slog.warning(
            "http_exchanges_write_failed",
            test_run_id=run_key, source=source, exchanges=attempted, error_type=type(exc).__name__,
        )
        return WriteCounts(stored=0, truncated=0, dropped=sum(dropped.values()), write_failed=True)

    total_dropped = sum(dropped.values())
    _count("stored", source, len(docs))
    _count("truncated", source, truncated)
    _count("dropped", source, total_dropped)
    return WriteCounts(stored=len(docs), truncated=truncated, dropped=total_dropped, write_failed=False)


def _case_filter(project_id: str, test_run_id: str, test_case_id: str) -> dict[str, str]:
    return {"project_id": str(project_id), "test_run_id": str(test_run_id), "test_case_id": str(test_case_id)}


async def _read_case(query: dict[str, str], limit: int) -> list[dict[str, Any]]:
    cursor = _collection().find(query, {"_id": 0}).sort("seq", 1).limit(limit)
    return list(await cursor.to_list(length=limit))


async def list_for_case(
    *, project_id: str, test_run_id: str, test_case_id: str,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    """(items ≤ LIST_PAGE_MAX in seq order, total stored, dropped, run_cap_reached). Reads only."""
    query = _case_filter(project_id, test_run_id, test_case_id)
    try:
        items = await _read_case(query, LIST_PAGE_MAX)
        total = int(await _collection().count_documents(query))
        in_run = int(await _collection().count_documents(
            {"project_id": str(project_id), "test_run_id": str(test_run_id)}, limit=EXCHANGES_PER_RUN_MAX,
        ))
    except Exception as exc:  # noqa: BLE001 — surfaced as a 503 by the route
        _slog.warning("http_exchanges_read_failed", test_run_id=str(test_run_id),
                      error_type=type(exc).__name__)
        raise ExchangesUnavailable() from exc
    first = next((doc for doc in items if doc.get("seq") == 0), None)
    dropped = int(first.get("dropped_for_test") or 0) if first else 0
    return items, total, dropped, in_run >= EXCHANGES_PER_RUN_MAX


async def docs_for_case(*, project_id: str, test_run_id: str, test_case_id: str) -> list[dict[str, Any]]:
    """The tools' read: this case's docs (≤ 20) in seq order."""
    try:
        return await _read_case(_case_filter(project_id, test_run_id, test_case_id), EXCHANGES_PER_TEST_MAX)
    except Exception as exc:  # noqa: BLE001
        _slog.warning("http_exchanges_read_failed", test_run_id=str(test_run_id),
                      error_type=type(exc).__name__)
        raise ExchangesUnavailable() from exc


async def drift_baseline(
    *, project_id: str, method: str, endpoint: str, status_class: Optional[int],
    before: datetime, exclude_run_id: str,
) -> list[dict[str, Any]]:
    """Parsed docs of the project's ≤ DRIFT_BASELINE_RUNS newest runs created before
    ``before`` that captured the same (method, endpoint template, status class),
    newest run first; at most DRIFT_BASELINE_DOCS_MAX docs are read."""
    query: dict[str, Any] = {
        "project_id": str(project_id), "method": method, "endpoint": endpoint,
        "run_created_at": {"$lt": before}, "test_run_id": {"$ne": str(exclude_run_id)},
        "response_schema_state": "parsed",
    }
    if status_class is not None:
        query["status_class"] = status_class
    try:
        cursor = (
            _collection().find(query, _DRIFT_PROJECTION)
            .sort([("run_created_at", -1), ("test_run_id", 1)])
            .limit(DRIFT_BASELINE_DOCS_MAX)
        )
        docs = list(await cursor.to_list(length=DRIFT_BASELINE_DOCS_MAX))
    except Exception as exc:  # noqa: BLE001
        _slog.warning("http_exchanges_read_failed", project_id=str(project_id),
                      error_type=type(exc).__name__)
        raise ExchangesUnavailable() from exc
    runs: list[str] = []
    out: list[dict[str, Any]] = []
    for doc in docs:
        run = str(doc.get("test_run_id"))
        if run not in runs:
            if len(runs) >= DRIFT_BASELINE_RUNS:
                break
            runs.append(run)
        out.append(doc)
    return out


def to_read(doc: Mapping[str, Any]) -> dict[str, Any]:
    """A stored doc as the ``HttpExchangeRead`` shape (``redacted`` = any count > 0)."""
    def _side(side: Any) -> dict[str, Any]:
        side = side if isinstance(side, Mapping) else {}
        return {
            "headers": dict(side.get("headers") or {}), "body": side.get("body"),
            "body_truncated": bool(side.get("body_truncated")), "body_bytes": int(side.get("body_bytes") or 0),
        }

    counts = doc.get("redaction_counts") or {}
    return {
        "seq": int(doc.get("seq") or 0), "method": doc.get("method") or "", "endpoint": doc.get("endpoint") or "",
        "url": doc.get("url") or "", "host": doc.get("host"), "status": doc.get("status"),
        "duration_ms": doc.get("duration_ms"), "started_at": doc.get("started_at"), "error": doc.get("error"),
        "request": _side(doc.get("request")), "response": _side(doc.get("response")),
        "response_schema_state": doc.get("response_schema_state") or "empty",
        "source": doc.get("source") or "", "captured_at": doc.get("captured_at"),
        "redacted": any(int(v or 0) > 0 for v in counts.values()) if isinstance(counts, Mapping) else False,
    }
