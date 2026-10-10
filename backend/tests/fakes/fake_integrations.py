"""A fake Jira Cloud and a fake Slack, as one ASGI app.

Owner request 2026-10-10: test the Jira and Slack integrations with mock
services, because the real applications are not available. The same app serves
two layers:

* **In the test suite** -- ``httpx.ASGITransport(app=create_app(state))``
  behind the real services, so their requests, payloads and error handling are
  exercised against the contract instead of a patched ``_jira_post``.
* **On a deployment** -- ``uvicorn tests.fakes.fake_integrations:app`` (or the
  file alone on ``PYTHONPATH``), over TLS, with TestLookup pointed at it.

It is a CONTRACT double, not a stub: it refuses what the real service refuses.
Jira: Basic auth, unknown project / issue type, a summary over 255 characters
or with a newline, a description that is not a valid Atlassian Document, a
label with a space, the removed ``/rest/api/3/search`` (410, Atlassian
CHANGE-2046). Slack: no text and no blocks, unknown block types, Block Kit
length limits, a revoked webhook (404 ``no_service``).

Self-contained (Starlette only), because a deployment mounts this one file.

Inspect and steer it under ``/_fake/``:

* ``GET /_fake/requests`` -- every request seen (method, path, query, body,
  whether it carried auth, the ``X-TestLookup-Delivery`` header)
* ``POST /_fake/reset`` -- forget issues, messages, requests and faults
* ``POST /_fake/faults`` -- ``{"method", "path", "status", "times", "after",
  "delay_s", "retry_after", "body"}``: answer the next matching requests with
  ``status``; ``after: true`` runs the real handler first (Jira created the
  issue, then the answer was lost)
* ``POST /_fake/jira/issues/{key}/status`` -- ``{"name", "category"}``
* ``POST /_fake/jira/projects/{key}/versions`` -- ``{"name", "description"}``
* ``POST /_fake/slack/revoke`` -- ``{"path"}``: that webhook answers 404
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

# ── Contract limits (the real services' documented values) ───────────────────

JIRA_SUMMARY_MAX = 255
JIRA_LABEL_MAX = 255
SLACK_MAX_BLOCKS = 50
SLACK_SECTION_TEXT_MAX = 3000
SLACK_HEADER_TEXT_MAX = 150
SLACK_FIELDS_MAX = 10
SLACK_FIELD_TEXT_MAX = 2000
SLACK_CONTEXT_ELEMENTS_MAX = 10
SLACK_BUTTON_TEXT_MAX = 75
SLACK_URL_MAX = 3000

_ADF_BLOCK = {
    "paragraph", "heading", "bulletList", "orderedList", "listItem", "codeBlock",
    "rule", "blockquote", "panel", "table", "tableRow", "tableHeader", "tableCell",
    "mediaSingle", "media", "expand",
}
_ADF_INLINE = {"text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status"}
_ADF_MARKS = {"strong", "em", "code", "link", "strike", "underline", "subsup", "textColor"}
_SLACK_BLOCKS = {"section", "header", "divider", "context", "actions", "image", "rich_text"}


@dataclass
class FakeState:
    """Everything the fake remembers. Thread-safe enough for one test or pod."""

    jira_email: str = "fake-bot@example.test"
    jira_token: str = "fake-jira-token"
    base_url: str = "https://fake-jira.example.test"
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    slack_messages: list[dict[str, Any]] = field(default_factory=list)
    revoked_webhooks: set[str] = field(default_factory=set)
    requests: list[dict[str, Any]] = field(default_factory=list)
    faults: list[dict[str, Any]] = field(default_factory=list)
    _next_id: int = 10000
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.projects:
            self.add_project("QA", "QA Board")
            self.add_project("OPS", "Operations")

    def add_project(self, key: str, name: str, issue_types: Optional[list[str]] = None) -> None:
        self.projects[key] = {
            "id": str(self._new_id()),
            "key": key,
            "name": name,
            "issue_types": issue_types or ["Bug", "Task", "Story"],
            "versions": [],
            "seq": 0,
        }

    def add_version(self, key: str, name: str, description: Optional[str] = None) -> dict[str, Any]:
        version_id = str(self._new_id())
        version = {
            "id": version_id,
            "name": name,
            "description": description,
            "self": f"{self.base_url}/rest/api/3/version/{version_id}",
            "released": False,
            "archived": False,
        }
        self.projects[key]["versions"].append(version)
        return version

    def reset(self) -> None:
        with self._lock:
            self.issues.clear()
            self.slack_messages.clear()
            self.revoked_webhooks.clear()
            self.requests.clear()
            self.faults.clear()
            for project in self.projects.values():
                project["seq"] = 0
                project["versions"] = []

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def requests_to(self, method: str, path_prefix: str) -> list[dict[str, Any]]:
        return [
            r for r in self.requests
            if r["method"] == method and r["path"].startswith(path_prefix)
        ]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _jira_error(status: int, messages: Optional[list[str]] = None,
                errors: Optional[dict[str, str]] = None) -> JSONResponse:
    return JSONResponse({"errorMessages": messages or [], "errors": errors or {}}, status_code=status)


def _authorized(request: Request, state: FakeState) -> bool:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[6:]).decode()
    except Exception:
        return False
    return raw == f"{state.jira_email}:{state.jira_token}"


def adf_problems(node: Any, path: str = "doc") -> list[str]:
    """Why ``node`` is not a valid Atlassian Document (empty list = valid)."""
    if not isinstance(node, dict):
        return [f"{path}: not an object"]
    if path == "doc":
        problems = []
        if node.get("type") != "doc" or node.get("version") != 1:
            problems.append("doc: type must be 'doc' and version 1")
        content = node.get("content")
        if not isinstance(content, list):
            return problems + ["doc: content must be a list"]
        for i, child in enumerate(content):
            if isinstance(child, dict) and child.get("type") in _ADF_INLINE:
                problems.append(f"doc.content[{i}]: inline node '{child.get('type')}' at top level")
            else:
                problems += adf_problems(child, f"doc.content[{i}]")
        return problems
    kind = node.get("type")
    if kind not in _ADF_BLOCK and kind not in _ADF_INLINE:
        return [f"{path}: unknown node type {kind!r}"]
    problems = []
    if kind == "text":
        text = node.get("text")
        if not isinstance(text, str) or not text:
            problems.append(f"{path}: a text node needs non-empty text")
        for mark in node.get("marks") or []:
            mtype = mark.get("type") if isinstance(mark, dict) else None
            if mtype not in _ADF_MARKS:
                problems.append(f"{path}: unknown mark {mtype!r}")
            elif mtype == "link" and not isinstance((mark.get("attrs") or {}).get("href"), str):
                problems.append(f"{path}: a link mark needs attrs.href")
        return problems
    content = node.get("content")
    if content is not None:
        if not isinstance(content, list):
            return [f"{path}: content must be a list"]
        for i, child in enumerate(content):
            if kind in ("paragraph", "heading") and isinstance(child, dict) and child.get("type") in _ADF_BLOCK:
                problems.append(f"{path}.content[{i}]: block node inside {kind}")
            problems += adf_problems(child, f"{path}.content[{i}]")
    if kind == "heading" and not 1 <= int((node.get("attrs") or {}).get("level") or 0) <= 6:
        problems.append(f"{path}: heading level must be 1-6")
    return problems


def slack_problems(payload: Any) -> list[str]:
    """Why Slack would refuse ``payload`` (empty list = it would post)."""
    if not isinstance(payload, dict):
        return ["invalid_payload"]
    blocks = payload.get("blocks")
    attachments = payload.get("attachments")
    if not payload.get("text") and not blocks and not attachments:
        return ["no_text"]
    problems: list[str] = []
    groups: list[tuple[str, Any]] = [("blocks", blocks)] if blocks is not None else []
    for i, att in enumerate(attachments or []):
        if not isinstance(att, dict):
            problems.append(f"attachments[{i}]: not an object")
            continue
        if att.get("blocks") is not None:
            groups.append((f"attachments[{i}].blocks", att.get("blocks")))
        elif not (att.get("text") or att.get("fallback") or att.get("fields")):
            problems.append(f"attachments[{i}]: empty attachment")
    for where, group in groups:
        if not isinstance(group, list):
            problems.append(f"{where}: must be a list")
            continue
        if len(group) > SLACK_MAX_BLOCKS:
            problems.append(f"{where}: {len(group)} blocks, the limit is {SLACK_MAX_BLOCKS}")
        for j, block in enumerate(group):
            problems += _block_problems(block, f"{where}[{j}]")
    return problems


def _text_problems(obj: Any, where: str, limit: int, *, plain_only: bool = False) -> list[str]:
    if not isinstance(obj, dict) or obj.get("type") not in ("mrkdwn", "plain_text"):
        return [f"{where}: a text object needs type mrkdwn or plain_text"]
    if plain_only and obj.get("type") != "plain_text":
        return [f"{where}: must be plain_text"]
    text = obj.get("text")
    if not isinstance(text, str) or not text:
        return [f"{where}: text must be a non-empty string"]
    if len(text) > limit:
        return [f"{where}: {len(text)} characters, the limit is {limit}"]
    return []


def _block_problems(block: Any, where: str) -> list[str]:
    if not isinstance(block, dict):
        return [f"{where}: not an object"]
    kind = block.get("type")
    if kind not in _SLACK_BLOCKS:
        return [f"{where}: unknown block type {kind!r}"]
    if len(str(block.get("block_id") or "")) > 255:
        return [f"{where}: block_id over 255 characters"]
    if kind == "header":
        return _text_problems(block.get("text"), f"{where}.text", SLACK_HEADER_TEXT_MAX, plain_only=True)
    if kind == "section":
        problems: list[str] = []
        if block.get("text") is None and not block.get("fields"):
            return [f"{where}: a section needs text or fields"]
        if block.get("text") is not None:
            problems += _text_problems(block.get("text"), f"{where}.text", SLACK_SECTION_TEXT_MAX)
        fields = block.get("fields") or []
        if len(fields) > SLACK_FIELDS_MAX:
            problems.append(f"{where}.fields: {len(fields)} fields, the limit is {SLACK_FIELDS_MAX}")
        for k, item in enumerate(fields):
            problems += _text_problems(item, f"{where}.fields[{k}]", SLACK_FIELD_TEXT_MAX)
        return problems
    if kind == "context":
        elements = block.get("elements") or []
        if not elements or len(elements) > SLACK_CONTEXT_ELEMENTS_MAX:
            return [f"{where}: 1-{SLACK_CONTEXT_ELEMENTS_MAX} elements required"]
        problems = []
        for k, item in enumerate(elements):
            if isinstance(item, dict) and item.get("type") == "image":
                continue
            problems += _text_problems(item, f"{where}.elements[{k}]", SLACK_SECTION_TEXT_MAX)
        return problems
    if kind == "actions":
        elements = block.get("elements") or []
        problems = [] if 1 <= len(elements) <= 25 else [f"{where}: 1-25 elements required"]
        for k, item in enumerate(elements):
            if not isinstance(item, dict) or item.get("type") != "button":
                continue
            problems += _text_problems(item.get("text"), f"{where}.elements[{k}].text",
                                       SLACK_BUTTON_TEXT_MAX, plain_only=True)
            if item.get("url") is not None and len(str(item["url"])) > SLACK_URL_MAX:
                problems.append(f"{where}.elements[{k}].url: over {SLACK_URL_MAX} characters")
        return problems
    return []


# ── Fault injection ─────────────────────────────────────────────────────────


def _take_fault(state: FakeState, method: str, path: str) -> Optional[dict[str, Any]]:
    with state._lock:
        for fault in state.faults:
            if fault.get("method", method).upper() != method:
                continue
            if not re.fullmatch(str(fault.get("path", ".*")), path):
                continue
            if int(fault.get("times", 1)) <= 0:
                continue
            fault["times"] = int(fault.get("times", 1)) - 1
            return dict(fault)
    return None


def _fault_response(fault: dict[str, Any]) -> Response:
    headers = {}
    if fault.get("retry_after") is not None:
        headers["Retry-After"] = str(fault["retry_after"])
    body = fault.get("body")
    if isinstance(body, (dict, list)):
        return JSONResponse(body, status_code=int(fault["status"]), headers=headers)
    return PlainTextResponse(str(body or ""), status_code=int(fault["status"]), headers=headers)


# ── Jira handlers ───────────────────────────────────────────────────────────


async def _server_info(request: Request, state: FakeState) -> Response:
    return JSONResponse({
        "baseUrl": state.base_url, "version": "1001.0.0-SNAPSHOT",
        "deploymentType": "Cloud", "serverTitle": "Fake Jira",
    })


async def _myself(request: Request, state: FakeState) -> Response:
    return JSONResponse({
        "accountId": "fake-account-1", "emailAddress": state.jira_email,
        "displayName": "Fake Bot", "active": True,
    })


async def _project_search(request: Request, state: FakeState) -> Response:
    values = [{"id": p["id"], "key": p["key"], "name": p["name"]} for p in state.projects.values()]
    return JSONResponse({"values": values, "total": len(values), "isLast": True})


async def _issue_types(request: Request, state: FakeState) -> Response:
    names: list[str] = []
    for project in state.projects.values():
        names += [n for n in project["issue_types"] if n not in names]
    types = [{"id": str(i + 1), "name": n, "subtask": False} for i, n in enumerate(names)]
    types.append({"id": "99", "name": "Sub-task", "subtask": True})
    return JSONResponse(types)


async def _project_versions(request: Request, state: FakeState) -> Response:
    key = request.path_params["key"]
    project = state.projects.get(key)
    if project is None:
        return _jira_error(404, [f"No project could be found with key '{key}'."])
    return JSONResponse(project["versions"])


async def _create_issue(request: Request, state: FakeState) -> Response:
    try:
        body = await request.json()
    except Exception:
        return _jira_error(400, ["Unexpected character in the request body."])
    fields = (body or {}).get("fields")
    if not isinstance(fields, dict):
        return _jira_error(400, ["Field 'fields' is required."])
    errors: dict[str, str] = {}
    project = state.projects.get(((fields.get("project") or {}).get("key")) or "")
    if project is None:
        errors["project"] = "Specify a valid project ID or key"
    issue_type = (fields.get("issuetype") or {}).get("name")
    if project is not None and issue_type not in project["issue_types"]:
        errors["issuetype"] = "Specify a valid issue type"
    summary = fields.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors["summary"] = "You must specify a summary of the issue."
    elif len(summary) > JIRA_SUMMARY_MAX:
        errors["summary"] = f"Summary must be less than {JIRA_SUMMARY_MAX} characters."
    elif "\n" in summary or "\r" in summary:
        errors["summary"] = "The summary is invalid because it contains newline characters."
    if "description" in fields and fields["description"] is not None:
        problems = adf_problems(fields["description"])
        if problems:
            errors["description"] = "Operation value must be an Atlassian Document (see the Atlassian Document Format). " + "; ".join(problems[:3])
    labels = fields.get("labels") or []
    for label in labels:
        if not isinstance(label, str) or " " in label or not label or len(label) > JIRA_LABEL_MAX:
            errors["labels"] = f"The label '{label}' contains spaces or is empty or too long, which is invalid."
            break
    if errors:
        return _jira_error(400, errors=errors)
    with state._lock:
        project["seq"] += 1
        key = f"{project['key']}-{project['seq']}"
        issue_id = str(state._new_id())
        state.issues[key] = {
            "id": issue_id,
            "key": key,
            "fields": {
                "summary": summary,
                "description": fields.get("description"),
                "labels": list(labels),
                "issuetype": {"name": issue_type},
                "project": {"key": project["key"]},
                "assignee": fields.get("assignee"),
                "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                "created": _now(),
            },
            "comments": [],
        }
    return JSONResponse(
        {"id": issue_id, "key": key, "self": f"{state.base_url}/rest/api/3/issue/{issue_id}"},
        status_code=201,
    )


async def _get_issue(request: Request, state: FakeState) -> Response:
    issue = state.issues.get(request.path_params["key"])
    if issue is None:
        return _jira_error(404, ["Issue does not exist or you do not have permission to see it."])
    wanted = request.query_params.get("fields")
    fields = issue["fields"]
    if wanted:
        names = {n.strip() for n in wanted.split(",")}
        fields = {k: v for k, v in fields.items() if k in names}
    return JSONResponse({"id": issue["id"], "key": issue["key"], "fields": fields})


async def _add_comment(request: Request, state: FakeState) -> Response:
    issue = state.issues.get(request.path_params["key"])
    if issue is None:
        return _jira_error(404, ["Issue does not exist or you do not have permission to see it."])
    body = (await request.json() or {}).get("body")
    problems = adf_problems(body)
    if problems:
        return _jira_error(400, errors={"comment": "Comment body is not a valid Atlassian Document: " + "; ".join(problems[:3])})
    comment = {"id": str(state._new_id()), "body": body, "created": _now()}
    issue["comments"].append(comment)
    return JSONResponse(comment, status_code=201)


_JQL_LABEL = re.compile(r'^\s*labels\s*=\s*"([^"]+)"\s*(ORDER BY created (ASC|DESC))?\s*$', re.I)
_JQL_PROJECT = re.compile(r'^\s*project\s*=\s*"?([A-Z][A-Z0-9_]*)"?\s*$', re.I)
_JQL_PARENT = re.compile(r'^\s*"Epic Link"\s*=\s*([A-Z0-9_-]+)\s+OR\s+parent\s*=\s*([A-Z0-9_-]+)\s*$', re.I)


async def _search_jql(request: Request, state: FakeState) -> Response:
    jql = request.query_params.get("jql", "")
    limit = int(request.query_params.get("maxResults", 50))
    issues = list(state.issues.values())
    if (m := _JQL_LABEL.match(jql)):
        found = [i for i in issues if m.group(1) in i["fields"]["labels"]]
        found.sort(key=lambda i: i["fields"]["created"], reverse=(m.group(3) or "").upper() == "DESC")
    elif (m := _JQL_PROJECT.match(jql)):
        found = [i for i in issues if i["fields"]["project"]["key"] == m.group(1).upper()]
    elif (m := _JQL_PARENT.match(jql)):
        found = [i for i in issues if (i["fields"].get("parent") or {}).get("key") in (m.group(1), m.group(2))]
    else:
        return _jira_error(400, [f"The fake understands only label, project and epic-children JQL, not: {jql}"])
    wanted = {n.strip() for n in request.query_params.get("fields", "").split(",") if n.strip()}
    out = [
        {"id": i["id"], "key": i["key"],
         "fields": {k: v for k, v in i["fields"].items() if not wanted or k in wanted}}
        for i in found[:limit]
    ]
    return JSONResponse({"issues": out, "isLast": True})


async def _search_removed(request: Request, state: FakeState) -> Response:
    # Atlassian CHANGE-2046: the old search endpoints answer 410 Gone.
    return _jira_error(410, [
        "The requested API has been removed. Please migrate to the /rest/api/3/search/jql API. "
        "A full migration guideline is available at https://developer.atlassian.com/changelog/#CHANGE-2046"
    ])


_JIRA_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/rest/api/3/serverInfo", _server_info),
    ("GET", "/rest/api/3/myself", _myself),
    ("GET", "/rest/api/3/project/search", _project_search),
    ("GET", "/rest/api/3/issuetype", _issue_types),
    ("GET", "/rest/api/3/project/{key}/versions", _project_versions),
    ("POST", "/rest/api/3/issue", _create_issue),
    ("GET", "/rest/api/3/issue/{key}", _get_issue),
    ("POST", "/rest/api/3/issue/{key}/comment", _add_comment),
    ("GET", "/rest/api/3/search/jql", _search_jql),
    ("GET", "/rest/api/3/search", _search_removed),
    ("POST", "/rest/api/3/search", _search_removed),
    ("GET", "/rest/api/2/search", _search_removed),
]


# ── Slack handler ───────────────────────────────────────────────────────────


async def _slack_webhook(request: Request, state: FakeState) -> Response:
    path = request.url.path
    if path in state.revoked_webhooks:
        return PlainTextResponse("no_service", status_code=404)
    try:
        payload = await request.json()
    except Exception:
        return PlainTextResponse("invalid_payload", status_code=400)
    problems = slack_problems(payload)
    if problems:
        code = problems[0] if problems[0] in ("no_text", "invalid_payload") else "invalid_blocks"
        return PlainTextResponse(code, status_code=400, headers={"X-Fake-Problems": " | ".join(problems)[:900]})
    state.slack_messages.append({
        "path": path,
        "payload": payload,
        "delivery_id": request.headers.get("x-testlookup-delivery"),
        "received_at": _now(),
    })
    return PlainTextResponse("ok")


# ── Control endpoints ───────────────────────────────────────────────────────


async def _control(request: Request, state: FakeState) -> Response:
    path = request.url.path
    if path == "/_fake/requests":
        return JSONResponse(state.requests)
    if path == "/_fake/reset":
        state.reset()
        return JSONResponse({"reset": True})
    if path == "/_fake/faults":
        state.faults.append(await request.json())
        return JSONResponse({"faults": len(state.faults)})
    if path == "/_fake/slack/messages":
        return JSONResponse(state.slack_messages)
    if path == "/_fake/slack/revoke":
        state.revoked_webhooks.add((await request.json())["path"])
        return JSONResponse({"revoked": sorted(state.revoked_webhooks)})
    if path == "/_fake/jira/issues":
        return JSONResponse(list(state.issues.values()))
    m = re.fullmatch(r"/_fake/jira/issues/([^/]+)/status", path)
    if m and m.group(1) in state.issues:
        body = await request.json()
        state.issues[m.group(1)]["fields"]["status"] = {
            "name": body["name"], "statusCategory": {"key": body.get("category", "indeterminate")},
        }
        return JSONResponse(state.issues[m.group(1)]["fields"]["status"])
    m = re.fullmatch(r"/_fake/jira/projects/([^/]+)/versions", path)
    if m and m.group(1) in state.projects:
        body = await request.json()
        return JSONResponse(state.add_version(m.group(1), body["name"], body.get("description")))
    return JSONResponse({"error": "unknown control path"}, status_code=404)


# ── App ─────────────────────────────────────────────────────────────────────


def create_app(state: Optional[FakeState] = None) -> Starlette:
    state = state or FakeState()

    async def dispatch(request: Request) -> Response:
        path = request.url.path
        method = request.method.upper()
        if path.startswith("/_fake/"):
            return await _control(request, state)
        raw = await request.body()
        try:
            import json as _json
            body: Any = _json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode(errors="replace")[:2000]
        state.requests.append({
            "method": method,
            "path": path,
            "query": dict(request.query_params),
            "body": body,
            "authorized": _authorized(request, state),
            "delivery_id": request.headers.get("x-testlookup-delivery"),
        })
        fault = _take_fault(state, method, path)
        if fault and fault.get("delay_s"):
            await asyncio.sleep(float(fault["delay_s"]))
        if fault and not fault.get("after"):
            return _fault_response(fault)

        if path.startswith("/services/"):
            response = await _slack_webhook(request, state)
        elif path.startswith("/rest/api/"):
            if not _authorized(request, state):
                response = _jira_error(401, ["Client must be authenticated to access this resource."])
            else:
                response = None
                for route_method, template, handler in _JIRA_ROUTES:
                    if route_method != method:
                        continue
                    pattern = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template) + "$"
                    if (m := re.match(pattern, path)):
                        request.scope["path_params"] = m.groupdict()
                        response = await handler(request, state)
                        break
                if response is None:
                    response = _jira_error(404, [f"No fake route for {method} {path}"])
        else:
            response = PlainTextResponse("not found", status_code=404)

        if fault and fault.get("after"):
            return _fault_response(fault)
        return response

    app = Starlette(routes=[Route("/{path:path}", dispatch, methods=["GET", "POST", "PUT", "DELETE"])])
    app.state.fake = state
    return app


def _state_from_env() -> FakeState:
    state = FakeState(
        jira_email=os.environ.get("FAKE_JIRA_EMAIL", "fake-bot@example.test"),
        jira_token=os.environ.get("FAKE_JIRA_TOKEN", "fake-jira-token"),
        base_url=os.environ.get("FAKE_BASE_URL", "https://fake-jira.example.test"),
    )
    return state


app = create_app(_state_from_env())
