"""Allure JSON result file parser."""
import json
import posixpath
from typing import Optional

# Allure step/test status → TestLookup status vocab (PASSED/FAILED/SKIPPED/
# BROKEN/UNKNOWN). The granular ``test_steps.status`` column is fronted by the
# strict ``TestStatus`` enum, so a value outside this vocab silently 422s the
# read endpoint — every emitted step status MUST land in these five.
_ALLURE_STATUS_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "broken": "BROKEN",
    "skipped": "SKIPPED",
    "unknown": "UNKNOWN",
}


# Hard caps on the granular step tree. Customer-supplied ``-result.json`` is
# untrusted input at the upload/webhook boundary; Allure permits arbitrarily
# deep/wide ``steps`` nesting (before/after fixtures + sub-steps). Without a cap
# a pathological tree (a) blows Python's ~1000-frame recursion limit →
# RecursionError that escapes the per-file try/except mid-ingest, and (b)
# materialises an unbounded number of ``test_steps`` rows + per-step flushes
# inside the single ingestion transaction. Stop recursing past MAX_DEPTH and
# stop emitting past MAX_NODES; the remainder is dropped (the snapshot is a
# best-effort latest-run view, not an audit log).
_MAX_STEP_DEPTH = 20
_MAX_STEP_NODES = 2000
_MAX_METADATA_ITEMS = 100
_MAX_METADATA_VALUE_LENGTH = 4096
_MAX_TEST_PARAMETERS = 100
_MAX_TEST_ATTACHMENTS = 100


def _map_step_status(raw: Optional[str]) -> str:
    return _ALLURE_STATUS_MAP.get(str(raw or "").lower(), "UNKNOWN")


def _source_value(raw) -> Optional[str]:
    """Keep opaque source identifiers bounded and stable for persistence."""
    return str(raw)[:255] if raw is not None else None


def _parse_allure_attachment(att: dict) -> dict:
    """Normalise one Allure attachment into the common attachment dict.

    Index-only (Phase 1): keep the reference (``source``) and MIME (``type``),
    do not proxy bytes.
    """
    return {
        "name": str(att.get("name") or att.get("source") or "attachment")[:500],
        "source_ref": str(att["source"])[:1000] if isinstance(att.get("source"), str) else None,
        "media_type": str(att["type"])[:100] if isinstance(att.get("type"), str) else None,
    }


def _parse_allure_parameters(raw: object, *, counter: Optional[dict] = None) -> list[dict]:
    """Normalize bounded test-level parameters; malformed items are skipped."""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    remaining = _MAX_TEST_PARAMETERS - int((counter or {}).get("parameters", 0))
    for item in raw[:max(0, min(_MAX_METADATA_ITEMS, remaining))]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"].strip()
        if not name:
            continue
        mode = str(item.get("mode"))[:30] if item.get("mode") is not None else None
        masked = bool(item.get("masked")) or mode in {"masked", "hidden"}
        value = item.get("value")
        if isinstance(value, str):
            value = value[:_MAX_METADATA_VALUE_LENGTH]
        elif not isinstance(value, (int, float, bool)):
            value = None
        if masked:
            value = None
        normalized = {
            "name": name[:255],
            "value": value,
        }
        # Keep the legacy sparse shape stable; add optional metadata only when
        # the source actually supplied a meaningful value.
        if mode is not None or counter is None:
            normalized["mode"] = mode
        if masked:
            normalized["masked"] = True
        if item.get("excludedFromHistory", False):
            normalized["excluded_from_history"] = True
        result.append(normalized)
        if counter is not None:
            counter["parameters"] = counter.get("parameters", 0) + 1
    return result


def _parse_allure_links(raw: object) -> list[dict]:
    """Normalize bounded issue/document links without trusting arbitrary data."""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw[:_MAX_METADATA_ITEMS]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        url = item["url"].strip()
        if not url:
            continue
        result.append({
            "url": url[:2000],
            "name": str(item.get("name"))[:255] if item.get("name") is not None else None,
            "type": str(item.get("type"))[:50] if item.get("type") is not None else None,
        })
    return result


def _parse_allure_steps(
    raw_steps: list,
    depth: int = 0,
    counter: Optional[dict] = None,
) -> list[dict]:
    """Recursively normalise Allure ``steps`` into the common step dict shape.

    Allure nests ``steps`` arbitrarily (before/after fixtures + sub-steps).
    Each node carries ``name``, ``status``, ``start``/``stop``, optional
    ``statusDetails`` (message/trace), ``parameters`` and ``attachments``.

    Bounded by ``_MAX_STEP_DEPTH`` (stop recursing into deeper subtrees) and a
    shared ``_MAX_STEP_NODES`` node budget (stop emitting once exhausted) so a
    hostile/pathological tree cannot RecursionError the worker or blow up the
    ``test_steps`` table — untrusted-input hardening at the ingest boundary.
    """
    out: list[dict] = []
    if not isinstance(raw_steps, list):
        return out
    if depth > _MAX_STEP_DEPTH:
        return out  # truncate subtrees deeper than the cap
    if counter is None:
        counter = {"nodes": 0}
    for node in raw_steps:
        if not isinstance(node, dict):
            continue
        if counter["nodes"] >= _MAX_STEP_NODES:
            break  # global node budget exhausted — drop the remainder
        counter["nodes"] += 1
        details = node.get("statusDetails")
        details = details if isinstance(details, dict) else {}
        params = _parse_allure_parameters(node.get("parameters"), counter=counter)
        raw_node_attachments = node.get("attachments")
        raw_node_attachments = raw_node_attachments if isinstance(raw_node_attachments, list) else []
        out.append({
            "name": node.get("name") or "step",
            "keyword": None,
            "status": _map_step_status(node.get("status")),
            "start_ms": node.get("start"),
            "duration_ms": _calc_duration(node.get("start"), node.get("stop")),
            "assertion_message": details.get("message"),
            "assertion_trace": details.get("trace"),
            "expected": None,
            "actual": None,
            "parameters": params if isinstance(params, list) else [],
            "attachments": [],
            "steps": _parse_allure_steps(node.get("steps") or [], depth + 1, counter),
        })
        for attachment in raw_node_attachments:
            if counter.get("attachments", 0) >= _MAX_TEST_ATTACHMENTS:
                break
            if isinstance(attachment, dict):
                out[-1]["attachments"].append(_parse_allure_attachment(attachment))
                counter["attachments"] = counter.get("attachments", 0) + 1
    return out


def parse_allure_result(data: dict, test_run_id: str, s3_key: str) -> Optional[dict]:
    """Parse a single Allure *-result.json file into a normalised dict."""
    test_name = data.get("name")
    if not isinstance(test_name, str) or not test_name.strip():
        return None
    status = data.get("status") if isinstance(data.get("status"), str) else "unknown"

    raw_labels = data.get("labels")
    normalized_labels = [
        {"name": str(label.get("name"))[:100], "value": str(label.get("value"))[:500]}
        for label in (raw_labels if isinstance(raw_labels, list) else [])
        if isinstance(label, dict) and label.get("name") is not None and label.get("value") is not None
    ]
    labels = {label["name"]: label["value"] for label in normalized_labels}
    raw_steps = data.get("steps")
    raw_steps = raw_steps if isinstance(raw_steps, list) else []
    raw_attachments = data.get("attachments")
    raw_attachments = raw_attachments if isinstance(raw_attachments, list) else []

    error_message = None
    error_trace = None
    status_details = data.get("statusDetails")
    status_details = status_details if isinstance(status_details, dict) else {}
    if status_details:
        error_message = next(
            (value for value in (status_details.get("message"), status_details.get("trace"))
             if isinstance(value, str) and value),
            None,
        )
        error_trace = status_details.get("trace") if isinstance(status_details.get("trace"), str) else None

    steps = _parse_allure_steps(raw_steps)
    parser_version = (
        data.get("frameworkVersion")
        or data.get("reporterVersion")
        or data.get("version")
    )
    return {
        "allure_uuid": data.get("uuid"),
        "source_uuid": _source_value(data.get("uuid")),
        "source_history_id": _source_value(data.get("historyId")),
        "source_test_case_id": _source_value(data.get("testCaseId")),
        "source_parameters": _parse_allure_parameters(data.get("parameters")),
        "source_links": _parse_allure_links(data.get("links")),
        "source_labels": normalized_labels,
        "source_extensions": {},
        "parser_format": "allure",
        "parser_version": str(parser_version)[:100] if parser_version is not None else None,
        "test_run_id": test_run_id,
        "test_name": test_name[:1000],
        "full_name": data.get("fullName"),
        "suite_name": labels.get("suite") or labels.get("parentSuite"),
        "class_name": labels.get("testClass"),
        "package_name": labels.get("package"),
        "status": status,
        "duration_ms": _calc_duration(data.get("start"), data.get("stop")),
        "severity": labels.get("severity"),
        "feature": labels.get("feature"),
        "story": labels.get("story"),
        "epic": labels.get("epic"),
        "owner": labels.get("owner"),
        "tags": [label["value"] for label in normalized_labels if label["name"] == "tag"],
        "error_message": error_message,
        "stack_trace": error_trace,
        # Test-level attachments in the common shape (step_id resolves to NULL).
        "attachments": [
            _parse_allure_attachment(a)
            for a in raw_attachments[:_MAX_TEST_ATTACHMENTS]
            if isinstance(a, dict)
        ],
        # Recursive granular step tree (common shape) for the latest-run snapshot.
        "steps": steps,
        "steps_present": bool(steps),
        "minio_s3_prefix": s3_key.rsplit("/", 1)[0] + "/",
    }


def _calc_duration(start: Optional[int], stop: Optional[int]) -> Optional[int]:
    if start is not None and stop is not None:
        return max(0, stop - start)
    return None


def parse_allure_zip(
    files: dict[str, bytes],
    test_run_id: str,
    s3_prefix: str = "uploads",
) -> list[dict]:
    """Parse an extracted Allure results directory (``{name: bytes}``) into a
    list of normalized result dicts (MRU-12).

    Reuses ``parse_allure_result`` per ``*-result.json``. Then:
      * enriches ``suite_name`` from ``*-container.json`` ``children`` linkage
        ONLY when a result carried no suite label (label-first precedence);
      * collapses retries (same ``historyId``) to the LATEST attempt, tagging
        ``is_flaky`` when attempts disagree on status — because the downstream
        per-(run, fingerprint) upsert would otherwise silently keep an arbitrary
        attempt and lose the flaky signal.
    """
    parsed: list[dict] = []
    containers: list[dict] = []

    for name, raw in files.items():
        base = posixpath.basename(name).lower()
        try:
            doc = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            continue  # one corrupt entry must not fail the whole upload
        if not isinstance(doc, dict):
            continue
        if base.endswith("-result.json"):
            s3_key = f"{s3_prefix}/{test_run_id}/{posixpath.basename(name)}"
            res = parse_allure_result(doc, test_run_id, s3_key)
            if res:
                res["_history_id"] = doc.get("historyId")
                res["_sort_key"] = doc.get("stop") or doc.get("start") or 0
                parsed.append(res)
        elif base.endswith("-container.json"):
            containers.append(doc)

    _enrich_suites_from_containers(parsed, containers)
    return _collapse_retries(parsed)


def _enrich_suites_from_containers(parsed: list[dict], containers: list[dict]) -> None:
    """Backfill suite_name from container ``children`` linkage for results that
    have no suite label. Nearest named container wins; existing labels are kept."""
    if not containers or not any(not r.get("suite_name") for r in parsed):
        return
    child_to_name: dict[str, str] = {}
    for c in containers:
        cname = c.get("name")
        if not cname:
            continue  # unnamed (pure-fixture) container — hierarchy only
        for child in c.get("children", []) or []:
            child_to_name.setdefault(child, cname)
    for res in parsed:
        if not res.get("suite_name"):
            uuid = res.get("allure_uuid")
            if uuid and uuid in child_to_name:
                res["suite_name"] = child_to_name[uuid]


def _collapse_retries(parsed: list[dict]) -> list[dict]:
    """Keep the latest attempt per historyId (fallback: class::name); tag
    is_flaky when a group's attempts disagree on status."""
    groups: dict[str, list[dict]] = {}
    for res in parsed:
        key = res.get("_history_id") or f"{res.get('class_name') or ''}::{res.get('test_name')}"
        groups.setdefault(key, []).append(res)

    out: list[dict] = []
    for group in groups.values():
        group.sort(key=lambda r: r.get("_sort_key") or 0)
        survivor = group[-1]
        if len(group) >= 2:
            survivor["retry_count"] = len(group) - 1
            survivor["is_flaky"] = len({g.get("status") for g in group}) > 1
        for k in ("_history_id", "_sort_key"):
            survivor.pop(k, None)
        out.append(survivor)
    return out
