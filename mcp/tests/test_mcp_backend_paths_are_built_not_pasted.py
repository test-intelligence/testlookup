"""Backend paths are built by ``client.path()``; no module pastes a value into one.

``client.path(template, **values)`` is the one place a caller-supplied value
enters a URL path: it percent-encodes each value into its own segment and
refuses an empty value, ``.`` and ``..``. The request helpers (``client.get``
and friends) refuse a plain string, and check the finished URL once more
before sending.

Two rules are pinned here:

1. The builder and the send-time check behave as described (unit tests).
2. No source file under ``mcp/`` hands the client a path made with an
   f-string, ``+``, ``%`` or ``.format``, or a variable: the first argument of
   every client call is ``api.path("<literal template>", name=value, ...)``.
   That is read from the syntax tree, so a new tool written the old way fails
   here before it ships.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402

RUN = "11111111-1111-4111-8111-111111111111"
VERBS = ("get", "post", "put", "patch", "delete")


# ── 1. The builder ───────────────────────────────────────────────────────────


def test_a_plain_value_is_encoded_into_one_segment():
    built = client.path("/api/v1/feature-flags/{key}/status", key="a/b?c#d%2e e")

    assert built == "/api/v1/feature-flags/a%2Fb%3Fc%23d%252e%20e/status"
    assert built.prefix == "/api/v1/feature-flags/"


def test_a_uuid_value_is_sent_in_canonical_form():
    built = client.path("/api/v1/runs/{run_id}/tests", run_id=client.uuid_id(f"  {RUN.upper()} "))

    assert built == f"/api/v1/runs/{RUN}/tests"


def test_a_template_without_values_is_sent_as_written():
    built = client.path("/api/v1/projects")

    assert built == "/api/v1/projects" and built.prefix == "/api/v1/projects"


@pytest.mark.parametrize("value", ["", "   ", ".", "..", None])
def test_an_empty_or_dot_segment_is_refused(value):
    with pytest.raises(client.PathArgumentError, match="key must not be empty"):
        client.path("/api/v1/feature-flags/{key}/status", key=value)


@pytest.mark.parametrize("value", ["../x?y#z", "run-1", "", None, f"{RUN}/../x", f"{RUN}?a=b"])
def test_a_value_that_is_not_a_uuid_is_refused_where_the_route_takes_a_uuid(value):
    with pytest.raises(client.PathArgumentError, match="run_id must be a UUID"):
        client.path("/api/v1/runs/{run_id}", run_id=client.uuid_id(value))


@pytest.mark.parametrize(
    "template",
    [
        "api/v1/projects",  # no leading slash
        "//evil.example/api",  # would name another host
        "/api/v1/runs/../auth/me",  # dot segment
        "/api/v1/runs/{run_id}x",  # a placeholder must be a whole segment
        "/api/v1/runs/abc?x=1",  # a query does not belong in a path
        "/api/v1/runs/a b",
        "/api/v1/runs/{}",  # unnamed placeholder
    ],
)
def test_a_template_that_is_not_fixed_segments_and_placeholders_is_refused(template):
    with pytest.raises(ValueError, match="path template"):
        client.path(template, run_id=RUN)


def test_a_value_pasted_into_the_template_is_caught_when_it_matters():
    """``path(f"/runs/{run_id}")`` with a hostile id: the template check refuses it."""
    hostile = "../auth/me?"
    with pytest.raises(ValueError, match="path template"):
        client.path(f"/api/v1/runs/{hostile}/intelligence")


def test_missing_and_surplus_values_are_refused():
    with pytest.raises(ValueError, match="needs a value"):
        client.path("/api/v1/runs/{run_id}")
    with pytest.raises(ValueError, match="no placeholder"):
        client.path("/api/v1/projects", run_id=RUN)


def test_the_refusal_reaches_the_caller_as_a_tool_and_resource_error():
    """The SDK only passes a ToolError / ResourceError message through to the model."""
    from mcp.server.mcpserver.exceptions import ResourceError, ToolError

    assert issubclass(client.PathArgumentError, (ToolError,))
    assert issubclass(client.PathArgumentError, (ResourceError,))
    assert issubclass(client.PathArgumentError, ValueError)


# ── 1b. The send-time check ──────────────────────────────────────────────────


@pytest.fixture
def backend(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    def use(base_url: str) -> list[httpx.Request]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)
        monkeypatch.setattr(client, "_client", http)
        return seen

    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    return use


@pytest.mark.parametrize("verb", VERBS)
def test_a_plain_string_is_never_sent(backend, verb):
    seen = backend("http://backend")

    with pytest.raises(TypeError, match="client.path"):
        asyncio.run(getattr(client, verb)(f"/api/v1/runs/{RUN}"))

    assert seen == []


@pytest.mark.parametrize(
    "forged",
    [
        client.ApiPath("/api/v1/runs/../auth/me", "/api/v1/runs/"),
        client.ApiPath("/api/v1/auth/me?/intelligence", "/api/v1/runs/"),
        client.ApiPath("/api/v1/runs/x?y", "/api/v1/runs/"),
        client.ApiPath("/api/v1/runs/x#y", "/api/v1/runs/"),
        client.ApiPath("/api/v1/runs//x", "/api/v1/runs/"),
        client.ApiPath("//evil.example/api/v1/runs/x", "/"),
        client.ApiPath("/api/v1/reviews/x/accept", "/api/v1/runs/"),
    ],
)
@pytest.mark.parametrize("verb", VERBS)
def test_a_path_that_left_its_fixed_prefix_is_refused_before_sending(backend, verb, forged):
    """Second check: even a path object is refused when the final URL is not under its prefix."""
    seen = backend("http://backend")

    with pytest.raises(client.PathArgumentError, match="Refused to send"):
        asyncio.run(getattr(client, verb)(forged))

    assert seen == []


@pytest.mark.parametrize("base_url", ["http://backend", "http://backend/", "https://host.example/testlookup"])
@pytest.mark.parametrize("verb", VERBS)
def test_a_built_path_is_sent_unchanged_under_the_base_url(backend, verb, base_url):
    seen = backend(base_url)
    built = client.path("/api/v1/feature-flags/{key}/status", key="a/b?c#d é")

    asyncio.run(getattr(client, verb)(built))

    expected = httpx.URL(base_url).raw_path.rstrip(b"/") + b"/api/v1/feature-flags/a%2Fb%3Fc%23d%20%C3%A9/status"
    assert [(r.method, r.url.raw_path) for r in seen] == [(verb.upper(), expected)]
    assert seen[0].headers["Authorization"] == "Bearer caller-token"


# ── 2. No module pastes a value into a path ──────────────────────────────────


def _client_aliases(tree: ast.AST) -> set[str]:
    """Names the ``client`` module is bound to in a file (``import client as api``)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.asname or alias.name for alias in node.names if alias.name == "client"}
    return names


def violations(source: str, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source)
    aliases = _client_aliases(tree)
    found = []

    def is_literal(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    def is_path_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "path"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in aliases
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        where = f"{filename}:{node.lineno}"
        if isinstance(func, ast.Name) and func.id == "ApiPath" or isinstance(func, ast.Attribute) and func.attr == "ApiPath":
            found.append(f"{where} builds an ApiPath by hand; use client.path()")
        if is_path_call(node):
            if len(node.args) != 1 or not is_literal(node.args[0]):
                found.append(f"{where} client.path() takes one literal template; values go in as keyword arguments")
            if any(keyword.arg is None for keyword in node.keywords):
                found.append(f"{where} client.path() values must be named, not **unpacked")
        if not (isinstance(func, ast.Attribute) and func.attr in VERBS and node.args):
            continue
        target = func.value
        first = node.args[0]
        if isinstance(target, ast.Name) and target.id in aliases:
            if not is_path_call(first):
                found.append(f"{where} client.{func.attr}() must be given client.path(...), not {ast.unparse(first)[:60]}")
        elif (
            isinstance(target, ast.Call)
            and isinstance(target.func, ast.Attribute)
            and target.func.attr in ("_get_client", "_get_auth_client")
            and not is_literal(first)
        ):
            found.append(f"{where} a raw HTTP call must use a literal path, not {ast.unparse(first)[:60]}")
    return found


def _source_files() -> list[Path]:
    files = []
    for f in sorted(MCP_DIR.rglob("*.py")):
        parts = f.relative_to(MCP_DIR).parts
        if parts[0] == "tests" or any(part.startswith(".") for part in parts) or parts == ("client.py",):
            continue
        files.append(f)
    return files


def test_the_scan_covers_the_tools_and_the_resources():
    names = {f.relative_to(MCP_DIR).as_posix() for f in _source_files()}

    assert {"tools/runs.py", "tools/intelligence.py", "resources/registry.py", "release_resolver.py"} <= names
    assert len([n for n in names if n.startswith("tools/")]) >= 26


def test_no_module_hands_the_client_a_pasted_path():
    found = []
    for f in _source_files():
        found += violations(f.read_text(encoding="utf-8"), f.relative_to(MCP_DIR).as_posix())

    assert not found, "Build backend paths with client.path(template, name=value):\n" + "\n".join(found)


@pytest.mark.parametrize(
    "call",
    [
        'api.get(f"/api/v1/runs/{run_id}/intelligence")',
        'api.post("/api/v1/runs/" + run_id + "/intelligence/refresh")',
        'api.get("/api/v1/runs/{}/intelligence".format(run_id))',
        'api.get("/api/v1/runs/%s/intelligence" % run_id)',
        "api.get(url)",
        'api.get("/api/v1/projects")',
        'api.delete(api.path(f"/api/v1/runs/{run_id}"))',
        'api.put(api.path("/api/v1/runs/" + run_id))',
        "api.get(api.path(template, run_id=run_id))",
        'api.get(api.path("/api/v1/runs/{run_id}", **values))',
        'api.get(api.ApiPath(f"/api/v1/runs/{run_id}", "/api/v1/runs/"))',
        'api._get_client().post(f"/api/v1/runs/{run_id}/intelligence/refresh")',
    ],
)
def test_the_scan_catches_each_way_of_pasting_a_value(call):
    source = f"import client as api\n\nasync def tool(run_id, url, template, values):\n    return await {call}\n"

    assert violations(source), f"not caught: {call}"


@pytest.mark.parametrize(
    "call",
    [
        'api.get(api.path("/api/v1/projects"))',
        'api.get(api.path("/api/v1/runs/{run_id}", run_id=api.uuid_id(run_id)), params={"a": 1})',
        'api.post(api.path("/api/v1/agents/{agent_id}/invoke", agent_id=agent_id), json_body={})',
        'api._get_client().post("/api/v1/auth/login", data={})',
        "other.get(url)",
    ],
)
def test_the_scan_accepts_the_built_form(call):
    source = f"import client as api\n\nasync def tool(run_id, agent_id, url, other):\n    return await {call}\n"

    assert violations(source) == []
