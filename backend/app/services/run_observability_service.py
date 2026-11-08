"""W39 STRAT-07 (first slice): one run's observability evidence timeline, read from what is stored.

``GET /api/v1/runs/{run_id}/observability-timeline`` (``routers/run_observability.py``) answers with the Splunk /
OpenShift / Prometheus evidence the AI analysis ALREADY fetched (contract D-1). This module fetches NOTHING: no
fetcher, tool, LLM, ``runtime_integrations_for`` or ``GET /runs/{id}/intelligence`` (which writes a snapshot), and it
writes nothing to Postgres or Mongo -- stage-only and read-only (``tests/test_architectural_transaction_boundaries.py``).

Where each lane reads (D-2; ``observability_timeline_vocab``):

* ``prometheus`` / ``splunk_trace`` -- the NEWEST ``decision_evidence_snapshots`` doc for the run (greatest
  ``created_at``; ``decision_evidence_snapshot.build_decision_evidence_snapshot`` stamps it), its
  ``canonical_decision.log_findings``. With clusters, ``cluster_findings[]`` is read and the top level -- a copy of
  the first cluster (``log_intelligence_agent.run``) -- is NOT read again; without clusters (the no-context branch,
  ``cluster_findings: []``) the top-level ``app_metrics`` / ``distributed_trace`` are the run's own.
* ``ocp_events`` -- ``test_runs.ocp_metadata.events`` (fetched at ingest, stored unsanitized): ``reason`` /
  ``message`` pass through ``redact_text`` here (D-6); ``node`` (the host IP) is never read out.
* ``splunk_per_test`` / ``ocp_per_test`` -- ``ai_analysis.evidence_references`` of the run's test cases, only
  ``kind == "tool_observation"`` items whose ``source`` is a REAL tool name; the dev seed's ``splunk`` /
  ``ocp_events`` rows are never shown. Splunk excerpt lines ``[<_time>] [<level>] <message>`` become events; OCP
  answers carry no event time and are untimed (``no_time_stored``).

Never fabricate a time (D-3): an unreadable / redacted / missing time sends the item to its lane's ``untimed`` list.
Honest states in words (D-4); a Mongo outage reads ``unavailable`` with the store sentence, never "none found".
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.mongo import Collections, get_mongo_db
from app.models.postgres import AIAnalysis, TestCase, TestRun
from app.services import observability_timeline_vocab as v
from app.services.integration_config_service import _saved_integrations_row, effective_value
from app.services.redaction_service import redact_text

logger = structlog.get_logger("services.run_observability")

_S = v.LANE_SENTENCES
#: ``[<_time>] [<level>] <message>`` (``tools/query_splunk.py`` renders each hit like this). The time is matched
#: lazily so a redacted ``[[REDACTED_PHONE]]`` keeps its brackets.
_SPLUNK_LINE_RE = re.compile(r"^\[(?P<ts>.*?)\] \[(?P<level>[^\]\s]*)\] ?(?P<msg>.*)$")
_OCP_EVENT_TYPES = frozenset({"Normal", "Warning"})
_PROMETHEUS_EMPTY_OUTCOMES = frozenset({"ok", "no_data"})


# ── Small helpers ────────────────────────────────────────────────────────────────────────────────────────────────


def _text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if value is not None else ""


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _untimed_reason(raw: Any) -> str:
    """Nothing stored (``None``, empty, the tool's ``?``) is ``no_time_stored``; anything else unreadable."""
    return "no_time_stored" if raw is None or str(raw).strip() in ("", "?") else "time_unreadable"


def _event(when: v.EvidenceTime, title: str, **fields: Any) -> dict[str, Any]:
    end: Optional[v.EvidenceTime] = fields.pop("end", None)
    return {
        "at": when.at,
        "end": end.at if end is not None else None,
        "zone_assumed": when.zone_assumed or (end is not None and end.zone_assumed),
        "title": title,
        "detail": fields.get("detail") or None,
        "level": fields.get("level") or None,
        "cluster_id": fields.get("cluster_id"),
        "test_case_id": fields.get("test_case_id"),
        "test_name": fields.get("test_name"),
        "count": fields.get("count"),
    }


def _untimed(title: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {
        "title": title,
        "detail": fields.get("detail") or None,
        "test_case_id": fields.get("test_case_id"),
        "test_name": fields.get("test_name"),
        "reason": reason,
    }


def _joined(sentences: list[str]) -> str:
    """One distinct sentence verbatim; several (at most ``NOTES_MAX``) joined with ``; ``."""
    distinct = list(dict.fromkeys(s for s in sentences if s))[: v.NOTES_MAX]
    if len(distinct) == 1:
        return distinct[0]
    return "; ".join(s.rstrip(". ") for s in distinct) + "."


def _lane(
    source: str,
    state: str,
    sentence: str,
    *,
    truncated: bool = False,
    series: Optional[list[dict]] = None,
    events: Optional[list[dict]] = None,
    untimed: Optional[list[dict]] = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "label": v.LANE_LABELS[source],
        "fetched_by": v.LANE_FETCHED_BY[source],
        "state": state,
        "sentence": sentence,
        "truncated": truncated,
        "series": series or [],
        "events": sorted(events or [], key=lambda e: e["at"]),
        "untimed": untimed or [],
    }


def _decide(
    source: str, *, items: bool, outcomes: list[tuple[str, str]], recorded: bool, analysed: bool,
) -> tuple[str, str]:
    """(state, sentence) from what the lane found: items → ``evidence`` (+ the other results as a note); a stored
    refusal / failure → ``unavailable``; only empty answers → ``none_found``; a run that was analysed but left nothing
    for this source → ``not_recorded``; never analysed → ``not_analysed``."""
    unavailable = [sentence for kind, sentence in outcomes if kind == "unavailable"]
    empty = [sentence for kind, sentence in outcomes if kind == "none"]
    if items:
        notes = unavailable + empty
        base = _S[f"{source}_evidence"]
        return "evidence", (f"{base} {_S['partial']} {_joined(notes)}" if notes else base)
    if unavailable:
        return "unavailable", _joined(unavailable + empty)
    if empty:
        return "none_found", _joined(empty)
    if recorded or analysed:
        return "not_recorded", _S[f"{source}_not_recorded"]
    return "not_analysed", _S[f"{source}_not_analysed"]


# ── Path B: the newest decision evidence snapshot (prometheus, splunk_trace) ─────────────────────────────────────


def _clusters(log_findings: dict) -> tuple[list[tuple[Optional[str], dict]], bool]:
    """(cluster id, cluster dict) to read, and whether clusters were cut. With ``cluster_findings`` the top level (a
    copy of the first cluster) is skipped, so nothing is counted twice; without them the top level is the run's."""
    raw = log_findings.get("cluster_findings")
    if isinstance(raw, list) and raw:
        items = [item for item in raw if isinstance(item, dict)]
        kept = [(_text(item.get("cluster_id"), 64) or None, item) for item in items[: v.CLUSTERS_MAX]]
        return kept, len(items) > v.CLUSTERS_MAX
    return [(None, log_findings)], False


def _prometheus_lane(log_findings: dict) -> dict[str, Any]:
    clusters, truncated = _clusters(log_findings)
    blocks = [(cid, c["app_metrics"]) for cid, c in clusters if isinstance(c.get("app_metrics"), dict)]
    series: list[dict] = []
    outcomes: list[tuple[str, str]] = []
    for cluster_id, block in blocks:
        outcome = str(block.get("outcome") or "")
        stored = _text(block.get("sentence"), v.DETAIL_MAX)
        raw_series = _list(block.get("series"))
        truncated = truncated or len(raw_series) > v.SERIES_PER_CLUSTER_MAX
        kept = 0
        for item in raw_series[: v.SERIES_PER_CLUSTER_MAX]:
            if not isinstance(item, dict):
                continue
            raw_points = _list(item.get("points"))
            truncated = truncated or len(raw_points) > v.POINTS_PER_SERIES_MAX
            points = [
                [float(pair[0]), _number(pair[1])]
                for pair in raw_points[: v.POINTS_PER_SERIES_MAX]
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and v.parse_evidence_time(_number(pair[0]))
            ]
            if not points:
                continue
            kept += 1
            series.append({
                "cluster_id": cluster_id,
                "label": _text(item.get("label"), v.TITLE_MAX) or _text(item.get("template_id"), 40),
                "unit": _text(item.get("unit"), 20) or "count",
                "points": points,
                "sentence": _text(item.get("sentence"), v.DETAIL_MAX),
                "rose": item.get("rose") is True,
            })
        if outcome in _PROMETHEUS_EMPTY_OUTCOMES:
            if not kept:
                outcomes.append(("none", stored or _S["prometheus_none_found"]))
        else:   # a refusal / failure outcome (its vocabulary sentence), even when partial series survived
            outcomes.append(("unavailable", stored or _S["prometheus_unavailable"]))
    state, sentence = _decide("prometheus", items=bool(series), outcomes=outcomes, recorded=False, analysed=True)
    return _lane("prometheus", state, sentence, truncated=truncated, series=series)


def _trace_items(cluster_id: Optional[str], steps: list) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    untimed: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        message = _text(step.get("message"), v.TITLE_MAX)
        service = _text(step.get("service"), 128)
        level = _text(step.get("level"), 20)
        title = message or service or "Log event"
        when = v.parse_evidence_time(step.get("timestamp"))
        if when is not None:   # the host is never read out
            events.append(_event(when, title, detail=service, level=level, cluster_id=cluster_id))
        else:
            untimed.append(_untimed(title, _untimed_reason(step.get("timestamp")), detail=service))
    return events, untimed


def _splunk_trace_lane(log_findings: dict) -> dict[str, Any]:
    clusters, truncated = _clusters(log_findings)
    traces = [(cid, c["distributed_trace"]) for cid, c in clusters
              if isinstance(c.get("distributed_trace"), dict) and c["distributed_trace"]]
    events: list[dict] = []
    untimed: list[dict] = []
    outcomes: list[tuple[str, str]] = []
    for cluster_id, trace in traces:
        if "error" in trace:   # the stage's {"error": <ExceptionType>}
            outcomes.append(("unavailable", _S["splunk_trace_failed"]))
            continue
        steps = _list(trace.get("trace_steps"))
        truncated = truncated or len(steps) > v.TRACE_STEPS_PER_CLUSTER_MAX
        found, unread = _trace_items(cluster_id, steps[: v.TRACE_STEPS_PER_CLUSTER_MAX])
        events += found
        untimed += unread
        if found or unread:
            continue
        summary = _text(trace.get("causal_summary"), v.DETAIL_MAX)
        if summary.startswith(v.TRACE_NO_EVENTS_PREFIX):
            # A failed / unconfigured search also reads "no events" (reconstruct_trace._query_splunk returns []).
            outcomes.append(("unavailable", _S["splunk_trace_no_events"]))
        else:   # a stored refusal ("Splunk integration is disabled…"), redacted at write: verbatim
            outcomes.append(("unavailable", summary or _S["splunk_trace_unavailable"]))
    state, sentence = _decide(
        "splunk_trace", items=bool(events or untimed), outcomes=outcomes, recorded=False, analysed=True)
    return _lane("splunk_trace", state, sentence, truncated=truncated, events=events, untimed=untimed)


def _path_b_lanes_without_findings(state: str, sentence_key: Optional[str] = None) -> list[dict[str, Any]]:
    return [_lane(source, state, _S[sentence_key or f"{source}_{state}"]) for source in ("prometheus", "splunk_trace")]


# ── Path C: test_runs.ocp_metadata.events (fetched at ingest) ────────────────────────────────────────────────────


def _ocp_events_lane(meta: Any) -> dict[str, Any]:
    source = "ocp_events"
    if meta is None:
        return _lane(source, "not_recorded", _S["ocp_events_not_recorded"])
    raw_events = meta.get("events") if isinstance(meta, dict) else None
    if not isinstance(raw_events, list):
        return _lane(source, "unavailable", _S["ocp_events_unavailable"])
    timed: list[tuple[datetime, dict]] = []
    untimed: list[dict] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        # Stored WITHOUT the sanitizer (ocp_enrichment.stage_ocp_metadata): redact on read (D-6). Never ``node``.
        title = redact_text(_text(item.get("reason"), v.TITLE_MAX)) or "Pod event"
        detail = redact_text(_text(item.get("message"), v.DETAIL_MAX))
        kind = item.get("type") if item.get("type") in _OCP_EVENT_TYPES else None
        raw_count = item.get("count")
        count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0 else None
        first = v.parse_evidence_time(item.get("first_time"))
        last = v.parse_evidence_time(item.get("last_time"))
        start = first or last
        if start is None:
            raw = item.get("first_time") or item.get("last_time")
            untimed.append(_untimed(title, _untimed_reason(raw), detail=detail))
            continue
        end = last if first is not None and last is not None and last.at > first.at else None
        timed.append(((end or start).at, _event(start, title, end=end, detail=detail, level=kind, count=count)))
    total = len(timed) + len(untimed)
    if not total:
        return _lane(source, "none_found", _S["ocp_events_none_found"])
    newest = [event for _at, event in sorted(timed, key=lambda pair: pair[0], reverse=True)[: v.OCP_EVENTS_MAX]]
    kept_untimed = untimed[: max(0, v.OCP_EVENTS_MAX - len(newest))]
    return _lane(source, "evidence", _S["ocp_events_evidence"], truncated=total > v.OCP_EVENTS_MAX,
                 events=newest, untimed=kept_untimed)


# ── Path A: ai_analysis.evidence_references of the run's test cases (splunk_per_test, ocp_per_test) ──────────────


def _splunk_excerpt_items(excerpt: str, ids: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """The lines after the ``=== Splunk Log Correlation (N entries) ===`` header. A line the 500-char excerpt cut is
    untimed with its raw text as the title; a message's own next line joins the item before it."""
    events: list[dict] = []
    untimed: list[dict] = []
    last: Optional[dict] = None
    for line in excerpt.splitlines()[1:]:
        if not line.strip():
            continue
        match = _SPLUNK_LINE_RE.match(line)
        if match is None:
            if last is not None and not line.startswith("["):
                last["detail"] = _text(f"{last['detail'] or ''}\n{line.strip()}".strip(), v.DETAIL_MAX)
                continue
            last = _untimed(_text(line, v.TITLE_MAX), "time_unreadable" if line.startswith("[") else "no_time_stored",
                            **ids)
            untimed.append(last)
            continue
        when = v.parse_evidence_time(match["ts"])
        level = match["level"] if match["level"] not in ("", "?") else None
        if when is not None:
            last = _event(when, _text(match["msg"], v.TITLE_MAX) or _text(line, v.TITLE_MAX), level=level, **ids)
            events.append(last)
        else:
            last = _untimed(_text(line, v.TITLE_MAX), _untimed_reason(match["ts"]), **ids)
            untimed.append(last)
    return events, untimed


def _ocp_excerpt_item(excerpt: str, ids: dict[str, Any]) -> dict:
    """``Pod: … | Namespace: … | Phase: …`` + its event lines: the answer drops every event time (ocp_client)."""
    first, _, rest = excerpt.partition("\n")
    return _untimed(_text(first, v.TITLE_MAX), "no_time_stored", detail=_text(rest, v.DETAIL_MAX), **ids)


def _collapse(source: str, outcomes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """One distinct stored answer per kind stays verbatim; several become the lane's own sentence."""
    collapsed: list[tuple[str, str]] = []
    for kind, generic in (("unavailable", _S[f"{source}_unavailable"]), ("none", _S[f"{source}_none_found"])):
        distinct = list(dict.fromkeys(sentence for k, sentence in outcomes if k == kind))
        if distinct:
            collapsed.append((kind, distinct[0] if len(distinct) == 1 else generic))
    return collapsed


async def _per_test_lanes(db: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]]:
    tools = v.PER_TEST_TOOL_SOURCES
    as_text = cast(AIAnalysis.evidence_references, String)
    rows = (await db.execute(
        select(TestCase.id, TestCase.test_name, AIAnalysis.evidence_references)
        .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
        .where(TestCase.test_run_id == run_id, or_(*(as_text.like(f"%{name}%") for name in tools.values())))
        .order_by(TestCase.test_name, TestCase.id)
        .limit(v.PER_TEST_ANALYSES_MAX + 1)
    )).all()
    rows_cut = len(rows) > v.PER_TEST_ANALYSES_MAX
    analysed = bool(rows) or (await db.execute(
        select(AIAnalysis.id).join(TestCase, TestCase.id == AIAnalysis.test_case_id)
        .where(TestCase.test_run_id == run_id).limit(1)
    )).first() is not None
    lane_for_tool = {name: source for source, name in tools.items()}
    acc: dict[str, dict[str, Any]] = {
        source: {"events": [], "untimed": [], "outcomes": [], "recorded": False} for source in tools
    }
    for case_id, test_name, refs in rows[: v.PER_TEST_ANALYSES_MAX]:
        for ref in _list(refs):
            if not isinstance(ref, dict) or ref.get("kind") != v.TOOL_OBSERVATION_KIND:
                continue
            source = lane_for_tool.get(str(ref.get("source")))   # never the seed's "splunk" / "ocp_events"
            excerpt = ref.get("excerpt")
            if source is None or not isinstance(excerpt, str):
                continue
            text = excerpt.strip()
            ids = {"test_case_id": str(case_id), "test_name": _text(test_name, v.TITLE_MAX) or None}
            lane = acc[source]
            lane["recorded"] = True
            found, empty = ((v.SPLUNK_FOUND_PREFIX, v.SPLUNK_NO_HITS_PREFIX) if source == "splunk_per_test"
                            else (v.OCP_FOUND_PREFIX, v.OCP_NO_EVENTS_PREFIX))
            if text.startswith(found) and source == "splunk_per_test":
                events, untimed = _splunk_excerpt_items(text, ids)
                lane["events"] += events
                lane["untimed"] += untimed
            elif text.startswith(found):
                lane["untimed"].append(_ocp_excerpt_item(text, ids))
            elif text.startswith(empty):
                lane["outcomes"].append(("none", _text(text, v.DETAIL_MAX)))
            elif text:   # a stored refusal / failure sentence (redacted at write): verbatim
                lane["outcomes"].append(("unavailable", _text(text, v.DETAIL_MAX)))
    lanes: list[dict[str, Any]] = []
    for source, lane in acc.items():
        events = sorted(lane["events"], key=lambda e: e["at"])
        total = len(events) + len(lane["untimed"])
        kept_events = events[: v.PER_TEST_ITEMS_MAX]
        kept_untimed = lane["untimed"][: max(0, v.PER_TEST_ITEMS_MAX - len(kept_events))]
        state, sentence = _decide(source, items=bool(total), outcomes=_collapse(source, lane["outcomes"]),
                                  recorded=lane["recorded"], analysed=analysed)
        lanes.append(_lane(source, state, sentence, truncated=rows_cut or total > v.PER_TEST_ITEMS_MAX,
                           events=kept_events, untimed=kept_untimed))
    return lanes


# ── The two "now" facts, the windows, the entry point ────────────────────────────────────────────────────────────


async def _configured_now(db: AsyncSession) -> dict[str, bool]:
    """Booleans only, from the saved ``integrations_config`` row (ONE select) with the environment for keys never
    saved -- the read-only half of ``resolve_runtime_integrations``. No secret is read (``read_secret`` may call
    Vault), so no token, URL or index can reach the response."""
    saved = await _saved_integrations_row(db)

    def value(key: str, env: str) -> Any:
        return effective_value(saved, key, getattr(settings, env, None))[0]

    splunk_url = _text(value("splunk_base_url", "SPLUNK_BASE_URL"), 2048)
    ocp_url = _text(value("ocp_api_url", "OCP_API_URL"), 2048)
    return {
        "splunk": bool(value("splunk_enabled", "SPLUNK_ENABLED")) and bool(splunk_url),
        "ocp": bool(value("ocp_enabled", "OCP_ENABLED")) and bool(ocp_url),
        # Saved-only, like resolve_prometheus_settings: the environment's PROMETHEUS_URL is TestLookup's own.
        "prometheus": saved.get("prometheus_enabled") is True and bool(_text(saved.get("prometheus_base_url"), 2048)),
    }


def _utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _path_b(project_id: uuid.UUID, run_id: uuid.UUID) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    """The newest snapshot's lanes + which analysis they come from. Every read keeps the run's predicate."""
    query = {"project_id": str(project_id), "test_run_id": str(run_id)}
    projection = {"_id": 0, "pipeline_run_id": 1, "created_at": 1, "canonical_decision.log_findings": 1}
    try:
        collection = get_mongo_db()[Collections.DECISION_EVIDENCE_SNAPSHOTS]
        docs = await (
            collection.find(query, projection).sort([("created_at", -1), ("pipeline_run_id", -1)]).limit(1)
        ).to_list(length=1)
        total = await collection.count_documents(query) if docs else 0
    except Exception as exc:  # noqa: BLE001 - an unreadable store is a lane state (200), never a 500 or "none found"
        logger.warning("observability_timeline_store_unreadable", error_type=type(exc).__name__)
        return _path_b_lanes_without_findings("unavailable", "store_unreadable"), None
    if not docs:
        return _path_b_lanes_without_findings("not_analysed"), None
    doc = docs[0]
    analysis = {
        "pipeline_run_id": _text(doc.get("pipeline_run_id"), 64),
        "analysed_at": _utc(doc.get("created_at")),
        "other_analyses": max(0, int(total) - 1),
    }
    decision = doc.get("canonical_decision")
    log_findings = decision.get("log_findings") if isinstance(decision, dict) else None
    if not isinstance(log_findings, dict) or not log_findings:
        return _path_b_lanes_without_findings("not_recorded"), analysis
    return [_prometheus_lane(log_findings), _splunk_trace_lane(log_findings)], analysis


def _run_window(run: Any) -> Optional[dict[str, datetime]]:
    """The run's own start / end: the EXEC-02 wall clock when both ends are known, else the report's start / end."""
    for start, end in ((run.exec_started_at, run.exec_finished_at), (run.start_time, run.end_time)):
        start, end = _utc(start), _utc(end)
        if start is not None and end is not None and end >= start:
            return {"start": start, "end": end}
    return None


def _axis(lanes: list[dict[str, Any]], window: Optional[dict[str, datetime]]) -> Optional[dict[str, datetime]]:
    """min / max over every TIMED item (+ the run window); ``None`` when nothing is timed -- never a recomputed
    Prometheus window (D-3: the stored block keeps no window)."""
    instants: list[datetime] = []
    for lane in lanes:
        for event in lane["events"]:
            instants.extend(at for at in (event["at"], event["end"]) if at is not None)
        for series in lane["series"]:
            instants.extend(datetime.fromtimestamp(point[0], tz=timezone.utc) for point in series["points"])
    if not instants:
        return None
    if window is not None:
        instants += [window["start"], window["end"]]
    return {"start": min(instants), "end": max(instants)}


async def build_run_observability_timeline(db: AsyncSession, run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """The §1.2 ``RunObservabilityTimeline`` for an authorised run, or ``None`` for an unknown run (the route's 404).

    Reads only: the run row, the newest snapshot (Mongo), the run's analysed test cases' evidence references and the
    saved integrations row. Nothing is fetched, nothing is written.
    """
    run = (await db.execute(
        select(TestRun.id, TestRun.project_id, TestRun.start_time, TestRun.end_time, TestRun.exec_started_at,
               TestRun.exec_finished_at, TestRun.ocp_metadata).where(TestRun.id == run_id)
    )).one_or_none()
    if run is None:
        return None
    path_b, analysis = await _path_b(run.project_id, run_id)
    lanes = [*path_b, _ocp_events_lane(run.ocp_metadata), *(await _per_test_lanes(db, run_id))]
    window = _run_window(run)
    return {
        "run_id": run_id,
        "run_window": window,
        "analysis": analysis,
        "offline_now": bool(settings.AI_OFFLINE_MODE),
        "configured_now": await _configured_now(db),
        "axis": _axis(lanes, window),
        "lanes": lanes,
    }
