"""Tool: validate captured REST responses against the project's previous runs (W23d AGT-14 P-4).

Scope is ``project_id`` + ``test_run_id`` + ``test_case_id`` — all server-bound by the
contract stage; a missing one is refused BEFORE any Mongo call. ``pipeline_run_id`` is
still accepted (the stage sends it) and ignored: an ingest-time writer cannot know the
pipeline. The docs are the ones ``services/http_exchange_store.py`` wrote (redacted).

Drift is cross-run: for each parsed response, the newest of the project's ≤ 5 earlier
runs that captured the same (method, endpoint template, status class) gives the STABLE
keys (present in every doc of that run). A stable key absent now is ``schema_drift``
(critical), a stable key whose JSON type changed is ``type_drift`` (warning); added keys
are counted, never violations. A key outside ``FIELD_NAME_RE`` is never named. No LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

from langchain_core.tools import tool

# Kept on purpose: tests/services/test_contract_agent.py patches this name to prove the
# scope refusal happens before any Mongo access (the reads go through the store).
from app.db.mongo import get_mongo_db  # noqa: F401
from app.services import http_exchange_store
from app.services.http_exchange_vocab import CONTRACT_SENTENCES, FIELD_NAME_RE

_FIELD_NAME = re.compile(FIELD_NAME_RE)
_UNNAMED_KEYS = "_unnamed_keys"
_MAX_VIOLATIONS = 200
_MAX_ENDPOINTS = 20
_MAX_SUMMARY = 1_000


def _check_schema(response_body: dict, expected_fields: dict) -> list[dict]:
    violations: list[dict] = []
    for field, expected_type in expected_fields.items():
        if field not in response_body:
            violations.append({
                "field_path": str(field)[:240],
                "violation_type": "missing_field",
                "expected": str(expected_type)[:160],
                "actual": "absent",
                "severity": "critical",
            })
            continue
        actual_value = response_body[field]
        actual_type = type(actual_value).__name__
        if expected_type == "string" and not isinstance(actual_value, str):
            violations.append({"field_path": str(field)[:240], "violation_type": "type_mismatch", "expected": "string", "actual": actual_type, "severity": "warning"})
        elif expected_type == "number" and (isinstance(actual_value, bool) or not isinstance(actual_value, (int, float))):
            violations.append({"field_path": str(field)[:240], "violation_type": "type_mismatch", "expected": "number", "actual": actual_type, "severity": "warning"})
        elif expected_type == "boolean" and not isinstance(actual_value, bool):
            violations.append({"field_path": str(field)[:240], "violation_type": "type_mismatch", "expected": "boolean", "actual": actual_type, "severity": "warning"})
    return violations


def _named_schema(schema: Any) -> dict[str, str]:
    """A stored ``response_schema`` with only nameable keys (never ``_unnamed_keys``)."""
    if not isinstance(schema, Mapping):
        return {}
    return {
        str(key): str(kind)
        for key, kind in schema.items()
        if key != _UNNAMED_KEYS and isinstance(key, str) and _FIELD_NAME.match(key)
    }


def _stable_keys(baseline_docs: list[dict]) -> tuple[dict[str, Optional[str]], set[str]]:
    """(stable key -> its type when every doc agrees, else None; every key seen)."""
    schemas = [_named_schema(doc.get("response_schema")) for doc in baseline_docs]
    if not schemas:
        return {}, set()
    seen: set[str] = set().union(*schemas)
    stable = set(schemas[0]).intersection(*schemas[1:])
    types: dict[str, Optional[str]] = {}
    for key in sorted(stable):
        kinds = {schema[key] for schema in schemas}
        types[key] = kinds.pop() if len(kinds) == 1 else None
    return types, seen


def _parsed_body(doc: Mapping[str, Any]) -> dict:
    response = doc.get("response") if isinstance(doc.get("response"), Mapping) else {}
    body = response.get("body") if isinstance(response, Mapping) else None
    try:
        parsed = json.loads(body) if isinstance(body, str) else None
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _missing_scope() -> str:
    return json.dumps({"error": "project_id, test_run_id and test_case_id are required", "evidence_available": False})


async def _drift_for(doc: Mapping[str, Any], *, project_id: str, test_run_id: str) -> tuple[list[dict], Optional[str], Optional[str], int]:
    """(violations, baseline build or None, a note sentence or None, added-key count) for ONE doc."""
    method = str(doc.get("method") or "")
    endpoint = str(doc.get("endpoint") or "")
    before = doc.get("run_created_at")
    if before is None:
        return [], None, CONTRACT_SENTENCES["no_baseline"].format(endpoint=endpoint), 0
    baseline = await http_exchange_store.drift_baseline(
        project_id=project_id, method=method, endpoint=endpoint,
        status_class=doc.get("status_class"), before=before, exclude_run_id=test_run_id,
    )
    if not baseline:
        return [], None, CONTRACT_SENTENCES["no_baseline"].format(endpoint=endpoint), 0
    newest = baseline[0].get("test_run_id")
    build = str(baseline[0].get("build_number") or "")
    stable, seen = _stable_keys([d for d in baseline if d.get("test_run_id") == newest])
    current = _named_schema(doc.get("response_schema"))
    violations: list[dict] = []
    for key, was in stable.items():
        if key not in current:
            violations.append({
                "violation_type": "schema_drift", "severity": "critical", "field_path": key,
                "expected": "present", "actual": "absent", "method": method, "endpoint": endpoint,
                "baseline_build": build,
                "sentence": CONTRACT_SENTENCES["missing_field"].format(field=key, method=method, endpoint=endpoint, build=build),
            })
        elif was is not None and current[key] != was:
            violations.append({
                "violation_type": "type_drift", "severity": "warning", "field_path": key,
                "expected": was, "actual": current[key], "method": method, "endpoint": endpoint,
                "baseline_build": build,
                "sentence": CONTRACT_SENTENCES["type_changed"].format(
                    field=key, method=method, endpoint=endpoint, was=was, now=current[key], build=build,
                ),
            })
    return violations, build, None, len(set(current) - seen)


@tool
async def validate_api_contract(params_json: str) -> str:
    """Validate a test's captured REST responses against the project's previous runs.

    The caller must supply the server-bound project, test-run and test-case ids.
    ``pipeline_run_id`` is accepted and ignored. Global lookups are unsupported.
    """
    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return json.dumps({"error": "invalid_json", "evidence_available": False})
    if not isinstance(params, dict):
        return json.dumps({"error": "invalid_parameters", "evidence_available": False})

    test_case_id = str(params.get("test_case_id") or "")
    project_id = str(params.get("project_id") or "")
    test_run_id = str(params.get("test_run_id") or "")
    expected_fields = params.get("expected_fields") or {}
    check_drift = bool(params.get("check_drift", True))
    if not (test_case_id and project_id and test_run_id):
        return _missing_scope()

    # ExchangesUnavailable propagates: a store outage is a failed check, never "no capture".
    docs = await http_exchange_store.docs_for_case(
        project_id=project_id, test_run_id=test_run_id, test_case_id=test_case_id,
    )
    if not docs:
        return json.dumps({
            "endpoint": "unknown", "endpoints": [], "violations": [], "has_violations": False,
            "drift_summary": CONTRACT_SENTENCES["no_capture"], "exchanges_checked": 0,
            "baseline_builds": [], "schema_fingerprint": {}, "evidence_available": False,
        })

    endpoints: list[str] = []
    violations: list[dict] = []
    keys_seen: set[tuple[str, str, str, str]] = set()
    notes: list[str] = []
    builds: list[str] = []
    checked = added = 0
    fingerprint: dict[str, str] = {}
    for doc in docs:
        method, endpoint = str(doc.get("method") or ""), str(doc.get("endpoint") or "")
        if endpoint and endpoint not in endpoints and len(endpoints) < _MAX_ENDPOINTS:
            endpoints.append(endpoint)
        if doc.get("response_schema_state") != "parsed":
            note = CONTRACT_SENTENCES["body_not_checked"].format(method=method, endpoint=endpoint)
            if note not in notes:
                notes.append(note)
            continue
        if not fingerprint:
            fingerprint = _named_schema(doc.get("response_schema"))
        found: list[dict] = []
        if expected_fields and isinstance(expected_fields, dict):
            body = _parsed_body(doc)
            if body:
                found += [{**v, "method": method, "endpoint": endpoint} for v in _check_schema(body, expected_fields)]
        if check_drift:
            drift, build, drift_note, extra = await _drift_for(doc, project_id=project_id, test_run_id=test_run_id)
            found += drift
            added += extra
            if drift_note and drift_note not in notes:
                notes.append(drift_note)
            if build is not None:
                checked += 1
                if build not in builds:
                    builds.append(build)
        for violation in found:
            key = (method, endpoint, str(violation.get("field_path")), str(violation.get("violation_type")))
            if key not in keys_seen and len(violations) < _MAX_VIOLATIONS:
                keys_seen.add(key)
                violations.append(violation)

    sentences = [str(v["sentence"]) for v in violations if v.get("sentence")]
    if not sentences and checked:
        sentences = [CONTRACT_SENTENCES["no_drift"].format(checked=checked)]
    summary = " ".join(sentences + notes) or "Schema drift check skipped."
    return json.dumps({
        "endpoint": endpoints[0] if endpoints else "unknown",
        "endpoints": endpoints,
        "violations": violations,
        "has_violations": bool(violations),
        "drift_summary": summary[:_MAX_SUMMARY],
        "exchanges_checked": checked,
        "added_fields": added,
        "baseline_builds": builds[:_MAX_ENDPOINTS],
        "schema_fingerprint": fingerprint,
        "evidence_available": True,
    }, default=str)
