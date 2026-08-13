"""Bounded API contract specialist for the deep test-intelligence workflow."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from app.services.evidence_sanitizer import sanitize_reference_text
from app.tools.validate_api_contract import validate_api_contract

logger = structlog.get_logger("agents.contract")

_MAX_TEST_CASES = 200
_MAX_VIOLATIONS = 200
_MAX_ENDPOINTS = 200


def _checksum(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContractAgent:
    """Validate REST response contracts using server-scoped evidence only."""

    async def validate_cluster(
        self,
        test_case_ids: list[str],
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, honest contract result.

        Missing ownership context is intentionally treated as
        ``not_enough_evidence``. The validator never falls back to a global
        ``test_case_id`` lookup, because that would permit cross-tenant or
        cross-run payloads to become trusted specialist evidence.
        """
        ids = list(dict.fromkeys(str(item) for item in (test_case_ids or [])))[:_MAX_TEST_CASES]
        all_violations: list[dict[str, Any]] = []
        endpoints_checked: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        evidence_available = False
        failures = 0

        if ids and not (project_id and run_id and pipeline_run_id):
            return {
                "status": "not_enough_evidence",
                "violations": [],
                "violation_count": 0,
                "critical_count": 0,
                "drift_count": 0,
                "endpoints_checked": [],
                "evidence_refs": [],
                "summary": "Contract evidence requires project, run, and pipeline scope.",
                "suggests_product_bug": False,
            }

        for tc_id in ids:
            try:
                result_json = await validate_api_contract.ainvoke({
                    "params_json": json.dumps({
                        "test_case_id": tc_id,
                        "project_id": project_id,
                        "test_run_id": run_id,
                        "pipeline_run_id": pipeline_run_id,
                        "check_drift": True,
                    })
                })
                result = json.loads(result_json)
                if result.get("evidence_available"):
                    evidence_available = True
                endpoint = str(result.get("endpoint") or "unknown")[:500]
                if endpoint != "unknown" and endpoint not in endpoints_checked:
                    endpoints_checked.append(endpoint)
                raw_violations = result.get("violations") or []
                if not isinstance(raw_violations, list):
                    raw_violations = []
                for raw in raw_violations:
                    if not isinstance(raw, dict) or len(all_violations) >= _MAX_VIOLATIONS:
                        break
                    violation = {
                        "test_case_id": tc_id,
                        "endpoint": endpoint,
                        "field_path": sanitize_reference_text(str(raw.get("field_path") or ""), limit=240)[0],
                        "violation_type": sanitize_reference_text(str(raw.get("violation_type") or "unknown"), limit=80)[0],
                        "expected": sanitize_reference_text(str(raw.get("expected") or ""), limit=160)[0],
                        "actual": sanitize_reference_text(str(raw.get("actual") or ""), limit=160)[0],
                        "severity": str(raw.get("severity") or "warning")[:20],
                    }
                    all_violations.append(violation)
                    evidence_refs.append({
                        "source": "validate_api_contract",
                        "kind": "contract_violation",
                        "test_case_id": tc_id,
                        "endpoint": endpoint,
                        "checksum_sha256": _checksum(violation),
                    })
            except Exception as exc:  # fail closed per test, preserve other members
                failures += 1
                logger.warning(
                    "contract_validation_failed",
                    test_case_id=tc_id,
                    error_type=type(exc).__name__,
                )

        critical = [v for v in all_violations if v.get("severity") == "critical"]
        drift = [v for v in all_violations if v.get("violation_type") == "schema_drift"]
        if failures:
            status = "failed"
            summary = (
                "Contract validation was incomplete for one or more scoped test cases; "
                "the report must not treat the partial result as authoritative."
            )
        elif not evidence_available:
            status = "not_enough_evidence"
            summary = "No tenant/run-scoped REST contract evidence was available for these failures."
        else:
            status = "complete"
            if critical:
                summary = (
                    f"CRITICAL CONTRACT VIOLATIONS: {len(critical)} required fields missing across "
                    f"{len({v.get('endpoint') for v in critical})} endpoints."
                )
            elif drift:
                summary = f"SCHEMA DRIFT DETECTED: {len(drift)} fields changed from historical responses."
            elif all_violations:
                summary = f"{len(all_violations)} contract warnings found."
            else:
                summary = "No API contract violations detected. Responses match the scoped historical schema."

        return {
            "status": status,
            "violations": all_violations[:_MAX_VIOLATIONS],
            "violation_count": len(all_violations),
            "critical_count": len(critical),
            "drift_count": len(drift),
            "endpoints_checked": endpoints_checked[:_MAX_ENDPOINTS],
            "evidence_refs": evidence_refs[:_MAX_VIOLATIONS],
            "summary": summary[:2000],
            "suggests_product_bug": bool(critical),
        }