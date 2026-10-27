"""Fake Jira Cloud, Slack, Splunk, OpenShift, GitHub / GHE, GitLab and a webhook receiver, as one ASGI app.

Owner request 2026-10-10: test the Jira and Slack integrations with mock
services, because the real applications are not available. W10 (INT-01, INT-03,
INT-04, INT-07, FAIL-09) added Splunk, OpenShift / Kubernetes, GitHub (and
GitHub Enterprise under ``/api/v3``), GitLab and an outbound-webhook receiver
to the SAME file (decision D-11: a deployment mounts this one file). The app
serves two layers:

* **In the test suite** -- ``httpx.ASGITransport(app=create_app(state))``
  behind the real services, so their requests, payloads and error handling are
  exercised against the contract instead of a patched ``_jira_post``.
* **On a deployment** -- ``uvicorn tests.fakes.fake_integrations:app --host
  <ip> --port <port>`` (or the file alone on ``PYTHONPATH``). Plain HTTP serves
  Splunk, OpenShift, GitHub, GitLab and the receiver; the legacy Jira client
  builds ``https://{domain}``, so Jira needs ``--ssl-keyfile/--ssl-certfile``
  and ``HTTP_CA_BUNDLE``. TestLookup refuses a loopback integration host by
  design, so bind an RFC1918 address, not 127.0.0.1.

It is a CONTRACT double, not a stub: each service refuses what the real one
refuses.

* Jira: Basic auth, unknown project / issue type, a summary over 255
  characters or with a newline, a description that is not a valid Atlassian
  Document, a label with a space, the removed ``/rest/api/3/search`` (410,
  Atlassian CHANGE-2046).
* Slack: no text and no blocks, unknown block types, Block Kit length limits,
  a revoked webhook (404 ``no_service``).
* Splunk (``Authorization: Bearer <splunk_token>``): ``GET
  /services/server/info``; ``POST /services/search/jobs`` (form; ``exec_mode=
  oneshot`` only) answers ``{"results": [...]}``; ``POST
  /services/search/jobs/export`` answers NDJSON ``{"result": {...}}`` lines, or
  one ``{"result": {"count": "<n>"}}`` for a search ending ``| stats count``.
  An event matches when its index is the search's ``index=``, its service is
  one of the ``service="..."`` terms (OR) and its level one of the ``level=``
  terms (when the search names any); ``| head N`` caps the answer; time terms
  are ignored. A bad token is 401, an empty search 400, an unknown index an
  empty answer (as Splunk).
* OpenShift / Kubernetes (``Bearer <ocp_token>``): ``GET
  /api/v1/namespaces/{ns}``, ``.../pods/{pod}``, ``.../events?fieldSelector=
  involvedObject.name=<pod>``; an unknown namespace or pod is a 404 Status.
* GitHub (``Authorization: token <pat>`` or ``Bearer <pat>``), at the root
  (``base: "dotcom"``) and under ``/api/v3`` (GitHub Enterprise, ``base:
  "ghe"``): ``/rate_limit``, ``/repos/{o}/{r}``, ``.../compare/{base}...{head}``,
  ``.../commits/{sha}``, ``.../commits?since=&until=&per_page=``. An unknown or
  revoked PAT is 401 ``Bad credentials``; a repo the PAT cannot see, or an
  unknown sha, is 404 ``Not Found``.
* GitHub App (W19 INT-06): ``POST /app/installations/{id}/access_tokens`` (root
  and ``/api/v3``) takes ``Authorization: Bearer <App JWT>`` -- RS256, ``iss`` =
  the App id, signed by the key registered with ``POST /_fake/github/app``, not
  expired, issued at most 10 minutes ahead -- and answers 201 ``{token:
  "ghs_…", expires_at}``; the token then reads the installation's repos like a
  PAT. A bad JWT is 401 ``A JSON web token could not be decoded``, an unknown
  installation 404. The request log's ``token_kind`` says ``installation``
  (a ``ghs_`` token), ``app_jwt`` or ``pat``.
* GitLab (``PRIVATE-TOKEN``): ``GET /api/v4/projects/{ref}`` (URL-encoded path
  or numeric id); 401 / 404 as GitLab words them. W19 INT-05 adds, under the
  same ref: ``/repository/compare?from=&to=`` (``commits[]`` oldest -> newest
  with ``id``, ``title``, ``message``, ``author_name``, ``committed_date``, and
  the range's ``diffs[].new_path``), ``/repository/commits?since=&until=``
  (newest first) and ``/milestones`` (``iid``, ``title``, ``description``,
  ``state``, ``due_date``, ``web_url``; steer them with ``POST
  /_fake/gitlab/milestones``).
* Receiver: ``POST /hooks/ok`` (200), ``/hooks/fail`` (500), ``/hooks/reject``
  (400); every call is logged with its event, delivery id and whether it was
  signed.

Self-contained (Starlette only), because a deployment mounts this one file.

Inspect and steer it under ``/_fake/``:

* ``GET /_fake/requests`` -- every request seen: method, path, query, body,
  ``service`` (jira | slack | splunk | ocp | github | gitlab | hooks),
  ``auth`` (``ok`` | ``bad`` | ``none`` against THAT service's credential --
  never the credential itself), ``base`` (GitHub only: ``dotcom`` | ``ghe``),
  ``form`` (a form-encoded POST, parsed), ``authorized`` (Jira Basic auth), the
  ``X-TestLookup-Delivery`` header
* ``POST /_fake/reset`` -- forget issues, messages, requests, faults, Splunk
  searches, receiver calls and revoked tokens (seeded data stays)
* ``POST /_fake/faults`` -- ``{"method", "path", "status", "times", "after",
  "delay_s", "retry_after", "body"}``: answer the next matching requests with
  ``status``; ``after: true`` runs the real handler first (Jira created the
  issue, then the answer was lost)
* ``POST /_fake/jira/issues/{key}/status`` -- ``{"name", "category"}``
* ``POST /_fake/jira/projects/{key}/versions`` -- ``{"name", "description"}``
* ``POST /_fake/slack/revoke`` -- ``{"path"}``: that webhook answers 404
* ``GET /_fake/splunk/searches`` -- every search: ``{endpoint, search (the
  decoded SPL), index, services, auth}``
* ``POST /_fake/splunk/events`` -- ``{"index", "events"}``: append events
  (``{_time, level, service, message, host}``) to an index
* ``POST /_fake/ocp/pods`` -- ``{"namespace", "pod", "phase", "events",
  "image", "limits", "node"}``: create or replace a pod (and its namespace)
* ``POST /_fake/github/app`` -- ``{"app_id", "public_key" (PEM), "installations": {"<id>": ["owner/repo", ...]}}``:
  register a GitHub App (W19 INT-06).
* ``POST /_fake/github/revoke`` -- ``{"token"}``: that PAT now answers 401
* ``GET /_fake/hooks`` -- the receiver's log ``{path, status, event,
  delivery_id, signed}``

Environment (``_state_from_env``): ``FAKE_JIRA_EMAIL``, ``FAKE_JIRA_TOKEN``,
``FAKE_BASE_URL``, ``FAKE_SPLUNK_TOKEN``, ``FAKE_OCP_TOKEN``,
``FAKE_GITHUB_PAT`` (the PAT that sees ``acme/payments``), ``FAKE_GITLAB_PAT``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl

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

# ── The demo world (W10): defaults every test may override ─────────────────

GITHUB_HTML_ROOT = "https://github.fake.test"
GITLAB_WEB_ROOT = "https://gitlab.fake.test"


def demo_sha(repo: str, n: int) -> str:
    """A stable 40-hex commit sha for the ``n``-th demo commit of ``repo``."""
    return hashlib.sha1(f"{repo}#{n}".encode()).hexdigest()


def _demo_splunk_events() -> dict[str, list[dict[str, Any]]]:
    return {
        "main": [
            {"_time": "2026-10-20T09:58:00.000+00:00", "level": "WARN", "service": "checkout",
             "message": "slow response from payment-gateway: 2300 ms", "host": "app-1"},
            {"_time": "2026-10-20T09:59:30.000+00:00", "level": "WARN", "service": "checkout",
             "message": "retrying card authorisation (attempt 2)", "host": "app-2"},
        ],
        "payments": [
            {"_time": "2026-10-20T10:00:05.000+00:00", "level": "ERROR", "service": "checkout",
             "message": "db pool exhausted after 30000 ms", "host": "pay-db-proxy-1"},
            {"_time": "2026-10-20T10:00:07.000+00:00", "level": "ERROR", "service": "checkout",
             "message": "HTTP 503 from payment-gateway POST /authorize", "host": "app-1"},
            {"_time": "2026-10-20T10:00:09.000+00:00", "level": "ERROR", "service": "checkout",
             "message": "SQLTransientConnectionException: connection is not available, request timed out",
             "host": "app-2"},
        ],
    }


def _demo_ocp_pods() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "qa-payments": {
            "payment-runner-7d9f": {
                "phase": "Running",
                "image": "registry.fake.test/payments/runner:1.4.2",
                "limits": {"cpu": "500m", "memory": "512Mi"},
                "node": "worker-2",
                "host_ip": "10.0.4.12",
                "events": [
                    {"reason": "OOMKilled", "message": "Container runner exceeded its memory limit (512Mi)",
                     "type": "Warning", "count": 2, "firstTimestamp": "2026-10-20T09:55:00Z",
                     "lastTimestamp": "2026-10-20T10:00:02Z"},
                    {"reason": "BackOff", "message": "Back-off restarting failed container runner",
                     "type": "Warning", "count": 1, "firstTimestamp": "2026-10-20T10:00:03Z",
                     "lastTimestamp": "2026-10-20T10:00:03Z"},
                ],
            },
        },
        "qa-testing": {},
    }


def _demo_commits(repo: str, specs: list[tuple[str, str, list[str]]]) -> list[dict[str, Any]]:
    start = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    return [
        {
            "sha": demo_sha(repo, n),
            "author": author,
            "date": (start + timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": message,
            "files": files,
        }
        for n, (author, message, files) in enumerate(specs)
    ]


def _demo_github_commits() -> dict[str, list[dict[str, Any]]]:
    return {
        # Oldest -> newest; the first is the green baseline's head.
        "acme/payments": _demo_commits("acme/payments", [
            ("Ana Lead", "Bootstrap the payment service", ["payments/app.py"]),
            ("Ben Dev", "Raise the payment client timeout to 30 s", ["payments/client.py"]),
            ("Ben Dev", "Shrink the DB connection pool to 5", ["payments/db.py", "config/pool.yaml"]),
            ("Cara QA", "Retry card authorisation once on 503", ["payments/authorize.py"]),
        ]),
        "acme/checkout": _demo_commits("acme/checkout", [
            ("Dan Dev", "Checkout page skeleton", ["checkout/page.py"]),
            ("Dan Dev", "Round totals half-up", ["checkout/totals.py"]),
        ]),
    }


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
    # ── W10: Splunk ──
    splunk_token: str = "fake-splunk-token"
    #: index -> events {_time, level, service, message, host}
    splunk_events: dict[str, list[dict[str, Any]]] = field(default_factory=_demo_splunk_events)
    #: {endpoint, search (the decoded SPL), index, services, auth}
    splunk_searches: list[dict[str, Any]] = field(default_factory=list)
    # ── W10: OpenShift / Kubernetes ──
    ocp_token: str = "fake-ocp-token"
    #: namespace -> pod -> {phase, image, limits, node, host_ip, events: [...]}
    ocp_pods: dict[str, dict[str, dict[str, Any]]] = field(default_factory=_demo_ocp_pods)
    # ── W10: GitHub / GitHub Enterprise ──
    #: PAT -> the repos it can see
    github_tokens: dict[str, set[str]] = field(default_factory=lambda: {
        "fake-gh-pat": {"acme/payments"},
        "fake-gh-pat-2": {"acme/checkout"},
    })
    #: repo -> commits oldest -> newest {sha (40 hex), author, date, message, files}
    github_commits: dict[str, list[dict[str, Any]]] = field(default_factory=_demo_github_commits)
    revoked_github_tokens: set[str] = field(default_factory=set)
    # ── W19 INT-06: GitHub Apps ──
    #: app id -> public key PEM
    github_app_keys: dict[str, str] = field(default_factory=dict)
    #: installation id -> {"app_id", "repos"}
    github_installations: dict[str, dict[str, Any]] = field(default_factory=dict)
    # ── W10: GitLab ──
    gitlab_token: str = "fake-gitlab-pat"
    #: path -> {"id", "web_url"}
    gitlab_projects: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "group/api-gateway": {"id": 42, "web_url": f"{GITLAB_WEB_ROOT}/group/api-gateway"},
    })
    # ── W19 INT-05: GitLab commits + milestones ──
    #: path -> commits oldest -> newest {sha, author, date, message, files}
    gitlab_commits: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {
        "group/api-gateway": _demo_commits("group/api-gateway", [
            ("Priya Nair", "Add rate-limit headers", ["gateway/ratelimit.py"]),
            ("Omar Haddad", "Retry upstream on 503", ["gateway/upstream.py", "gateway/retry.py"]),
            ("Dana Whitfield", "Tighten auth token parsing", ["gateway/auth.py"]),
        ]),
    })
    #: path -> milestones
    gitlab_milestones: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {
        "group/api-gateway": [
            {"id": 9001, "iid": 1, "title": "Gateway 2.4", "description": "Rate limits", "state": "active",
             "due_date": "2026-11-15", "web_url": f"{GITLAB_WEB_ROOT}/group/api-gateway/-/milestones/1"},
            {"id": 9002, "iid": 2, "title": "Gateway 2.5", "description": "", "state": "active",
             "due_date": None, "web_url": f"{GITLAB_WEB_ROOT}/group/api-gateway/-/milestones/2"},
        ],
    })
    # ── W20 ADM-10: a Vault-compatible KV v2 store (token + approle auth) ──
    vault_root_token: str = "fake-vault-token"
    vault_approle: tuple[str, str] = ("fake-role-id", "fake-secret-id")
    #: path (under the "secret" mount) -> {key: value}
    vault_kv: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "testlookup/jira": {"api_token": "fake-jira-token"},
    })
    vault_issued: set[str] = field(default_factory=set)
    # ── W10: outbound webhook receiver ──
    #: {path, status, event, delivery_id, signed}
    hooks: list[dict[str, Any]] = field(default_factory=list)
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
            self.splunk_searches.clear()
            self.hooks.clear()
            self.revoked_github_tokens.clear()
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


# ── Credentials: which service a request is for, and how it authenticated ───
# ``auth`` is "ok" | "bad" | "none" against THAT service's credential. The
# credential itself is never recorded.

_SPLUNK_PATHS = {"/services/server/info", "/services/search/jobs", "/services/search/jobs/export"}


def _service_of(path: str) -> Optional[str]:
    if path in _SPLUNK_PATHS:
        return "splunk"
    if path.startswith("/services/"):
        return "slack"
    if path.startswith("/rest/api/"):
        return "jira"
    if path.startswith("/api/v1/namespaces/") or path == "/api/v1/namespaces":
        return "ocp"
    if path.startswith("/api/v4/projects/"):
        return "gitlab"
    if path.startswith("/api/v3/") or path.startswith("/repos/") or path == "/rate_limit" or path.startswith("/app/"):
        return "github"
    if path.startswith("/hooks/"):
        return "hooks"
    return None


def _bearer(request: Request, *schemes: str) -> Optional[str]:
    """The token of an ``Authorization: <scheme> <token>`` header, or None."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() not in {s.lower() for s in schemes}:
        return None
    token = token.strip()
    return token or None


def _github_pat(request: Request) -> Optional[str]:
    return _bearer(request, "token", "Bearer")


def _github_pat_valid(state: FakeState, pat: Optional[str]) -> bool:
    return bool(pat) and pat in state.github_tokens and pat not in state.revoked_github_tokens


def _auth_status(request: Request, state: FakeState, service: Optional[str], path: str) -> str:
    if service == "jira":
        if not request.headers.get("authorization"):
            return "none"
        return "ok" if _authorized(request, state) else "bad"
    if service == "slack":
        # An incoming webhook's URL is its credential.
        return "bad" if path in state.revoked_webhooks else "ok"
    if service == "splunk":
        token = _bearer(request, "Bearer", "Splunk")
        if token is None:
            return "none"
        return "ok" if token == state.splunk_token else "bad"
    if service == "ocp":
        token = _bearer(request, "Bearer")
        if token is None:
            return "none"
        return "ok" if token == state.ocp_token else "bad"
    if service == "github":
        pat = _github_pat(request)
        if pat is None:
            return "none"
        return "ok" if _github_pat_valid(state, pat) else "bad"
    if service == "gitlab":
        token = request.headers.get("private-token")
        if not token:
            return "none"
        return "ok" if token == state.gitlab_token else "bad"
    return "none"


def _form(raw: bytes, content_type: str) -> Optional[dict[str, str]]:
    """The body of a form-encoded POST, parsed (last value wins); else None."""
    if "application/x-www-form-urlencoded" not in content_type.lower():
        return None
    return dict(parse_qsl(raw.decode(errors="replace"), keep_blank_values=True))


# ── Splunk handlers ─────────────────────────────────────────────────────────

_SPL_INDEX = re.compile(r'\bindex\s*=\s*"?([^"\s|()]+)"?', re.I)
_SPL_SERVICE = re.compile(r'\bservice\s*=\s*(?:"([^"]*)"|([^\s|()"]+))', re.I)
_SPL_LEVEL = re.compile(r'\blevel\s*=\s*(?:"([^"]*)"|([^\s|()"]+))', re.I)
_SPL_HEAD = re.compile(r'\|\s*head\s+(\d+)', re.I)
_SPL_STATS_COUNT = re.compile(r'\|\s*stats\s+count\s*$', re.I)


def _splunk_error(status: int, kind: str, text: str) -> JSONResponse:
    return JSONResponse({"messages": [{"type": kind, "text": text}]}, status_code=status)


def parse_spl(search: str) -> dict[str, Any]:
    """What the fake reads from an SPL search: index, services, levels, head, stats count."""
    index = _SPL_INDEX.search(search)
    head = _SPL_HEAD.search(search)
    return {
        "index": index.group(1) if index else None,
        "services": [a or b for a, b in _SPL_SERVICE.findall(search)],
        "levels": [(a or b).upper() for a, b in _SPL_LEVEL.findall(search)],
        "head": int(head.group(1)) if head else None,
        "count": bool(_SPL_STATS_COUNT.search(search.strip())),
    }


def _splunk_matches(state: FakeState, spl: dict[str, Any]) -> list[dict[str, Any]]:
    events = state.splunk_events.get(spl["index"] or "main", [])
    services = set(spl["services"])
    levels = set(spl["levels"])
    found = [
        dict(e) for e in events
        if (not services or e.get("service") in services)
        and (not levels or str(e.get("level", "")).upper() in levels)
    ]
    if spl["head"] is not None:
        found = found[: spl["head"]]
    return found


async def _splunk(request: Request, state: FakeState, path: str, method: str,
                  form: Optional[dict[str, str]], auth: str) -> Response:
    if path == "/services/server/info":
        if method != "GET":
            return _splunk_error(405, "FATAL", "Method not allowed.")
        if auth != "ok":
            return _splunk_error(401, "WARN", "call not properly authenticated")
        return JSONResponse({"entry": [{"content": {"serverName": "fake-splunk", "version": "9.2.0"}}]})
    if method != "POST":
        return _splunk_error(405, "FATAL", "Method not allowed.")
    form = form or {}
    search = (form.get("search") or "").strip()
    if search:
        spl = parse_spl(search)
        with state._lock:
            state.splunk_searches.append({
                "endpoint": path,
                "search": search,
                "index": spl["index"],
                "services": spl["services"],
                "auth": auth,
            })
    if auth != "ok":
        return _splunk_error(401, "WARN", "call not properly authenticated")
    if not search:
        return _splunk_error(400, "FATAL", "Empty search.")
    spl = parse_spl(search)
    if path == "/services/search/jobs":
        if form.get("exec_mode") != "oneshot":
            return _splunk_error(400, "FATAL", "The fake Splunk supports exec_mode=oneshot only.")
        return JSONResponse({"results": _splunk_matches(state, spl)})
    # /services/search/jobs/export: newline-delimited JSON results.
    found = _splunk_matches(state, {**spl, "head": None if spl["count"] else spl["head"]})
    if spl["count"]:
        lines = [json.dumps({"result": {"count": str(len(found))}})]
    else:
        lines = [json.dumps({"result": event}) for event in found]
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""), media_type="application/json")


# ── OpenShift / Kubernetes handlers ─────────────────────────────────────────


def _k8s_status(code: int, reason: str, message: str) -> JSONResponse:
    return JSONResponse({
        "kind": "Status", "apiVersion": "v1", "metadata": {}, "status": "Failure",
        "message": message, "reason": reason, "code": code,
    }, status_code=code)


async def _ocp(request: Request, state: FakeState, path: str, method: str, auth: str) -> Response:
    if auth != "ok":
        return _k8s_status(401, "Unauthorized", "Unauthorized")
    if method != "GET":
        return _k8s_status(405, "MethodNotAllowed", "the fake OpenShift API is read-only")
    parts = path.split("/")[4:]  # after /api/v1/namespaces/
    namespace = parts[0] if parts else ""
    pods = state.ocp_pods.get(namespace)
    if len(parts) == 1:
        if pods is None:
            return _k8s_status(404, "NotFound", f'namespaces "{namespace}" not found')
        return JSONResponse({
            "kind": "Namespace", "apiVersion": "v1",
            "metadata": {"name": namespace}, "status": {"phase": "Active"},
        })
    if len(parts) == 3 and parts[1] == "pods":
        pod = (pods or {}).get(parts[2])
        if pod is None:
            return _k8s_status(404, "NotFound", f'pods "{parts[2]}" not found')
        return JSONResponse({
            "kind": "Pod", "apiVersion": "v1",
            "metadata": {"name": parts[2], "namespace": namespace},
            "spec": {
                "nodeName": pod.get("node"),
                "containers": [{
                    "name": "runner", "image": pod.get("image"),
                    "resources": {"limits": dict(pod.get("limits") or {})},
                }],
            },
            "status": {"phase": pod.get("phase"), "hostIP": pod.get("host_ip")},
        })
    if len(parts) == 2 and parts[1] == "events":
        selector = request.query_params.get("fieldSelector", "")
        wanted = selector.split("involvedObject.name=", 1)[1].split(",", 1)[0] if "involvedObject.name=" in selector else None
        items = []
        for name, pod in sorted((pods or {}).items()):
            if wanted is not None and name != wanted:
                continue
            for i, event in enumerate(pod.get("events") or []):
                items.append({
                    "metadata": {"name": f"{name}.{i}", "namespace": namespace},
                    "involvedObject": {"kind": "Pod", "name": name, "namespace": namespace},
                    **event,
                })
        return JSONResponse({"kind": "EventList", "apiVersion": "v1", "items": items})
    return _k8s_status(404, "NotFound", "the server could not find the requested resource")


# ── GitHub / GitHub Enterprise handlers ─────────────────────────────────────


def _gh_not_found() -> JSONResponse:
    return JSONResponse({"message": "Not Found", "documentation_url": "https://docs.github.com/rest"}, status_code=404)


def _gh_commit(repo: str, commit: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha": commit["sha"],
        "html_url": f"{GITHUB_HTML_ROOT}/{repo}/commit/{commit['sha']}",
        "commit": {
            "author": {"name": commit.get("author"), "date": commit.get("date")},
            "message": commit.get("message"),
        },
    }


def _find_commit(commits: list[dict[str, Any]], ref: str) -> Optional[int]:
    """Index of the commit ``ref`` names: the full sha, or a unique prefix of 7+."""
    ref = ref.strip().lower()
    if len(ref) < 7:
        return None
    hits = [i for i, c in enumerate(commits) if c["sha"].lower().startswith(ref)]
    return hits[0] if len(hits) == 1 else None


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _token_kind(request: Request) -> Optional[str]:
    raw = _github_pat(request)
    if raw is None:
        return None
    if raw.startswith("ghs_"):
        return "installation"
    return "app_jwt" if raw.count(".") == 2 else "pat"


async def _github_app_token(request: Request, state: FakeState, installation_id: str) -> Response:
    import secrets as _secrets

    bad = JSONResponse({"message": "A JSON web token could not be decoded",
                        "documentation_url": "https://docs.github.com/rest"}, status_code=401)
    raw = _github_pat(request)
    if not raw or raw.count(".") != 2:
        return bad
    try:
        import jwt as _jwt

        unverified = _jwt.decode(raw, options={"verify_signature": False})
        app_id = str(unverified.get("iss"))
        key = state.github_app_keys.get(app_id)
        if key is None:
            return bad
        claims = _jwt.decode(raw, key, algorithms=["RS256"], options={"require": ["iat", "exp", "iss"]})
        if int(claims["exp"]) - int(claims["iat"]) > 660:
            return bad
    except Exception:
        return bad
    inst = state.github_installations.get(installation_id)
    if inst is None or str(inst.get("app_id")) != app_id:
        return _gh_not_found()
    token = "ghs_" + _secrets.token_hex(18)
    with state._lock:
        state.github_tokens[token] = set(inst.get("repos") or [])
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
    return JSONResponse({"token": token, "expires_at": expires.isoformat().replace("+00:00", "Z"),
                         "permissions": {"checks": "write", "contents": "read"}}, status_code=201)


async def _github(request: Request, state: FakeState, path: str, method: str) -> Response:
    am = re.fullmatch(r"/app/installations/([0-9]+)/access_tokens", path)
    if am and method == "POST":
        return await _github_app_token(request, state, am.group(1))
    if method != "GET":
        return _gh_not_found()
    pat = _github_pat(request)
    if not _github_pat_valid(state, pat):
        return JSONResponse(
            {"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest"}, status_code=401,
        )
    if path == "/rate_limit":
        return JSONResponse({"rate": {"limit": 5000, "remaining": 4999, "used": 1}})
    m = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)?", path)
    if not m:
        return _gh_not_found()
    repo = f"{m.group(1)}/{m.group(2)}"
    rest = m.group(3) or ""
    visible = state.github_tokens.get(pat or "", set())
    if repo not in visible:
        return _gh_not_found()
    commits = state.github_commits.get(repo, [])
    if rest == "":
        return JSONResponse({
            "full_name": repo, "name": m.group(2), "owner": {"login": m.group(1)},
            "html_url": f"{GITHUB_HTML_ROOT}/{repo}", "private": True,
        })
    cm = re.fullmatch(r"/compare/([^/.]+)\.\.\.([^/.]+)", rest)
    if cm:
        base, head = _find_commit(commits, cm.group(1)), _find_commit(commits, cm.group(2))
        if base is None or head is None:
            return _gh_not_found()
        between = commits[base + 1: head + 1] if head > base else []
        return JSONResponse({
            "status": "ahead" if between else ("identical" if head == base else "behind"),
            "ahead_by": len(between),
            "total_commits": len(between),
            "commits": [_gh_commit(repo, c) for c in between],
        })
    dm = re.fullmatch(r"/commits/([^/]+)", rest)
    if dm:
        found = _find_commit(commits, dm.group(1))
        if found is None:
            return _gh_not_found()
        commit = commits[found]
        return JSONResponse({
            **_gh_commit(repo, commit),
            "files": [{"filename": f} for f in commit.get("files") or []],
        })
    if rest == "/commits":
        since = _parse_time(request.query_params.get("since"))
        until = _parse_time(request.query_params.get("until"))
        try:
            per_page = max(1, min(100, int(request.query_params.get("per_page", 30))))
        except ValueError:
            per_page = 30
        window = []
        for commit in reversed(commits):  # newest first
            when = _parse_time(commit.get("date"))
            if since and (when is None or when < since):
                continue
            if until and (when is None or when > until):
                continue
            window.append(_gh_commit(repo, commit))
        return JSONResponse(window[:per_page])
    return _gh_not_found()


# ── GitLab handler ──────────────────────────────────────────────────────────


async def _gitlab(request: Request, state: FakeState, path: str, method: str, auth: str) -> Response:
    if auth != "ok":
        return JSONResponse({"message": "401 Unauthorized"}, status_code=401)
    if method != "GET":
        return JSONResponse({"message": "404 Not Found"}, status_code=404)
    # ``group%2Fapi-gateway`` reaches the app decoded (``group/api-gateway``);
    # either way everything after /api/v4/projects/ is the reference.
    ref = path[len("/api/v4/projects/"):].replace("%2F", "/").replace("%2f", "/")
    sub_path = ""
    for marker in ("/repository/", "/milestones"):
        if marker in ref:
            ref, sub_path = ref.split(marker, 1)[0], marker + ref.split(marker, 1)[1]
            break
    project_path = None
    if ref in state.gitlab_projects:
        project_path = ref
    else:
        for candidate, project in state.gitlab_projects.items():
            if str(project.get("id")) == ref:
                project_path = candidate
                break
    if project_path is None:
        return JSONResponse({"message": "404 Project Not Found"}, status_code=404)
    project = state.gitlab_projects[project_path]
    if sub_path:
        return _gitlab_sub(request, state, project_path, sub_path)
    return JSONResponse({
        "id": project.get("id"),
        "name": project_path.rsplit("/", 1)[-1],
        "path_with_namespace": project_path,
        "web_url": project.get("web_url") or f"{GITLAB_WEB_ROOT}/{project_path}",
    })


def _gl_commit(commit: dict[str, Any]) -> dict[str, Any]:
    message = commit.get("message") or ""
    return {"id": commit["sha"], "short_id": commit["sha"][:8], "title": message.splitlines()[0] if message else "",
            "message": message, "author_name": commit.get("author"), "committed_date": commit.get("date"),
            "created_at": commit.get("date")}


def _gitlab_sub(request: Request, state: FakeState, project_path: str, sub_path: str) -> Response:
    commits = state.gitlab_commits.get(project_path, [])
    if sub_path == "/repository/compare":
        base = _find_commit(commits, request.query_params.get("from", ""))
        head = _find_commit(commits, request.query_params.get("to", ""))
        if base is None or head is None:
            return JSONResponse({"message": "404 Commit Not Found"}, status_code=404)
        between = commits[base + 1: head + 1] if head > base else []
        files = []
        for c in between:
            for f in c.get("files") or []:
                if f not in files:
                    files.append(f)
        return JSONResponse({"commits": [_gl_commit(c) for c in between],
                             "diffs": [{"new_path": f, "old_path": f} for f in files], "compare_same_ref": head == base})
    if sub_path == "/repository/commits":
        since = _parse_time(request.query_params.get("since"))
        until = _parse_time(request.query_params.get("until"))
        out = []
        for c in reversed(commits):
            when = _parse_time(c.get("date"))
            if when and ((since and when < since) or (until and when > until)):
                continue
            out.append(_gl_commit(c))
        return JSONResponse(out)
    if sub_path == "/milestones":
        return JSONResponse(state.gitlab_milestones.get(project_path, []))
    return JSONResponse({"message": "404 Not Found"}, status_code=404)


# ── Outbound webhook receiver ───────────────────────────────────────────────

_HOOK_ANSWERS = {
    "/hooks/ok": (200, "ok"),
    "/hooks/fail": (500, "receiver error"),
    "/hooks/reject": (400, "bad request"),
}


async def _hook(request: Request, state: FakeState, path: str, method: str) -> Response:
    if method != "POST" or path not in _HOOK_ANSWERS:
        return PlainTextResponse("not found", status_code=404)
    status, text = _HOOK_ANSWERS[path]
    return PlainTextResponse(text, status_code=status)


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
    # ── W10 ──
    if path == "/_fake/splunk/searches":
        return JSONResponse(state.splunk_searches)
    if path == "/_fake/splunk/events":
        body = await request.json()
        index = str(body.get("index") or "main")
        events = [dict(e) for e in body.get("events") or [] if isinstance(e, dict)]
        with state._lock:
            state.splunk_events.setdefault(index, []).extend(events)
        return JSONResponse({"index": index, "events": len(state.splunk_events[index])})
    if path == "/_fake/ocp/pods":
        body = await request.json()
        namespace, pod = str(body["namespace"]), str(body["pod"])
        with state._lock:
            pods = state.ocp_pods.setdefault(namespace, {})
            current = pods.get(pod, {})
            pods[pod] = {
                "phase": body.get("phase", current.get("phase", "Running")),
                "image": body.get("image", current.get("image", "registry.fake.test/app:latest")),
                "limits": body.get("limits", current.get("limits", {})),
                "node": body.get("node", current.get("node", "worker-1")),
                "host_ip": body.get("host_ip", current.get("host_ip", "10.0.4.10")),
                "events": list(body.get("events", current.get("events", []))),
            }
        return JSONResponse({"namespace": namespace, "pod": pod, "events": len(pods[pod]["events"])})
    if path == "/_fake/vault/kv":
        body = await request.json()
        with state._lock:
            state.vault_kv[str(body["path"])] = {str(k): str(v) for k, v in dict(body["data"]).items()}
        return JSONResponse({"path": body["path"], "keys": sorted(body["data"])})
    if path == "/_fake/gitlab/milestones":
        body = await request.json()
        with state._lock:
            state.gitlab_milestones[str(body["project"])] = list(body["milestones"])
        return JSONResponse({"milestones": len(body["milestones"])})
    if path == "/_fake/github/app":
        body = await request.json()
        app_id = str(body["app_id"])
        with state._lock:
            state.github_app_keys[app_id] = str(body["public_key"])
            for inst_id, repos in (body.get("installations") or {}).items():
                state.github_installations[str(inst_id)] = {"app_id": app_id, "repos": set(repos)}
        return JSONResponse({"app_id": app_id, "installations": sorted(state.github_installations)})
    if path == "/_fake/github/revoke":
        token = str((await request.json())["token"])
        with state._lock:
            state.revoked_github_tokens.add(token)
        return JSONResponse({"revoked": len(state.revoked_github_tokens)})
    if path == "/_fake/hooks":
        return JSONResponse(state.hooks)
    return JSONResponse({"error": "unknown control path"}, status_code=404)


# ── App ─────────────────────────────────────────────────────────────────────


async def _vault(request: Request, state: FakeState, path: str, method: str) -> Response:
    """W20 ADM-10: Vault KV v2 under mount ``secret`` — ``POST /v1/auth/approle/login``, ``GET
    /v1/auth/token/lookup-self``, ``GET /v1/secret/data/<path>``; ``X-Vault-Token`` is the root token or one the
    approle login issued. Logged as service ``vault`` (auth ok|bad|none, never the token)."""
    token = request.headers.get("x-vault-token")
    ok = bool(token) and (token == state.vault_root_token or token in state.vault_issued)
    with state._lock:
        state.requests.append({"method": method, "path": path, "query": dict(request.query_params), "body": None,
                               "service": "vault", "auth": "ok" if ok else ("bad" if token else "none")})
    if path == "/v1/auth/approle/login" and method == "POST":
        body = await request.json()
        if (body.get("role_id"), body.get("secret_id")) != state.vault_approle:
            return JSONResponse({"errors": ["invalid role or secret ID"]}, status_code=400)
        issued = f"s.fake-{len(state.vault_issued) + 1}"
        state.vault_issued.add(issued)
        return JSONResponse({"auth": {"client_token": issued, "lease_duration": 3600, "renewable": True}})
    if not ok:
        return JSONResponse({"errors": ["permission denied"]}, status_code=403)
    if path == "/v1/auth/token/lookup-self":
        return JSONResponse({"data": {"policies": ["testlookup-read"], "ttl": 3600}})
    prefix = "/v1/secret/data/"
    if path.startswith(prefix) and method == "GET":
        data = state.vault_kv.get(path[len(prefix):])
        if data is None:
            return JSONResponse({"errors": []}, status_code=404)
        return JSONResponse({"data": {"data": dict(data), "metadata": {"version": 1}}})
    return JSONResponse({"errors": ["unsupported path"]}, status_code=404)


def create_app(state: Optional[FakeState] = None) -> Starlette:
    state = state or FakeState()

    async def dispatch(request: Request) -> Response:
        path = request.url.path
        method = request.method.upper()
        if path.startswith("/_fake/"):
            return await _control(request, state)
        if path.startswith("/v1/"):
            return await _vault(request, state, path, method)
        raw = await request.body()
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode(errors="replace")[:2000]
        form = _form(raw, request.headers.get("content-type", ""))
        service = _service_of(path)
        # GitHub Enterprise serves the same API under /api/v3.
        base: Optional[str] = None
        route = path
        if service == "github":
            if path.startswith("/api/v3/"):
                base, route = "ghe", path[len("/api/v3"):]
            else:
                base = "dotcom"
        auth = _auth_status(request, state, service, path)
        entry: dict[str, Any] = {
            "method": method,
            "path": path,
            "query": dict(request.query_params),
            "body": body,
            "authorized": _authorized(request, state),
            "delivery_id": request.headers.get("x-testlookup-delivery"),
            "service": service,
            "auth": auth,
            "form": form,
        }
        if base is not None:
            entry["base"] = base
        if service == "github":
            entry["token_kind"] = _token_kind(request)
        with state._lock:
            state.requests.append(entry)

        def log_hook(status: int) -> None:
            if service != "hooks":
                return
            with state._lock:
                state.hooks.append({
                    "path": path,
                    "status": status,
                    "event": request.headers.get("x-testlookup-event"),
                    "delivery_id": request.headers.get("x-testlookup-delivery"),
                    "signed": bool(request.headers.get("x-testlookup-signature")),
                })

        fault = _take_fault(state, method, path)
        if fault and fault.get("delay_s"):
            await asyncio.sleep(float(fault["delay_s"]))
        if fault and not fault.get("after"):
            faulted = _fault_response(fault)
            log_hook(faulted.status_code)
            return faulted

        # Dispatch order (first match wins): Splunk's /services/ paths before
        # Slack's /services/ prefix, then Jira, OpenShift, GitLab, GitHub (and
        # GHE under /api/v3), the webhook receiver, else 404.
        if service == "splunk":
            response = await _splunk(request, state, path, method, form, auth)
        elif service == "slack":
            response = await _slack_webhook(request, state)
        elif service == "ocp":
            response = await _ocp(request, state, path, method, auth)
        elif service == "gitlab":
            response = await _gitlab(request, state, path, method, auth)
        elif service == "github":
            response = await _github(request, state, route, method)
        elif service == "hooks":
            response = await _hook(request, state, path, method)
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
            response = _fault_response(fault)
        log_hook(response.status_code)
        return response

    app = Starlette(routes=[Route("/{path:path}", dispatch, methods=["GET", "POST", "PUT", "DELETE"])])
    app.state.fake = state
    return app


def _state_from_env() -> FakeState:
    state = FakeState(
        jira_email=os.environ.get("FAKE_JIRA_EMAIL", "fake-bot@example.test"),
        jira_token=os.environ.get("FAKE_JIRA_TOKEN", "fake-jira-token"),
        base_url=os.environ.get("FAKE_BASE_URL", "https://fake-jira.example.test"),
        splunk_token=os.environ.get("FAKE_SPLUNK_TOKEN", "fake-splunk-token"),
        ocp_token=os.environ.get("FAKE_OCP_TOKEN", "fake-ocp-token"),
        gitlab_token=os.environ.get("FAKE_GITLAB_PAT", "fake-gitlab-pat"),
    )
    github_pat = os.environ.get("FAKE_GITHUB_PAT")
    if github_pat and github_pat != "fake-gh-pat":
        # The deployment's PAT sees what the default "fake-gh-pat" saw.
        state.github_tokens[github_pat] = state.github_tokens.pop("fake-gh-pat")
    return state


app = create_app(_state_from_env())
