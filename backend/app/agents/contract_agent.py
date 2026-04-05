"""
API Contract Agent.
Validates REST API response payloads from a failure cluster against schema expectations.
Reports schema drift and type mismatches.
"""
import json

import structlog

from app.tools.validate_api_contract import validate_api_contract

logger = structlog.get_logger("agents.contract")


class ContractAgent:
    """
    Validates API contracts for a set of failed test case IDs.
    Returns consolidated violation report.
    """

    async def validate_cluster(self, test_case_ids: list[str]) -> dict:
        """
        Run contract validation for each test in the cluster.
        Returns aggregated violations and a summary.
        """
        all_violations: list[dict] = []
        endpoints_checked: list[str] = []

        for tc_id in test_case_ids:
            try:
                result_json = await validate_api_contract.ainvoke({
                    "params_json": json.dumps({
                        "test_case_id": tc_id,
                        "check_drift": True,
                    })
                })
                result = json.loads(result_json)

                if result.get("has_violations"):
                    endpoint = result.get("endpoint", "unknown")
                    endpoints_checked.append(endpoint)
                    for v in result.get("violations", []):
                        v["test_case_id"] = tc_id
                        v["endpoint"] = endpoint
                        all_violations.append(v)
            except Exception as exc:
                logger.debug("Contract validation failed for %s: %s", tc_id, exc)

        critical = [v for v in all_violations if v.get("severity") == "critical"]
        drift = [v for v in all_violations if v.get("violation_type") == "schema_drift"]

        if critical:
            summary = (
                f"CRITICAL CONTRACT VIOLATIONS: {len(critical)} required fields missing across "
                f"{len(set(v['endpoint'] for v in critical))} endpoints. "
                "This indicates PRODUCT_BUG or breaking API change."
            )
        elif drift:
            summary = (
                f"SCHEMA DRIFT DETECTED: {len(drift)} fields disappeared from responses. "
                "Possible breaking change or serialization regression."
            )
        elif all_violations:
            summary = f"{len(all_violations)} contract warnings (type mismatches) found."
        else:
            summary = "No API contract violations detected. Responses match historical schema."

        return {
            "violations": all_violations,
            "violation_count": len(all_violations),
            "critical_count": len(critical),
            "drift_count": len(drift),
            "endpoints_checked": list(set(endpoints_checked)),
            "summary": summary,
            "suggests_product_bug": bool(critical),
        }
