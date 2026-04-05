"""LangChain tool: fetch stack trace from MongoDB."""
from langchain_core.tools import tool

from app.db.mongo import Collections, get_mongo_db


@tool
async def fetch_allure_stacktrace(test_case_id: str) -> str:
    """
    Retrieve the full assertion failure, exception message, and stack trace
    for a specific test case from the Allure result stored in MongoDB.
    Use this as the FIRST step in any failure investigation.

    Args:
        test_case_id: The Allure UUID of the test case (from the result JSON).

    Returns:
        The stack trace and error message as a formatted string.
    """
    db = get_mongo_db()
    doc = await db[Collections.RAW_ALLURE_JSON].find_one({"test_case_id": test_case_id})

    if not doc:
        return f"No Allure result found for test_case_id: {test_case_id}"

    raw = doc.get("raw_result", {})
    status_details = raw.get("statusDetails", {})
    message = status_details.get("message", "No error message")
    trace = status_details.get("trace", "No stack trace available")

    steps = raw.get("steps", [])
    failed_steps = [
        f"  - [{s.get('status', '?')}] {s.get('name', 'Unknown step')}"
        for s in steps
        if s.get("status") in ("failed", "broken")
    ]
    steps_info = "\n".join(failed_steps) if failed_steps else "  (no failed steps recorded)"

    attachments = raw.get("attachments", [])
    att_info = f"{len(attachments)} attachment(s) available (screenshots, logs)" if attachments else "No attachments"

    return f"""=== Allure Stack Trace ===
Test: {raw.get('name', 'Unknown')}
Status: {raw.get('status', 'unknown')}
Duration: {raw.get('stop', 0) - raw.get('start', 0)}ms

Error Message:
{message}

Stack Trace:
{trace[:3000]}

Failed Steps:
{steps_info}

Attachments: {att_info}
"""
