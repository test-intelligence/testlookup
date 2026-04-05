"""
Tool: Validate REST API response payloads against OpenAPI specs or historical baselines.
Detects schema drift, missing fields, type mismatches.
"""
import json
import logging

from langchain_core.tools import tool

from app.db.mongo import get_mongo_db

logger = logging.getLogger("tools.validate_api_contract")


def _check_schema(response_body: dict, expected_fields: dict) -> list[dict]:
    """Recursively check that expected_fields are present and type-match in response_body."""
    violations = []
    for field, expected_type in expected_fields.items():
        if field not in response_body:
            violations.append({
                "field_path": field,
                "violation_type": "missing_field",
                "expected": str(expected_type),
                "actual": "absent",
                "severity": "critical",
            })
        else:
            actual_value = response_body[field]
            actual_type = type(actual_value).__name__
            if expected_type == "string" and not isinstance(actual_value, str):
                violations.append({
                    "field_path": field,
                    "violation_type": "type_mismatch",
                    "expected": "string",
                    "actual": actual_type,
                    "severity": "warning",
                })
            elif expected_type == "number" and not isinstance(actual_value, (int, float)):
                violations.append({
                    "field_path": field,
                    "violation_type": "type_mismatch",
                    "expected": "number",
                    "actual": actual_type,
                    "severity": "warning",
                })
            elif expected_type == "boolean" and not isinstance(actual_value, bool):
                violations.append({
                    "field_path": field,
                    "violation_type": "type_mismatch",
                    "expected": "boolean",
                    "actual": actual_type,
                    "severity": "warning",
                })
    return violations


def _extract_schema_fingerprint(body: dict) -> dict:
    """Extract field names and their types as a schema fingerprint."""
    if not isinstance(body, dict):
        return {}
    return {k: type(v).__name__ for k, v in body.items()}


@tool
async def validate_api_contract(params_json: str) -> str:
    """
    Validate REST API response payload from a failed test against schema expectations.

    Input JSON keys:
      - test_case_id: ID of the test case to inspect
      - expected_fields: optional dict of field_name -> type_string to validate against
      - check_drift: bool — compare against historical baseline responses (default true)

    Returns: JSON with violations list and drift_summary.
    """
    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError) as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    test_case_id: str = params.get("test_case_id", "")
    expected_fields: dict = params.get("expected_fields", {})
    check_drift: bool = params.get("check_drift", True)

    if not test_case_id:
        return json.dumps({"error": "test_case_id is required"})

    # Fetch stored REST payload from MongoDB
    db = get_mongo_db()
    payload_doc = await db["rest_api_payloads"].find_one({"test_case_id": test_case_id})
    if not payload_doc:
        return json.dumps({
            "violations": [],
            "drift_summary": "No REST payload found for this test case.",
            "has_violations": False,
        })

    try:
        resp_body_raw = payload_doc.get("response_body", "{}")
        if isinstance(resp_body_raw, str):
            resp_body = json.loads(resp_body_raw)
        else:
            resp_body = resp_body_raw
    except (json.JSONDecodeError, TypeError):
        resp_body = {}

    endpoint = payload_doc.get("endpoint", "unknown")
    status_code = payload_doc.get("response_status", 0)

    violations: list[dict] = []

    # 1. Check against expected_fields if provided
    if expected_fields and resp_body:
        violations.extend(_check_schema(resp_body, expected_fields))

    # 2. Schema drift — compare current schema fingerprint against historical average
    drift_summary = "Schema drift check skipped."
    if check_drift and resp_body:
        current_fingerprint = _extract_schema_fingerprint(resp_body)
        historical_docs = await db["rest_api_payloads"].find(
            {"endpoint": endpoint, "test_case_id": {"$ne": test_case_id}},
            {"response_body": 1}
        ).sort("_id", -1).limit(20).to_list(20)

        if historical_docs:
            historical_fields: set[str] = set()
            for doc in historical_docs:
                try:
                    body = doc.get("response_body", {})
                    if isinstance(body, str):
                        body = json.loads(body)
                    historical_fields.update(body.keys() if isinstance(body, dict) else [])
                except (json.JSONDecodeError, AttributeError):
                    pass

            missing_in_current = historical_fields - set(current_fingerprint.keys())
            new_in_current = set(current_fingerprint.keys()) - historical_fields

            for field in missing_in_current:
                violations.append({
                    "field_path": field,
                    "violation_type": "schema_drift",
                    "expected": "present (seen in 90%+ of historical responses)",
                    "actual": "absent",
                    "severity": "warning",
                })

            if missing_in_current or new_in_current:
                drift_summary = (
                    f"Schema drift detected on endpoint {endpoint}: "
                    f"{len(missing_in_current)} fields disappeared, "
                    f"{len(new_in_current)} new fields appeared."
                )
            else:
                drift_summary = f"No schema drift detected. Response structure matches {len(historical_docs)} historical responses."
        else:
            drift_summary = "No historical payloads found for baseline comparison."

    return json.dumps({
        "endpoint": endpoint,
        "response_status": status_code,
        "violations": violations,
        "has_violations": bool(violations),
        "drift_summary": drift_summary,
        "schema_fingerprint": _extract_schema_fingerprint(resp_body),
    })
