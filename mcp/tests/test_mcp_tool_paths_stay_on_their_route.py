"""A tool or resource argument can never move a backend request to another route.

Tool arguments are free strings chosen by the calling model. The tools used to
put them into the URL path as they were, so ``run_id="../reviews/<id>/accept?"``
on ``refresh_intelligence`` sent ``POST /api/v1/reviews/<id>/accept`` under the
caller's token: a different route than the tool the user approved.

The rule: every backend request a tool or resource sends matches one of the
path templates written in the MCP source, with the caller's value either
refused before any request or present only percent-encoded inside its own
segment. A normal UUID still produces exactly the documented path.

This drives the real server (``server.mcp.call_tool``) over a mock transport;
no network is used.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
import server  # noqa: E402
from resources import registry  # noqa: E402

VERBS = ("get", "post", "put", "patch", "delete")
BENIGN = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
HOSTILE = "../x?y#z"
RETARGET = f"../reviews/{OTHER}/accept?"
#: Sent by the ``login`` tool straight through the HTTP client: a fixed path, no caller value in it.
FIXED_PATHS = {("POST", "/api/v1/auth/login")}
#: Tools that check their own arguments before sending anything, and for which the generic
#: arguments this file builds (a UUID for every string) are not a valid call. ``login`` refuses
#: on a network session. Every other tool must reach the backend in the positive test.
SILENT_FOR_GENERIC_ARGUMENTS = {
    "correct_classification", "create_defect", "diff_ai_versions", "get_agent", "invoke_agent", "login",
    "record_fix_outcome",
}


def _source_files() -> list[Path]:
    return sorted(
        f for f in MCP_DIR.rglob("*.py")
        if "tests" not in f.relative_to(MCP_DIR).parts and not any(p.startswith(".") for p in f.relative_to(MCP_DIR).parts)
    )


def _template_of(node: ast.AST) -> str | None:
    """The path template a client call was written with: values become ``{}``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "path" and node.args:
        return _template_of(node.args[0])
    return None


def _path_pattern(template: str) -> re.Pattern[str]:
    return re.compile(
        "/".join("[^/]+" if part.startswith("{") else re.escape(part) for part in template.split("/"))
    )


def _routes() -> set[tuple[str, re.Pattern[str]]]:
    """Every (method, path pattern) the MCP source is written to call."""
    routes = set()
    for f in _source_files():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in VERBS
                and node.args
            ):
                template = _template_of(node.args[0])
                if template and template.startswith("/"):
                    routes.add((node.func.attr.upper(), _path_pattern(template)))
    return routes


ROUTES = _routes()


def _body() -> dict:
    """A permissive backend answer, so multi-request tools get past their first call."""
    row = {
        "id": BENIGN, "project_id": BENIGN, "run_id": BENIGN, "request_id": BENIGN, "release_id": BENIGN,
        "version": 1, "version_id": BENIGN, "name": "benign", "status": "PENDING", "ready_for_release": True,
        "test_name": "t", "token": "tok-fake-0001",
    }
    return {**row, "items": [row], "versions": [row], "total": 1, "pages": 1, "run": row}


@pytest.fixture
def backend(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path in ("/api/v1/projects", "/api/v1/releases", "/api/v1/quarantine"):
            return httpx.Response(200, json=[_body()])
        return httpx.Response(200, json=_body())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    return seen


def _tools() -> list:
    return asyncio.run(server.mcp.list_tools())


def _schema(tool) -> dict:
    return tool.model_dump(mode="json", by_alias=True).get("inputSchema") or {}


def _types(spec: dict) -> set[str]:
    found = {spec["type"]} if isinstance(spec.get("type"), str) else set(spec.get("type") or [])
    for option in spec.get("anyOf") or []:
        found |= _types(option)
    return found


def _value(spec: dict, flag: bool):
    kinds = _types(spec)
    if "string" in kinds:
        return BENIGN
    if "boolean" in kinds:
        return flag
    if "integer" in kinds or "number" in kinds:
        return 1
    if "array" in kinds:
        return []
    return {}


def _cases() -> list[tuple[str, str]]:
    cases = []
    for tool in _tools():
        for name, spec in (_schema(tool).get("properties") or {}).items():
            if "string" in _types(spec):
                cases.append((tool.name, name))
    return cases


def _call(tool_name: str, hostile_arg: str | None, hostile: str, flag: bool) -> None:
    tool = next(t for t in _tools() if t.name == tool_name)
    arguments = {name: _value(spec, flag) for name, spec in (_schema(tool).get("properties") or {}).items()}
    if hostile_arg is not None:
        arguments[hostile_arg] = hostile
    try:
        asyncio.run(server.mcp.call_tool(tool_name, arguments))
    except Exception:  # noqa: BLE001 - a refusal or a rendering error; only the requests matter here
        pass


def _off_route(seen: list[httpx.Request], hostile: str) -> list[str]:
    """Requests that match no template, or carry the hostile text unencoded in their path."""
    problems = []
    for request in seen:
        raw = request.url.raw_path.split(b"?", 1)[0].decode("ascii")
        on_route = (request.method, raw) in FIXED_PATHS or any(
            method == request.method and pattern.fullmatch(raw) for method, pattern in ROUTES
        )
        if not on_route:
            problems.append(f"{request.method} {raw} matches no path template in the MCP source")
        elif hostile in raw or "/../" in raw or raw.endswith("/.."):
            problems.append(f"{request.method} {raw} carries the argument unencoded")
    return problems


def test_the_catalogue_has_tools_with_string_arguments():
    assert len(_cases()) > 100, "the parametrised cases below did not find the tool catalogue"
    assert len(ROUTES) > 60, "the path templates were not found in the MCP source"


def test_mcp_methods_and_paths_match_the_backend_api_contract():
    """The backend documentation check regenerates this contract from its mounted app."""
    schema_path = MCP_DIR.parent / "docs" / "reference" / "openapi.json"
    api_paths = json.loads(schema_path.read_text(encoding="utf-8"))["paths"]
    backend_routes = {
        (verb.upper(), _path_pattern(path))
        for path, operations in api_paths.items()
        for verb in operations
        if verb in VERBS
    }
    assert len(ROUTES) >= 80
    unmatched = sorted(f"{method} {pattern.pattern}" for method, pattern in ROUTES - backend_routes)
    assert not unmatched, f"MCP requests without corresponding backend operations: {unmatched}"


@pytest.mark.parametrize("flag", [False, True], ids=["flags-off", "flags-on"])
@pytest.mark.parametrize(("tool_name", "arg"), _cases(), ids=lambda v: v)
def test_a_hostile_string_argument_never_leaves_the_tools_route(backend, tool_name, arg, flag):
    _call(tool_name, arg, HOSTILE, flag)

    assert not _off_route(backend, HOSTILE), (
        f"{tool_name}({arg}={HOSTILE!r}) sent a request off its route: {_off_route(backend, HOSTILE)}"
    )


@pytest.mark.parametrize("flag", [False, True], ids=["flags-off", "flags-on"])
@pytest.mark.parametrize("tool_name", sorted({name for name, _ in _cases()}))
def test_normal_ids_still_reach_the_backend_on_a_known_route(backend, tool_name, flag):
    """The positive case: with ordinary UUIDs the tools still send their requests."""
    _call(tool_name, None, "", flag)

    assert not _off_route(backend, HOSTILE), _off_route(backend, HOSTILE)


def test_most_tools_send_a_request_for_normal_ids(backend):
    silent = []
    for tool_name in sorted({name for name, _ in _cases()}):
        before = len(backend)
        _call(tool_name, None, "", True)
        if len(backend) == before:
            silent.append(tool_name)
    assert set(silent) <= SILENT_FOR_GENERIC_ARGUMENTS, (
        f"these tools sent nothing for ordinary UUID arguments: {sorted(set(silent) - SILENT_FOR_GENERIC_ARGUMENTS)}"
    )


def test_refresh_intelligence_cannot_be_turned_into_a_review_accept(backend):
    _call("refresh_intelligence", "run_id", RETARGET, False)

    paths = [request.url.raw_path.decode("ascii") for request in backend]
    assert not any("/reviews/" in path for path in paths), paths
    assert not any(request.method == "POST" for request in backend), (
        f"run_id is a UUID on the backend, so nothing should be sent at all: {paths}"
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "method", "expected"),
    [
        ("get_run_intelligence", {"run_id": BENIGN}, "GET", f"/api/v1/runs/{BENIGN}/intelligence"),
        ("refresh_intelligence", {"run_id": BENIGN}, "POST", f"/api/v1/runs/{BENIGN}/intelligence/refresh"),
        ("get_test_case", {"run_id": BENIGN, "test_id": OTHER}, "GET", f"/api/v1/runs/{BENIGN}/tests/{OTHER}"),
        ("get_agent", {"agent_id": "agent.summary.v1"}, "GET", "/api/v1/agents/catalog/agent.summary.v1"),
    ],
)
def test_a_normal_id_produces_exactly_the_documented_path(backend, tool_name, arguments, method, expected):
    asyncio.run(server.mcp.call_tool(tool_name, arguments))

    assert (backend[0].method, backend[0].url.raw_path.decode("ascii")) == (method, expected)


def test_a_non_uuid_key_is_sent_encoded_in_its_own_segment(backend):
    """Ids that are not UUIDs on the backend (agent ids, flag keys) are encoded, not refused."""
    _call("check_feature_flag", "key", HOSTILE, False)

    assert [r.url.raw_path.split(b"?")[0].decode("ascii") for r in backend] == [
        "/api/v1/feature-flags/" + quote(HOSTILE, safe="") + "/status"
    ]


def test_a_uuid_argument_that_is_not_a_uuid_is_refused_with_a_clear_message(backend):
    result = None
    try:
        result = asyncio.run(server.mcp.call_tool("get_run_details", {"run_id": HOSTILE}))
    except Exception as exc:  # noqa: BLE001 - the SDK may raise or return an error result
        text = str(exc)
    else:
        text = str(result)

    assert backend == [], "no request may be sent for an id that is not a UUID"
    assert "run_id must be a UUID" in text


# ── Resources ────────────────────────────────────────────────────────────────


class _FakeMCP:
    def __init__(self):
        self.resources = {}

    def resource(self, uri):
        def decorate(fn):
            self.resources[uri] = fn
            return fn
        return decorate


def _resources() -> dict:
    fake = _FakeMCP()
    registry.register(fake)
    return fake.resources


def _resource_cases() -> list[tuple[str, str]]:
    return [(uri, name) for uri in _resources() for name in re.findall(r"{(\w+)}", uri)]


@pytest.mark.parametrize(("uri", "arg"), _resource_cases())
def test_a_hostile_resource_argument_never_leaves_the_resources_route(backend, uri, arg):
    arguments = {name: BENIGN for name in re.findall(r"{(\w+)}", uri)}
    arguments[arg] = HOSTILE
    try:
        asyncio.run(_resources()[uri](**arguments))
    except Exception:  # noqa: BLE001 - a refusal; only the requests matter here
        pass

    assert not _off_route(backend, HOSTILE), _off_route(backend, HOSTILE)


@pytest.mark.parametrize("uri", sorted(_resources()))
def test_resources_still_read_for_normal_ids(backend, uri):
    arguments = {name: BENIGN for name in re.findall(r"{(\w+)}", uri)}

    asyncio.run(_resources()[uri](**arguments))

    assert backend, f"{uri} sent no request"
    assert not _off_route(backend, HOSTILE), _off_route(backend, HOSTILE)
