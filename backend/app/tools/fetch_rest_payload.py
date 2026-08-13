"""LangChain tool: fetch captured REST API request/response payloads."""
from langchain_core.tools import tool

from app.db.mongo import Collections, get_mongo_db
from app.services.evidence_sanitizer import sanitize_reference_text
from app.tools.investigation_context import get_investigation_context


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
    context = get_investigation_context()
    if context is None:
        return "REST payload lookup unavailable: no authorized investigation context."
    if str(test_case_id) != context.test_case_id:
        return "REST payload lookup denied: requested test is outside the investigation scope."
    db = get_mongo_db()
    doc = await db[Collections.REST_API_PAYLOADS].find_one({
        "test_case_id": context.test_case_id,
        "test_run_id": context.run_id,
        "project_id": context.project_id,
    })
    if not doc:
        # Compatibility for legacy payloads written before owner fields were
        # added. The globally unique test ID is server-bound by the authorized
        # PostgreSQL context; model input cannot select this fallback key.
        doc = await db[Collections.REST_API_PAYLOADS].find_one({
            "test_case_id": context.test_case_id,
        })

    if not doc:
        return f"No REST API payload captured for test_case_id: {test_case_id}. The test may not use HTTP request capture."

    req = doc.get("request", {})
    resp = doc.get("response", {})

    lines = ["=== REST API Payload ==="]
    lines.append(f"REQUEST: {req.get('method', '?')} {req.get('url', '?')}")

    req_headers = req.get("headers", {})
    if req_headers:
        lines.append("Request Headers:")
        allowed = {"accept", "content-type", "user-agent", "x-request-id"}
        for k, v in list(req_headers.items())[:20]:
            if str(k).lower() in allowed:
                safe, _, _ = sanitize_reference_text(str(v), limit=300)
                lines.append(f"  {k}: {safe}")

    req_body = req.get("body")
    if req_body:
        body_str, _, _ = sanitize_reference_text(str(req_body), limit=500)
        lines.append(f"Request Body:\n{body_str}")

    lines.append(f"\nRESPONSE: HTTP {resp.get('status_code', '?')}")

    resp_body = resp.get("body")
    if resp_body:
        body_str, _, _ = sanitize_reference_text(str(resp_body), limit=1000)
        lines.append(f"Response Body:\n{body_str}")

    rendered, _, _ = sanitize_reference_text("\n".join(lines), limit=4000)
    return rendered
