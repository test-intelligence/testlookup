#!/usr/bin/env python3
"""Regenerate source-derived handoff references without starting application services.

Run with the backend requirements installed: python scripts/generate_handoff_reference.py
Use --check to detect drift without writing. No lifespan, DB queries or worker dispatch.
"""

from __future__ import annotations

import ast
import csv
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "reference"
CHECK = "--check" in sys.argv
ERRORS: list[str] = []
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "backend"))
# Clear application settings from this process and import from an empty cwd so
# Pydantic cannot load the developer's .env. System PATH/runtime settings stay.
_config_tree = ast.parse(
    (ROOT / "backend/app/core/config.py").read_text(encoding="utf-8")
)
for _class in _config_tree.body:
    if isinstance(_class, ast.ClassDef) and _class.name == "Settings":
        for _field in _class.body:
            if isinstance(_field, ast.AnnAssign) and isinstance(
                _field.target, ast.Name
            ):
                os.environ.pop(_field.target.id, None)
                if isinstance(_field.value, ast.Call):
                    for _kw in _field.value.keywords:
                        if (
                            _kw.arg in {"alias", "validation_alias"}
                            and isinstance(_kw.value, ast.Constant)
                            and isinstance(_kw.value.value, str)
                        ):
                            os.environ.pop(_kw.value.value, None)
_import_directory = tempfile.TemporaryDirectory(prefix="testlookup-docs-import-")
_original_cwd = Path.cwd()
os.chdir(_import_directory.name)
# Import requires a syntactically valid URL, never a live database.
os.environ.update(
    DATABASE_URL="postgresql+asyncpg://docs:docs@db.invalid:5432/docs",
    TESTING="true",
    OTEL_ENABLED="false",
    METRICS_ENABLED="false",
    APP_ENV="development",
    AI_OFFLINE_MODE="true",
)


def write(name: str, data: str) -> None:
    path = OUT / name
    data = data.rstrip() + "\n"
    if CHECK:
        if not path.exists() or path.read_text(encoding="utf-8") != data:
            ERRORS.append(str(path.relative_to(ROOT)))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8", newline="\n")


def cell(value: object) -> str:
    return str(value).replace("|", "&#124;").replace("\n", "<br>").replace("\r", "")


def source(path: str, line: int | None = None) -> str:
    suffix = f"#L{line}" if line else ""
    return f"[{path}{':' + str(line) if line else ''}](../../{path}{suffix})"


def header(title: str) -> str:
    return f"# {title}\n\n[Documentation home](../README.md) · [Regeneration](../handoff/maintenance.md)\n\nGenerated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../handoff/verification.md).\n\n"


def code_json(value: object) -> str:
    return (
        "```json\n"
        + json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n```\n"
    )


def tree_for(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def dependency_names(dependant: object) -> list[str]:
    names = set()

    def walk(dep: object) -> None:
        fn = getattr(dep, "call", None)
        if fn:
            name = getattr(fn, "__qualname__", type(fn).__name__)
            names.add(name)
        for child in getattr(dep, "dependencies", []):
            walk(child)

    for dep in getattr(dependant, "dependencies", []):
        walk(dep)
    return sorted(names)


def generate_api() -> tuple[int, int, int]:
    from app.main import app
    from fastapi.routing import APIRoute, APIWebSocketRoute

    spec = app.openapi()
    write(
        "openapi.json", json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False)
    )
    by_group: dict[str, list[str]] = {}
    index = header("REST endpoint index")
    index += "This indexes every OpenAPI HTTP operation. Exact parameters, body schemas and declared responses are in the linked domain pages and [OpenAPI JSON](openapi.json). Authentication dependencies and handler-raised errors supplement OpenAPI: absence of `security` does **not** imply anonymous access. See [authentication and errors](../api/README.md).\n\n"
    index += "| Method | Path | Summary | Reference |\n|---|---|---|---|\n"
    route_lookup = {
        (r.path, m.lower()): r
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
    }
    operations = 0
    incomplete = []
    for path, methods in sorted(spec["paths"].items()):
        for method, operation in sorted(methods.items()):
            if method not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "options",
                "head",
                "trace",
            }:
                continue
            operations += 1
            group = (operation.get("tags") or ["Other"])[0]
            slug = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")
            filename = f"api/{slug}.md"
            route = route_lookup.get((path, method))
            parts = [
                f"## {method.upper()} `{path}`\n",
                operation.get("summary", "") + "\n",
                operation.get("description", "") + "\n",
            ]
            if route:
                fn = inspect.unwrap(route.endpoint)
                file = Path(inspect.getsourcefile(fn)).resolve()
                rel = file.relative_to(ROOT).as_posix()
                line = inspect.getsourcelines(fn)[1]
                parts += [
                    f"Source: [{rel}:{line}](../../../{rel}#L{line}).\n",
                    "Dependency chain: "
                    + ", ".join(f"`{n}`" for n in dependency_names(route.dependant))
                    + ".\n",
                ]
                body = ast.parse(inspect.getsource(fn))
                parts.append(
                    "Declared Python handler arguments (includes exact role/guard options):\n\n```python\n"
                    + ast.unparse(body.body[0].args)
                    + "\n```\n"
                )
                raised = []
                for node in ast.walk(body):
                    if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(
                        "HTTPException"
                    ):
                        raised.append(ast.unparse(node))
                if raised:
                    parts.append(
                        "Direct handler error branches (dependency/service errors can add others):\n\n```python\n"
                        + "\n".join(sorted(set(raised)))
                        + "\n```\n"
                    )
            parts.append(
                "### Declared wire contract\n\nReferences such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).\n"
            )
            parts.append(
                code_json(
                    {
                        k: v
                        for k, v in operation.items()
                        if k not in {"description", "summary", "tags"}
                    }
                )
            )
            if any(
                not media.get("schema")
                for response in operation.get("responses", {}).values()
                for media in response.get("content", {}).values()
            ):
                incomplete.append(f"{method.upper()} {path}")
                if route:
                    returns = [
                        ast.unparse(n.value)
                        for n in ast.walk(body)
                        if isinstance(n, ast.Return) and n.value is not None
                    ]
                    if returns:
                        parts.append(
                            "Handler return expressions (source excerpts, not an inferred wire schema):\n\n```python\n"
                            + "\n".join(sorted(set(returns)))
                            + "\n```\n"
                        )
                parts.append(
                    "Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.\n"
                )
            by_group.setdefault(filename, []).append("\n".join(parts))
            index += f"| {method.upper()} | `{path}` | {cell(operation.get('summary', ''))} | [{cell(group)}]({filename}) |\n"
    for filename, sections in sorted(by_group.items()):
        # Domain pages live one level deeper than the other references.
        write(
            filename,
            header(
                filename.split("/")[-1].removesuffix(".md").replace("-", " ").title()
                + " API"
            )
            .replace("../README.md", "../../README.md")
            .replace("../handoff/", "../../handoff/")
            + "\n".join(sections),
        )
    index += f"\nTotal: **{operations} HTTP operations**, **{len(spec['paths'])} paths**, **{len(by_group)} domain pages**.\n"
    write("api-index.md", index)
    schemas = header("HTTP request and response schemas")
    schemas += "These are the full generated JSON Schema definitions, including required fields, defaults, enums, nested references and declared constraints. Custom validators and cross-field checks remain in the [Python contract inventory](python-contracts.md); JSON Schema does not encode every service-level rule.\n\n"
    for name, schema in sorted(spec["components"]["schemas"].items()):
        schemas += f"## {name}\n\n" + code_json(schema) + "\n"
    write("schemas.md", schemas)
    extra = header("Non-OpenAPI routes and contract gaps")
    extra += "Swagger/ReDoc/schema routes are framework-generated. `/metrics` is installed only when `METRICS_ENABLED=true`; generation deliberately disables metrics and telemetry. WebSocket and excluded HTTP routes below need their handler-level auth even when outside `/api/v1`.\n\n| Kind | Path | Source |\n|---|---|---|\n"
    for r in app.routes:
        if isinstance(r, APIWebSocketRoute) or (
            isinstance(r, APIRoute) and not r.include_in_schema
        ):
            fn = inspect.unwrap(r.endpoint)
            rel = Path(inspect.getsourcefile(fn)).resolve().relative_to(ROOT).as_posix()
            extra += f"| {'WebSocket' if isinstance(r, APIWebSocketRoute) else ', '.join(sorted(r.methods))} | `{r.path}` | {source(rel, inspect.getsourcelines(fn)[1])} |\n"
    for r in app.routes:
        if not isinstance(r, (APIRoute, APIWebSocketRoute)) and hasattr(r, "path"):
            extra += f"| {', '.join(sorted(getattr(r, 'methods', [])))} | `{r.path}` | FastAPI framework-generated route |\n"
    extra += (
        "\n## Responses without complete structured declarations\n\nThis is a documentation/typing limitation observed in source, not evidence that these endpoints fail. Prefer a typed response model when extending them.\n\n"
        + "\n".join("- `" + p + "`" for p in incomplete)
    )
    write("api-contract-gaps.md", extra)
    return operations, len(spec["paths"]), len(spec["components"]["schemas"])


def generate_models() -> int:
    import app.models.agentic_runtime
    import app.models.postgres  # noqa: F401
    from app.db.postgres import Base

    models = {}
    for file in (ROOT / "backend/app/models").glob("*.py"):
        for node in tree_for(file).body:
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and any(
                            isinstance(t, ast.Name) and t.id == "__tablename__"
                            for t in stmt.targets
                        )
                        and isinstance(stmt.value, ast.Constant)
                    ):
                        models[str(stmt.value.value)] = (
                            file.relative_to(ROOT).as_posix(),
                            node,
                        )
    text = header("PostgreSQL data dictionary")
    text += "Current SQLAlchemy metadata, without a database connection. All columns, server/application defaults, foreign keys, indexes and constraints below describe the model snapshot; [migrations](migrations.md) determine deployed schema history. Application validation and serializers can impose additional rules. JSON/JSONB types alone do not enforce a payload schema.\n\n"
    text += "| Table | Model | Columns |\n|---|---|---|\n"
    for name, table in sorted(Base.metadata.tables.items()):
        text += f"| [{name}](#{name}) | {models.get(name, ('', None))[1].name if name in models else 'metadata table'} | {len(table.columns)} |\n"
    for name, table in sorted(Base.metadata.tables.items()):
        text += f"\n## {name}\n\n"
        if name in models:
            file, node = models[name]
            text += (
                source(file, node.lineno)
                + "\n\n"
                + (ast.get_docstring(node) or "")
                + "\n\n"
            )
        text += "| Column | SQL type | Nullable | PK | Default | Foreign key / on delete |\n|---|---|---|---|---|---|\n"
        for col in table.columns:
            defaults = []
            for kind, default in [
                ("server", col.server_default),
                ("application", col.default),
                ("onupdate", col.onupdate),
                ("server_onupdate", col.server_onupdate),
            ]:
                if default is not None:
                    arg = getattr(default, "arg", default)
                    defaults.append(kind + "=" + getattr(arg, "__qualname__", str(arg)))
            value = "; ".join(defaults) or "—"
            fks = ", ".join(
                sorted(
                    f"{fk.target_fullname} / {fk.ondelete or 'unspecified'}"
                    for fk in col.foreign_keys
                )
            )
            text += f"| `{col.name}` | `{cell(col.type)}` | {col.nullable} | {col.primary_key} | `{cell(value)}` | {cell(fks)} |\n"
        text += "\nConstraints and indexes:\n\n"
        for constraint in sorted(
            table.constraints,
            key=lambda c: (
                type(c).__name__,
                str(c.name or ""),
                str(getattr(c, "sqltext", "")),
                ",".join(col.name for col in c.columns),
            ),
        ):
            value = str(getattr(constraint, "sqltext", "")) or ", ".join(
                c.name for c in constraint.columns
            )
            text += f"- `{type(constraint).__name__}` `{constraint.name or 'unnamed'}`: `{cell(value)}`\n"
        for idx in sorted(table.indexes, key=lambda i: str(i.name)):
            text += f"- Index `{idx.name}` (unique={idx.unique}): `{cell(', '.join(str(e) for e in idx.expressions))}`; options `{cell(json.dumps({k: str(v) for k, v in sorted(idx.dialect_kwargs.items())}, sort_keys=True))}`\n"
        if name in models:
            relationships = [
                ast.unparse(n)
                for n in models[name][1].body
                if isinstance(n, ast.AnnAssign)
                and isinstance(n.value, ast.Call)
                and ast.unparse(n.value.func) == "relationship"
            ]
            if relationships:
                text += (
                    "\nORM navigation and cascade declarations:\n\n```python\n"
                    + "\n".join(relationships)
                    + "\n```\n"
                )
    write("data-dictionary.md", text)
    return len(Base.metadata.tables)


def generate_static() -> dict:
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    # Exclude the generated docs and this generator to keep --check stable after commit.
    paths = [
        p
        for p in tracked
        if p
        and not p.startswith("docs/reference/")
        and p != "scripts/generate_handoff_reference.py"
    ]
    rows = []
    contracts = header("Python data contracts and enumerations")
    contracts += "Declared class fields and validators from all backend application modules, including inline router schemas, dataclasses, TypedDict state, enums and ORM models. Inherited fields are defined in linked base classes and public expanded schemas are in [HTTP schemas](schemas.md). Validator names are pointers to implementation, not an assertion that their logic is fully represented in JSON Schema.\n\n"
    config = header("Environment settings reference")
    config += "Source declarations only; no `.env` or running secrets are read. Compose/Helm/Kustomize can override these defaults. `Field` aliases and constraints are retained. For production requirements and profiles see [deployment](../operations/deployment.md).\n\n| Setting | Type | Source default/constraints |\n|---|---|---|\n"
    migrations = header("Database migration inventory")
    migrations += "Alembic revisions extracted without executing migrations. An inventory is not evidence that a live database is upgraded. Run `alembic current` and `alembic heads` in the target deployment.\n\n| File | Revision | Down revision | Purpose |\n|---|---|---|---|\n"
    module_count = 0
    for rel in paths:
        path = ROOT / rel
        suffix = path.suffix.lower()
        if suffix not in {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".mjs",
            ".java",
            ".go",
            ".sh",
            ".ps1",
            ".yaml",
            ".yml",
            ".toml",
            ".json",
            ".conf",
            ".sql",
        } and path.name not in {"Makefile", "Dockerfile", "Jenkinsfile"}:
            continue
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        role = (
            "test"
            if "/tests/" in rel
            or "/test/" in rel
            or re.search(r"(^|/)(test_|conftest)|\.(test|spec)\.", rel)
            else "implementation/configuration"
        )
        imports, symbols, description = [], [], ""
        if suffix == ".py":
            tree = ast.parse(raw)
            module_count += 1
            description = (ast.get_docstring(tree) or "").split("\n")[0]
            imports = sorted(
                {
                    (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in (n.names if isinstance(n, ast.Import) else [None])
                }
            )
            symbols = [
                n.name
                for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            if rel.startswith("backend/app/"):
                for node in tree.body:
                    if not isinstance(node, ast.ClassDef):
                        continue
                    fields = [
                        ast.unparse(n)
                        for n in node.body
                        if isinstance(n, (ast.AnnAssign, ast.Assign))
                    ]
                    validators = [
                        n
                        for n in node.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and any(
                            "validator" in ast.unparse(d)
                            or "serializer" in ast.unparse(d)
                            for d in n.decorator_list
                        )
                    ]
                    if not fields:
                        continue
                    contracts += (
                        f"## {rel} — {node.name}\n\n"
                        + source(rel, node.lineno)
                        + "\n\n"
                    )
                    contracts += f"Bases: `{cell(', '.join(ast.unparse(b) for b in node.bases))}`.\n\n"
                    contracts += (
                        (ast.get_docstring(node) or "")
                        + "\n\n```python\n"
                        + "\n".join(fields)
                        + "\n```\n\n"
                    )
                    for v in validators:
                        contracts += f"- Validator/serializer `{v.name}`: {source(rel, v.lineno)}. {ast.get_docstring(v) or 'Read source for the cross-field or conversion rule.'}\n"
            if rel == "backend/app/core/config.py":
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == "Settings":
                        for n in node.body:
                            if isinstance(n, ast.AnnAssign) and isinstance(
                                n.target, ast.Name
                            ):
                                config += f"| `{n.target.id}` | `{cell(ast.unparse(n.annotation))}` | `{cell(ast.unparse(n.value) if n.value else 'required')}` |\n"
            if rel.startswith("backend/migrations/versions/"):
                vals = {}
                for n in tree.body:
                    if isinstance(n, ast.Assign):
                        for t in n.targets:
                            if isinstance(t, ast.Name) and t.id in {
                                "revision",
                                "down_revision",
                            }:
                                vals[t.id] = ast.unparse(n.value)
                    elif (
                        isinstance(n, ast.AnnAssign)
                        and isinstance(n.target, ast.Name)
                        and n.target.id in {"revision", "down_revision"}
                    ):
                        vals[n.target.id] = ast.unparse(n.value)
                migrations += f"| {source(rel)} | `{cell(vals.get('revision', ''))}` | `{cell(vals.get('down_revision', ''))}` | {cell(description)} |\n"
        else:
            imports = re.findall(r'(?:from\s+|import\s*)[\'"]([^\'\"]+)[\'\"]', raw)
            symbols = re.findall(
                r"(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+(\w+)",
                raw,
            )
        rows.append(
            [
                rel,
                role,
                len(raw.splitlines()),
                description,
                "; ".join(imports),
                "; ".join(symbols),
            ]
        )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["path", "role", "lines", "module_description", "imports", "top_level_symbols"]
    )
    writer.writerows(rows)
    write("source-inventory.csv", buffer.getvalue())
    write("python-contracts.md", contracts)
    write("configuration.md", config)
    write("migrations.md", migrations)
    # MCP descriptors are functions registered with @mcp.tool/resource/prompt.
    integrations = header("MCP, CLI and frontend surfaces")
    integrations += "Static registration inventory. MCP/CLI commands call the REST API under the caller’s authority; their argument defaults do not bypass backend policy. See [integrations](../architecture/integrations.md).\n\n## MCP registrations\n\n| File / function | Kind | Arguments | Description |\n|---|---|---|---|\n"
    mcp_counts = {"tool": 0, "resource": 0, "prompt": 0}
    for rel in paths:
        if not rel.startswith("mcp/") or not rel.endswith(".py") or "/tests/" in rel:
            continue
        for node in ast.walk(tree_for(ROOT / rel)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in mcp_counts
                ):
                    mcp_counts[d.func.attr] += 1
                    integrations += f"| {source(rel, node.lineno)} `{node.name}` | `{cell(ast.unparse(d))}` | `{cell(ast.unparse(node.args))}` | {cell(ast.get_docstring(node) or '')} |\n"
    integrations += "\n## CLI command registrations\n\n| File / function | Registration | Arguments | Description |\n|---|---|---|---|\n"
    for rel in paths:
        if not rel.startswith("cli/testlookup_cli/") or not rel.endswith(".py"):
            continue
        for node in ast.walk(tree_for(ROOT / rel)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr in {"command", "callback"}
                    ):
                        integrations += f"| {source(rel, node.lineno)} `{node.name}` | `{cell(ast.unparse(d))}` | `{cell(ast.unparse(node.args))}` | {cell(ast.get_docstring(node) or '')} |\n"
    integrations += (
        "\n## Frontend route declarations\n\nSource: "
        + source("frontend/src/App.tsx")
        + "\n\n```tsx\n"
    )
    raw = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    integrations += (
        "\n".join(
            line.strip()
            for line in raw.splitlines()
            if re.search(r"path:|<Route .*path=", line)
        )
        + "\n```\n"
    )
    integrations += (
        "\nMCP registration totals: "
        + ", ".join(f"{n} {k}s" for k, n in mcp_counts.items())
        + ".\n"
    )
    write("client-surfaces.md", integrations)
    worker = header("Worker tasks, queues and schedules")
    worker += "Static declarations, not observations of running consumers. Deployment profiles override shard count and subscriptions; see [deployment](../operations/deployment.md). Every task below includes its decorator options and arguments; side effects and error handling remain in the linked implementation.\n\n"
    celery_tree = tree_for(ROOT / "backend/app/worker/celery_app.py")
    for node in ast.walk(celery_tree):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "celery_app.conf.update"
        ):
            for kw in node.keywords:
                if kw.arg in {
                    "task_routes",
                    "beat_schedule",
                    "broker_transport_options",
                    "task_time_limit",
                    "task_soft_time_limit",
                    "task_acks_late",
                    "worker_prefetch_multiplier",
                }:
                    worker += (
                        f"## {kw.arg}\n\n```python\n{ast.unparse(kw.value)}\n```\n\n"
                    )
    for rel in paths:
        if not rel.startswith("backend/app/worker/") or not rel.endswith(".py"):
            continue
        for node in ast.walk(tree_for(ROOT / rel)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [
                    ast.unparse(d)
                    for d in node.decorator_list
                    if isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "task"
                ]
                if decorators:
                    worker += (
                        f"## {node.name}\n\n"
                        + source(rel, node.lineno)
                        + "\n\n"
                        + (ast.get_docstring(node) or "")
                        + "\n\n```python\n"
                        + "\n".join(decorators)
                        + "\n"
                        + node.name
                        + "("
                        + ast.unparse(node.args)
                        + ")\n```\n\n"
                    )
    write("worker-operations.md", worker)
    return {
        "inventory_files": len(rows),
        "python_modules_parsed": module_count,
        "mcp": mcp_counts,
    }


def main() -> None:
    operations, paths, schemas = generate_api()
    tables = generate_models()
    counts = generate_static()
    counts.update(
        http_operations=operations,
        http_paths=paths,
        openapi_schemas=schemas,
        sqlalchemy_tables=tables,
    )
    write("inventory-counts.json", json.dumps(counts, indent=2, sort_keys=True))
    print(json.dumps(counts, indent=2))
    if ERRORS:
        print("Generated reference drift:\n" + "\n".join(ERRORS))
        raise SystemExit(1)
    print("Reference check passed." if CHECK else "References generated.")


if __name__ == "__main__":
    try:
        main()
    finally:
        os.chdir(_original_cwd)
        _import_directory.cleanup()
