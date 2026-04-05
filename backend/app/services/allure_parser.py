"""Allure JSON result file parser."""
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
