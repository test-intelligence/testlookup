"""Tool: validate tenant-scoped REST response contracts."""
from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from app.db.mongo import get_mongo_db

logger = logging.getLogger("tools.validate_api_contract")


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


def _extract_schema_fingerprint(body: dict) -> dict:
    return {str(k)[:240]: type(v).__name__ for k, v in body.items()} if isinstance(body, dict) else {}


def _missing_scope() -> str:
    return json.dumps({"error": "project_id, test_run_id, and pipeline_run_id are required", "evidence_available": False})


@tool
async def validate_api_contract(params_json: str) -> str:
    """Validate a REST response against an owned historical contract.

    The caller must supply server-bound project, test-run, pipeline, and test
    identifiers. Global test-case lookups are intentionally unsupported.
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
    pipeline_run_id = str(params.get("pipeline_run_id") or "")
    expected_fields = params.get("expected_fields") or {}
    check_drift = bool(params.get("check_drift", True))
    if not test_case_id:
        return json.dumps({"error": "test_case_id_required", "evidence_available": False})
    if not (project_id and test_run_id and pipeline_run_id):
        return _missing_scope()

    db = get_mongo_db()
    scope_filter = {
        "test_case_id": test_case_id,
        "project_id": project_id,
        "test_run_id": test_run_id,
        "pipeline_run_id": pipeline_run_id,
    }
    payload_doc = await db["rest_api_payloads"].find_one(scope_filter)
    if not payload_doc:
        return json.dumps({
            "violations": [],
            "drift_summary": "No tenant/run-scoped REST payload found for this test case.",
            "has_violations": False,
            "evidence_available": False,
        })

    try:
        resp_body_raw = payload_doc.get("response_body", {})
        resp_body = json.loads(resp_body_raw) if isinstance(resp_body_raw, str) else resp_body_raw
        if not isinstance(resp_body, dict):
            resp_body = {}
    except (json.JSONDecodeError, TypeError):
        resp_body = {}

    endpoint = str(payload_doc.get("endpoint") or "unknown")[:500]
    status_code = payload_doc.get("response_status", 0)
    violations = _check_schema(resp_body, expected_fields) if expected_fields and resp_body else []
    drift_summary = "Schema drift check skipped."
    if check_drift and resp_body:
        current_fingerprint = _extract_schema_fingerprint(resp_body)
        historical_docs = await db["rest_api_payloads"].find(
            {
                "endpoint": endpoint,
                "project_id": project_id,
                "test_run_id": test_run_id,
                "pipeline_run_id": pipeline_run_id,
                "test_case_id": {"$ne": test_case_id},
            },
            {"response_body": 1},
        ).sort("_id", -1).limit(20).to_list(20)
        if historical_docs:
            historical_fields: set[str] = set()
            for doc in historical_docs:
                try:
                    body = doc.get("response_body", {})
                    body = json.loads(body) if isinstance(body, str) else body
                    if isinstance(body, dict):
                        historical_fields.update(str(key)[:240] for key in body)
                except (json.JSONDecodeError, AttributeError, TypeError):
                    continue
            missing = historical_fields - set(current_fingerprint)
            added = set(current_fingerprint) - historical_fields
            for field in sorted(missing)[:200]:
                violations.append({"field_path": field, "violation_type": "schema_drift", "expected": "present in historical response", "actual": "absent", "severity": "warning"})
            drift_summary = (
                f"Schema drift detected on endpoint {endpoint}: {len(missing)} fields disappeared, {len(added)} new fields appeared."
                if missing or added else
                f"No schema drift detected. Response structure matches {len(historical_docs)} historical responses."
            )
        else:
            drift_summary = "No historical payloads found for baseline comparison."

    return json.dumps({
        "endpoint": endpoint,
        "response_status": status_code,
        "violations": violations[:200],
        "has_violations": bool(violations),
        "drift_summary": drift_summary[:1000],
        "schema_fingerprint": _extract_schema_fingerprint(resp_body),
        "evidence_available": True,
    })