"""LangChain tool: fetch captured REST API request/response payloads.

W23d AGT-14 P-4: the docs ``services/http_exchange_store.py`` writes (``v`` = 1) are
rendered as up to two exchanges — ``METHOD endpoint → status``, the allowlisted request
headers, and each BODY only inside W23c's data block (``untrusted_input.wrap_untrusted``),
so the ReAct triage prompt never sees a captured, attacker-controlled body raw. The
rendered text is bounded to ``_TOTAL_LIMIT`` by construction (no final cut can land on a
marker). There is ONE rendering: a doc of any shape goes through the data-block path (no
unwrapped branch for the pre-W23d shape — nothing in TestLookup ever wrote it), and only a
doc carrying the investigation's own owner fields is read.
"""
from typing import Any, Mapping

from langchain_core.tools import tool

from app.db.mongo import Collections, get_mongo_db
from app.services.evidence_sanitizer import sanitize_reference_text
from app.services.untrusted_input import wrap_untrusted
from app.tools.investigation_context import get_investigation_context

_ALLOWED_HEADERS = {"accept", "content-type", "user-agent", "x-request-id"}
_EXCHANGES_RENDERED = 2
_REQUEST_BODY_LIMIT = 600
_RESPONSE_BODY_LIMIT = 900
_ENDPOINT_LIMIT = 100
_HEADER_VALUE_LIMIT = 40
_TOTAL_LIMIT = 4000


def _side(doc: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = doc.get(name)
    return value if isinstance(value, Mapping) else {}


def _render_exchange(index: int, doc: Mapping[str, Any]) -> list[str]:
    request = _side(doc, "request")
    response = _side(doc, "response")
    method = str(doc.get("method") or "?")[:16]
    endpoint, _, _ = sanitize_reference_text(str(doc.get("endpoint") or "?"), limit=_ENDPOINT_LIMIT)
    status = doc.get("status")
    outcome = f"HTTP {int(status)}" if isinstance(status, int) else "no response"
    lines = [f"{index}. {method} {endpoint} → {outcome}"]
    headers = _side(request, "headers")
    allowed =[(str(k), v) for k, v in list(headers.items())[:50] if str(k).lower() in _ALLOWED_HEADERS]
    if allowed:
        lines.append("Request Headers:")
        for name, value in allowed[: len(_ALLOWED_HEADERS)]:
            safe, _, _ = sanitize_reference_text(str(value), limit=_HEADER_VALUE_LIMIT)
            lines.append(f"  {name[:16]}: {safe}")
    for label, side, limit in (("Request Body:", request, _REQUEST_BODY_LIMIT),
                               ("Response Body:", response, _RESPONSE_BODY_LIMIT)):
        body = side.get("body")
        if isinstance(body, str) and body:
            lines.append(label)
            lines.append(wrap_untrusted(body, limit=limit))
    return lines


async def _store_docs(db: Any, owner: dict[str, str]) -> tuple[list[dict], int]:
    coll = db[Collections.REST_API_PAYLOADS]
    docs = await coll.find(owner, {"_id": 0}).sort("seq", 1).limit(_EXCHANGES_RENDERED).to_list(_EXCHANGES_RENDERED)
    total = await coll.count_documents(owner)
    return list(docs), int(total)


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
    owner = {
        "test_case_id": context.test_case_id,
        "test_run_id": context.run_id,
        "project_id": context.project_id,
    }
    # Owner-scoped only: the store always writes the owner fields, so a doc found by the case id
    # alone would belong to another project / run (the pre-W23d case-id fallback is gone).
    doc = await db[Collections.REST_API_PAYLOADS].find_one(owner)
    if not doc:
        return f"No REST API payload captured for test_case_id: {test_case_id}. The test may not use HTTP request capture."

    docs, total = await _store_docs(db, owner)
    if not docs:
        docs, total = [doc], 1
    lines = ["=== REST API Payload ==="]
    for index, exchange in enumerate(docs[:_EXCHANGES_RENDERED], start=1):
        lines.extend(_render_exchange(index, exchange))
    more = total - min(len(docs), _EXCHANGES_RENDERED)
    if more > 0:
        lines.append(f"… and {more} more")
    # Bounded by construction (≤ _TOTAL_LIMIT): this final pass redacts and never cuts a block.
    rendered, _, _ = sanitize_reference_text("\n".join(lines), limit=_TOTAL_LIMIT)
    return rendered
