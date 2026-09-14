"""Generate the agent API's Postman collection and curl reference from OpenAPI (architecture E1.4).

``python -m app.services.agent_api_docs`` (run in ``backend/``) rewrites two files
from ``app.openapi()``:

* ``architecture/api/agents.postman_collection.json``
* ``architecture/api/AGENT_API_CURL.md``

``--check`` is a CI step: it fails when either file is stale, so a route change
cannot merge with docs that describe a different API.

Covered: the agent catalog (E1.1), invocations (E1.2, E1.3) and the human review
gate (E8.2).

Output is deterministic:

* routes are sorted by folder, path and method;
* the collection id is fixed;
* nothing volatile (such as the app version) is included.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
COLLECTION_PATH = REPO_ROOT / "architecture" / "api" / "agents.postman_collection.json"
CURL_PATH = REPO_ROOT / "architecture" / "api" / "AGENT_API_CURL.md"

_POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
_COLLECTION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "testlookup:agent-api-collection"))
_METHOD_ORDER = {"get": 0, "post": 1, "put": 2, "patch": 3, "delete": 4}
_FOLDERS = ("Catalog", "Invocations", "Reviews")
_VARIABLE_DEFAULTS = {"agent_id": "agent.summary.v1"}

#: Example JSON bodies, keyed by (METHOD, path). A selected route that takes a
#: request body but has no example here fails generation, so a new body route
#: cannot ship undocumented. ``{{name}}`` becomes a Postman variable, and a shell
#: variable in the curl reference.
EXAMPLE_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/agents/{agent_id}/invoke"): {
        "project_id": "{{project_id}}",
        "input": {"agent_id": "{{agent_id}}", "payload": {"test_run_id": "{{test_run_id}}"}},
        "mode": "async",
        "config_overrides": {"model": {"tier": "slm"}},
    },
    ("POST", "/api/v1/reviews/{review_id}/accept"): {
        "notes": "Checked against the failing test logs.",
    },
    ("POST", "/api/v1/reviews/{review_id}/reject"): {
        "reason_code": "unsupported_claim",
        "notes": "The cited stack trace belongs to another test.",
    },
}


def _folder(path: str) -> Optional[str]:
    if path.startswith("/api/v1/agents/catalog"):
        return "Catalog"
    if path.startswith("/api/v1/agents/invocations/") or path == "/api/v1/agents/{agent_id}/invoke":
        return "Invocations"
    if path.startswith("/api/v1/reviews/") or path == "/api/v1/projects/{project_id}/reviews":
        return "Reviews"
    return None


def select_operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The agent and review operations in the schema, in a stable order."""
    operations = []
    for path, methods in spec.get("paths", {}).items():
        folder = _folder(path)
        if folder is None:
            continue
        for method, operation in methods.items():
            if method in _METHOD_ORDER:
                operations.append({"folder": folder, "method": method.upper(), "path": path, "op": operation})
    operations.sort(key=lambda o: (_FOLDERS.index(o["folder"]), o["path"], _METHOD_ORDER[o["method"].lower()]))
    return operations


def _params(operation: dict[str, Any], where: str) -> list[dict[str, Any]]:
    return [p for p in operation.get("parameters", []) if p.get("in") == where]


def _shell_var(name: str) -> str:
    return "$" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _first_paragraph(text: Optional[str]) -> str:
    return (text or "").strip().split("\n\n", 1)[0].replace("\n", " ").strip()


def _example_body(method: str, path: str, operation: dict[str, Any]) -> Optional[dict[str, Any]]:
    if "requestBody" not in operation:
        return None
    try:
        return EXAMPLE_BODIES[(method, path)]
    except KeyError:
        raise ValueError(
            f"{method} {path} takes a request body but EXAMPLE_BODIES has no example for it"
        ) from None


def build_collection(spec: dict[str, Any]) -> dict[str, Any]:
    """A Postman v2.1 collection for the selected operations."""
    folders: dict[str, list[dict[str, Any]]] = {name: [] for name in _FOLDERS}
    variables: dict[str, str] = {"baseUrl": "http://localhost:8000", "token": "", "api_key": ""}
    for entry in select_operations(spec):
        method, path, operation = entry["method"], entry["path"], entry["op"]
        segments = []
        for segment in path.strip("/").split("/"):
            match = re.fullmatch(r"\{(\w+)\}", segment)
            if match:
                variables.setdefault(match.group(1), _VARIABLE_DEFAULTS.get(match.group(1), ""))
                segments.append("{{" + match.group(1) + "}}")
            else:
                segments.append(segment)

        query = []
        for param in _params(operation, "query"):
            if param["name"] == "ticket":
                variables.setdefault("ticket", "")
                value = "{{ticket}}"
            else:
                default = (param.get("schema") or {}).get("default")
                value = "" if default is None else str(default)
            query.append({"key": param["name"], "value": value, "disabled": not param.get("required", False)})

        body = _example_body(method, path, operation)
        headers: list[dict[str, Any]] = []
        if body is not None:
            headers.append({"key": "Content-Type", "value": "application/json"})
            # A variable a body uses must be declared too, or Postman sends it literally.
            for name in re.findall(r"\{\{(\w+)\}\}", json.dumps(body)):
                variables.setdefault(name, _VARIABLE_DEFAULTS.get(name, ""))
        for param in _params(operation, "header"):
            if param["name"] == "X-API-Key":
                headers.append({
                    "key": "X-API-Key",
                    "value": "{{api_key}}",
                    "disabled": True,
                    "description": "Instead of the bearer token: a project API key.",
                })
            elif param["name"] == "Idempotency-Key":
                headers.append({
                    "key": "Idempotency-Key",
                    "value": "{{$guid}}",
                    "description": _first_paragraph(param.get("description")),
                })
            else:
                headers.append({"key": param["name"], "value": "", "disabled": not param.get("required", False)})

        enabled_query = "&".join(f"{q['key']}={q['value']}" for q in query if not q["disabled"])
        url: dict[str, Any] = {
            "raw": "{{baseUrl}}/" + "/".join(segments) + (f"?{enabled_query}" if enabled_query else ""),
            "host": ["{{baseUrl}}"],
            "path": segments,
        }
        if query:
            url["query"] = query
        request: dict[str, Any] = {
            "method": method,
            "header": headers,
            "url": url,
            "description": (operation.get("description") or "").strip(),
        }
        if body is not None:
            request["body"] = {
                "mode": "raw",
                "raw": json.dumps(body, indent=2),
                "options": {"raw": {"language": "json"}},
            }
        folders[entry["folder"]].append({
            "name": str(operation.get("summary") or operation.get("operationId") or f"{method} {path}"),
            "request": request,
        })

    return {
        "info": {
            "_postman_id": _COLLECTION_ID,
            "name": "TestLookup - Agents API",
            "description": (
                "Agent catalog, invocations and human review. Generated from the OpenAPI schema by "
                "`python -m app.services.agent_api_docs`; do not edit by hand. Set `baseUrl` and "
                "`token` (a JWT), or enable the X-API-Key header on each request."
            ),
            "schema": _POSTMAN_SCHEMA,
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
        "variable": [{"key": key, "value": value} for key, value in variables.items()],
        "item": [{"name": name, "item": items} for name, items in folders.items() if items],
    }


def _curl(method: str, path: str, operation: dict[str, Any]) -> str:
    url = re.sub(r"\{(\w+)\}", lambda m: _shell_var(m.group(1)), path)
    required_query = [p for p in _params(operation, "query") if p.get("required")]
    if required_query:
        url += "?" + "&".join(f"{p['name']}={_shell_var(p['name'])}" for p in required_query)
    # The event stream authenticates with its single-use ticket, not a bearer token.
    streaming = path.endswith("/events")
    lines = [f'curl -s{"N" if streaming else ""} -X {method} "$BASE_URL{url}"']
    if not streaming:
        lines.append('  -H "Authorization: Bearer $TOKEN"')
    body = _example_body(method, path, operation)
    if body is not None:
        lines.append('  -H "Content-Type: application/json"')
    if any(p["name"] == "Idempotency-Key" for p in _params(operation, "header")):
        lines.append('  -H "Idempotency-Key: $(uuidgen)"')
    if body is None:
        return " \\\n".join(lines)
    lines.append("  --data @- <<EOF")
    payload = re.sub(r"\{\{(\w+)\}\}", lambda m: _shell_var(m.group(1)), json.dumps(body, indent=2))
    return " \\\n".join(lines) + "\n" + payload + "\nEOF"


def build_curl_markdown(spec: dict[str, Any]) -> str:
    """A curl reference for the selected operations, grouped like the collection."""
    operations = select_operations(spec)
    shell_vars = {"$BASE_URL", "$TOKEN"}
    for entry in operations:
        shell_vars.update(_shell_var(name) for name in re.findall(r"\{(\w+)\}", entry["path"]))
        shell_vars.update(_shell_var(p["name"]) for p in _params(entry["op"], "query") if p.get("required"))
        body = _example_body(entry["method"], entry["path"], entry["op"])
        if body is not None:
            shell_vars.update(_shell_var(n) for n in re.findall(r"\{\{(\w+)\}\}", json.dumps(body)))
    defaults = {"$BASE_URL": "http://localhost:8000", "$AGENT_ID": "agent.summary.v1"}

    out = [
        "# TestLookup agent API: curl reference",
        "",
        "Generated from the OpenAPI schema by `python -m app.services.agent_api_docs` (run in "
        "`backend/`). Do not edit by hand: CI fails when this file and the schema disagree. "
        "The same requests are in the Postman collection `agents.postman_collection.json`.",
        "",
        "Every request except the event stream authenticates with `Authorization: Bearer $TOKEN` "
        "(a JWT). A project API key works too: send `X-API-Key: <key>` instead.",
        "",
        "Variables used below:",
        "",
        "```bash",
        *[f"export {name[1:]}={defaults.get(name, '')}" for name in sorted(shell_vars)],
        "```",
    ]
    folder = None
    for entry in operations:
        if entry["folder"] != folder:
            folder = entry["folder"]
            out += ["", f"## {folder}"]
        operation = entry["op"]
        out += ["", f"### {entry['method']} {entry['path']}", ""]
        summary = _first_paragraph(operation.get("description")) or str(operation.get("summary") or "")
        if summary:
            out += [summary, ""]
        optional = [p["name"] for p in _params(operation, "query") if not p.get("required")]
        if optional:
            out += ["Optional query parameters: " + ", ".join(f"`{name}`" for name in optional) + ".", ""]
        out += ["```bash", _curl(entry["method"], entry["path"], operation), "```"]
    return "\n".join(out) + "\n"


def render(spec: dict[str, Any]) -> dict[Path, str]:
    """File path -> expected content."""
    return {
        COLLECTION_PATH: json.dumps(build_collection(spec), indent=2, ensure_ascii=False) + "\n",
        CURL_PATH: build_curl_markdown(spec),
    }


def load_spec() -> dict[str, Any]:
    from app.main import app  # noqa: PLC0415 -- the whole app is needed only to render its schema

    return app.openapi()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the agent API Postman collection and curl reference (E1.4).")
    parser.add_argument("--check", action="store_true", help="Fail when a generated file is stale instead of writing it.")
    args = parser.parse_args(argv)

    outputs = render(load_spec())
    stale = [
        path for path, text in outputs.items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    if args.check:
        if stale:
            names = ", ".join(path.name for path in stale)
            sys.stderr.write(
                f"Agent API docs are stale: {names}.\n"
                "Regenerate with: cd backend && python -m app.services.agent_api_docs\n"
            )
            return 1
        sys.stdout.write("Agent API docs match the OpenAPI schema.\n")
        return 0
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    sys.stdout.write(f"Wrote {len(outputs)} file(s); {len(stale)} changed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
