"""LangChain tool: fetch captured REST API request/response payloads."""
from langchain_core.tools import tool

from app.db.mongo import Collections, get_mongo_db


@tool
async def fetch_rest_api_payload(test_case_id: str) -> str:
    """
    Retrieve the captured HTTP request and response payload for a REST API test.
    Use this for tests using REST Assured or HttpClient to understand what API
    call was made and what the server actually returned.

    Args:
        test_case_id: The test case ID to look up.

    Returns:
        The HTTP request/response details as a formatted string.
    """
    db = get_mongo_db()
    doc = await db[Collections.REST_API_PAYLOADS].find_one({"test_case_id": test_case_id})

    if not doc:
        return f"No REST API payload captured for test_case_id: {test_case_id}. The test may not use HTTP request capture."

    req = doc.get("request", {})
    resp = doc.get("response", {})

    lines = ["=== REST API Payload ==="]
    lines.append(f"REQUEST: {req.get('method', '?')} {req.get('url', '?')}")

    req_headers = req.get("headers", {})
    if req_headers:
        lines.append("Request Headers:")
        for k, v in list(req_headers.items())[:5]:
            lines.append(f"  {k}: {v}")

    req_body = req.get("body")
    if req_body:
        body_str = str(req_body)[:500]
        lines.append(f"Request Body:\n{body_str}")

    lines.append(f"\nRESPONSE: HTTP {resp.get('status_code', '?')}")

    resp_body = resp.get("body")
    if resp_body:
        body_str = str(resp_body)[:1000]
        lines.append(f"Response Body:\n{body_str}")

    return "\n".join(lines)
