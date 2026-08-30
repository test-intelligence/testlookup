"""The CLI and the MCP server are API clients, and nothing checked their wire.

The UI's contract with the backend is exercised constantly. The other two
clients -- ``cli/`` and ``mcp/`` -- are not, and they drifted:

``testlookup tests list`` called ``GET /api/v1/runs/{run_id}/test-cases``. The
runs router serves ``/{run_id}/tests``; there is no ``test-cases`` sub-path
under that prefix. Every invocation 404'd, was mapped to ``Not found.`` and
exited 5. The command had never worked.

``reports share --expires 30`` and the MCP ``create_share_link`` both sent
``{"expires_days": N}``. ``CreateShareLinkRequest`` declares **expiry_days**,
and Pydantic v2 ignores extra keys by default -- so the value was dropped and
every link silently expired in the default 7 days while the CLI printed the
link and exited 0. The frontend sends ``expiry_days`` correctly, which is why
the drift was invisible from the UI.

The MCP ``list_test_runs`` tool declared a ``days`` argument, documented it as
"1-90, default 7", and never put it in the request. FastAPI ignores undeclared
query params, so every call silently used the backend's 30-day default. An
agent asking "what ran in the last day?" got a month of runs and reasoned over
them as if they were from the last 24 hours.

All three are the same shape: a client says something the server never hears,
and nothing fails loudly. These tests live in the backend suite because that is
the only job with the real FastAPI application *and* the client sources on
disk -- ``sdk-cli-test`` installs only ``client/`` and ``cli/``, and
``mcp-test`` runs with the MCP requirements alone, so neither can see a route
table. ``mcp/tests/test_mcp_endpoint_paths_exist.py`` does what it can from
that side (it catches a bare router prefix); this asks the assembled app.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_DIR = REPO_ROOT / "cli" / "testlookup_cli"
MCP_TOOLS_DIR = REPO_ROOT / "mcp" / "tools"

#: A literal ``/api/v1/...`` path inside a string or f-string.
_PATH_RE = re.compile(r"""["'](/api/v1/[a-zA-Z0-9_\-/{}\.]*)["']""")

#: Paths a client builds by concatenation, so the literal is only a prefix.
#: Recorded as data rather than skipped silently.
_PREFIX_ONLY_LITERALS: set[str] = set()


def _normalise(path: str) -> str:
    """``/runs/{run_id}/tests`` -> ``/runs/{}/tests``.

    Param *names* are the client's business; the shape is the contract.
    """
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/")) or "/"


def _real_routes() -> set[str]:
    from app.main import app

    routes = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            routes.add(_normalise(path))
    return routes


def _client_files() -> list[Path]:
    files = []
    if CLI_DIR.is_dir():
        files += sorted(CLI_DIR.rglob("*.py"))
    if MCP_TOOLS_DIR.is_dir():
        files += sorted(MCP_TOOLS_DIR.glob("*.py"))
    return [f for f in files if "__pycache__" not in f.parts]


def _literal_paths() -> dict[Path, set[str]]:
    found: dict[Path, set[str]] = {}
    for f in _client_files():
        src = f.read_text(encoding="utf-8", errors="ignore")
        paths = {m.group(1) for m in _PATH_RE.finditer(src)}
        paths = {p for p in paths if p not in _PREFIX_ONLY_LITERALS}
        if paths:
            found[f] = paths
    return found


def test_the_client_trees_are_actually_on_disk():
    """A guard that silently scanned nothing would be worse than no guard."""
    assert CLI_DIR.is_dir(), f"CLI source not found at {CLI_DIR}"
    assert MCP_TOOLS_DIR.is_dir(), f"MCP tools not found at {MCP_TOOLS_DIR}"
    scanned = _literal_paths()
    assert len(scanned) >= 5, f"expected to scan several client modules, saw {len(scanned)}"
    total = sum(len(v) for v in scanned.values())
    assert total >= 30, f"expected to find many API paths, found {total}"


def test_every_client_api_path_resolves_to_a_real_route():
    """The measured bug: ``/api/v1/runs/{run_id}/test-cases`` 404s."""
    routes = _real_routes()
    offenders: list[str] = []
    for f, paths in _literal_paths().items():
        for p in sorted(paths):
            if _normalise(p) not in routes:
                offenders.append(f"{f.relative_to(REPO_ROOT).as_posix()} -> {p}")
    assert not offenders, (
        "A CLI/MCP client calls a path the backend does not serve, so every "
        "invocation 404s:\n  " + "\n  ".join(offenders)
    )


def test_the_runs_test_case_listing_uses_the_route_that_exists():
    """Pin the specific regression, so the fix cannot be reverted quietly."""
    src = (CLI_DIR / "commands" / "tests.py").read_text(encoding="utf-8")
    assert "/api/v1/runs/{run_id}/tests" in src
    assert "test-cases" not in src, (
        "`testlookup tests list` is back on the route that does not exist"
    )


# ── Request bodies must use the field names the models declare ──────────────

def _share_link_body_keys(path: Path) -> set[str]:
    """Keys of the ``json_body={...}`` dict in a create-share-link call."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "json_body" or not isinstance(kw.value, ast.Dict):
                continue
            literal = {
                k.value for k in kw.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if "layout" in literal:  # the share-link body, not another POST
                keys |= literal
    return keys


@pytest.mark.parametrize(
    "rel",
    ["cli/testlookup_cli/commands/reports.py", "mcp/tools/reports.py"],
)
def test_share_link_body_uses_the_models_field_name(rel: str):
    """``expires_days`` was silently dropped; the model declares ``expiry_days``."""
    from app.routers.reports import CreateShareLinkRequest

    declared = set(CreateShareLinkRequest.model_fields)
    sent = _share_link_body_keys(REPO_ROOT / rel)
    assert sent, f"{rel}: no share-link json_body found -- did the call move?"
    unknown = sent - declared
    assert not unknown, (
        f"{rel} sends {sorted(unknown)} to CreateShareLinkRequest, which "
        f"declares {sorted(declared)}. Pydantic ignores extra keys, so the "
        "value is dropped and the caller is told it worked."
    )
    assert "expiry_days" in sent, (
        f"{rel} no longer sends the expiry at all -- the link would always "
        "take the model default"
    )


def test_the_share_link_model_really_ignores_the_old_key():
    """Proves the failure mode, so the test above is not merely stylistic."""
    from app.routers.reports import CreateShareLinkRequest

    built = CreateShareLinkRequest(**{"expires_days": 30, "layout": "executive"})
    assert built.expiry_days == 7, (
        "if this ever raises instead, the silent-drop hazard is gone and this "
        "guard can relax"
    )
    assert CreateShareLinkRequest(expiry_days=30, layout="executive").expiry_days == 30


# ── A declared argument must actually be sent ───────────────────────────────

def _unused_tool_arguments(path: Path) -> list[str]:
    """Parameters an MCP tool declares but never references in its body."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        decorated = any(
            isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
            for d in node.decorator_list
        )
        if not decorated:
            continue
        args = [a.arg for a in node.args.args + node.args.kwonlyargs if a.arg != "self"]
        used = {
            n.id for n in ast.walk(node) if isinstance(n, ast.Name)
        } | {
            # ``params={"days": days}`` reads as a Name, but a bare f-string
            # reference does too -- collect attribute bases for safety.
            getattr(n.value, "id", "")
            for n in ast.walk(node) if isinstance(n, ast.Attribute)
        }
        for a in args:
            if a not in used:
                offenders.append(f"{path.name}::{node.name}({a})")
    return offenders


def test_no_mcp_tool_declares_an_argument_it_never_uses():
    """``list_test_runs(days=...)`` was declared, documented, and dropped.

    A model reads the signature, passes ``days=1``, and receives the backend's
    30-day default with nothing in the response contradicting it.
    """
    offenders: list[str] = []
    for f in sorted(MCP_TOOLS_DIR.glob("*.py")):
        if "__pycache__" in f.parts:
            continue
        offenders += _unused_tool_arguments(f)
    assert not offenders, (
        "MCP tools declare arguments that never reach the request -- the model "
        "is told it can filter and is silently ignored:\n  "
        + "\n  ".join(offenders)
    )


def test_list_test_runs_forwards_days():
    """Pin the specific regression."""
    src = (MCP_TOOLS_DIR / "runs.py").read_text(encoding="utf-8")
    block = src[src.index("async def list_test_runs") :]
    block = block[: block.index("@mcp.tool()", 10)] if "@mcp.tool()" in block[10:] else block
    assert '"days": days' in block, "list_test_runs dropped the days filter again"


# ── A client must not render fields the endpoint never returns ──────────────

def _list_defects_select_columns() -> set[str]:
    """The response keys ``analytics_service.list_defects`` actually produces.

    It returns ``dict(row._mapping)`` over an explicit SELECT list, so the
    column names (or their ``AS`` aliases) are the wire contract.
    """
    src = (REPO_ROOT / "backend" / "app" / "services" / "analytics_service.py").read_text(
        encoding="utf-8"
    )
    start = src.index("def list_defects")
    block = src[start : src.index("FROM defects d", start)]
    select_body = block[block.rindex("SELECT") + len("SELECT") :]
    columns: set[str] = set()
    for raw in select_body.split(","):
        item = raw.strip().rstrip(",").strip()
        if not item or item.startswith("--"):
            continue
        item = item.split("--")[0].strip()
        if not item:
            continue
        if " AS " in item.upper():
            columns.add(item.rsplit(" ", 1)[-1].strip())
        else:
            columns.add(item.split(".")[-1].strip())
    return {c for c in columns if c.isidentifier()}


def _fields_read_by(path: Path) -> set[str]:
    """Every ``d.get("x")`` / ``d["x"]`` key read off a response row."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fields: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "d"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            fields.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "d"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            fields.add(node.slice.value)
    return fields


def test_the_select_list_is_actually_parsed():
    """Fail loudly rather than comparing against an empty set."""
    columns = _list_defects_select_columns()
    assert {"jira_ticket_id", "failure_category", "resolution_status", "test_name"} <= columns, (
        f"could not parse the list_defects SELECT list; got {sorted(columns)}"
    )


@pytest.mark.parametrize("rel", ["mcp/tools/defects.py", "mcp/tools/analytics.py"])
def test_defect_renderers_only_read_fields_the_endpoint_returns(rel: str):
    """``list_defects`` rendered severity/title/summary -- none are on the wire.

    Every row came out as "**Untitled** severity **?**", and the model had no
    signal that the field was absent rather than merely unset. The sibling tool
    reading the same endpoint had it right, which is how the drift survived.
    """
    returned = _list_defects_select_columns()
    read = _fields_read_by(REPO_ROOT / rel)
    assert read, f"{rel}: no response fields found -- did the renderer move?"
    phantom = read - returned
    assert not phantom, (
        f"{rel} renders {sorted(phantom)}, which /api/v1/analytics/defects "
        f"never returns (it selects {sorted(returned)}). Those render as "
        "placeholders that look like real, empty data."
    )
