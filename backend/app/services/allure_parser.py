"""Allure JSON result file parser."""
import json
import posixpath
from typing import Optional


def parse_allure_result(data: dict, test_run_id: str, s3_key: str) -> Optional[dict]:
    """Parse a single Allure *-result.json file into a normalised dict."""
    if not data.get("name"):
        return None

    labels = {label["name"]: label["value"] for label in data.get("labels", [])}
    steps = data.get("steps", [])
    attachments = data.get("attachments", [])

    error_message = None
    status_details = data.get("statusDetails", {})
    if status_details:
        error_message = status_details.get("message") or status_details.get("trace")

    return {
        "allure_uuid": data.get("uuid"),
        "test_run_id": test_run_id,
        "test_name": data.get("name", "Unknown"),
        "full_name": data.get("fullName"),
        "suite_name": labels.get("suite") or labels.get("parentSuite"),
        "class_name": labels.get("testClass"),
        "package_name": labels.get("package"),
        "status": data.get("status", "unknown"),
        "duration_ms": _calc_duration(data.get("start"), data.get("stop")),
        "severity": labels.get("severity"),
        "feature": labels.get("feature"),
        "story": labels.get("story"),
        "epic": labels.get("epic"),
        "owner": labels.get("owner"),
        "tags": [label["value"] for label in data.get("labels", []) if label["name"] == "tag"],
        "error_message": error_message,
        "minio_s3_prefix": s3_key.rsplit("/", 1)[0] + "/",
        "attachments": attachments,
        "steps": steps,
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
