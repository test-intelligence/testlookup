#!/usr/bin/env python3
"""Regenerate the source-derived reference under docs/reference/.

It writes the API reference (OpenAPI JSON, the endpoint index, one page per
API domain, the schema catalogue) and the operator references generated from
source: settings, migrations, the PostgreSQL data dictionary and worker tasks.
The application is imported without starting services: no lifespan, database
queries or worker dispatch.

Run with the backend requirements installed: python scripts/generate_reference.py
Use --check to detect drift without writing.
"""

from __future__ import annotations

import ast
import difflib
import inspect
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
GENERATED: set[str] = set()
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
                        if _kw.arg in {"alias", "validation_alias"}:
                            for _alias in ast.walk(_kw.value):
                                if isinstance(_alias, ast.Constant) and isinstance(
                                    _alias.value, str
                                ):
                                    os.environ.pop(_alias.value, None)
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
    GENERATED.add(name)
    path = OUT / name
    data = data.rstrip() + "\n"
    if CHECK:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing != data:
            ERRORS.append(str(path.relative_to(ROOT)))
            difference = difflib.unified_diff(
                existing.splitlines(keepends=True),
                data.splitlines(keepends=True),
                fromfile=str(path.relative_to(ROOT)),
                tofile="regenerated/" + name,
                n=2,
            )
            print("".join(difference)[:6000])
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8", newline="\n")


def candidate_outputs() -> list[Path]:
    """Files under the reference directory that Git sees: tracked, or untracked
    and not ignored. An ignored file there (a retired output kept locally, for
    example) is not part of the reference and is left alone."""
    listed = subprocess.check_output(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            OUT.relative_to(ROOT).as_posix(),
        ],
        cwd=ROOT,
    ).decode("utf-8")
    return sorted({ROOT / name for name in listed.split("\0") if name})


def reconcile_outputs() -> None:
    """Reject drift in the file set; prune only explicitly generator-owned files."""
    for path in candidate_outputs():
        if not path.is_file() or path.relative_to(OUT).as_posix() in GENERATED:
            continue
        if OUT.resolve() not in path.resolve().parents:
            ERRORS.append(f"Refusing output outside reference directory: {path}")
            continue
        owned = path.suffix == ".md" and (
            "Generated from tracked source by `scripts/generate_reference.py`."
            in path.read_text(encoding="utf-8", errors="replace")[:500]
        )
        if not CHECK and owned:
            path.unlink()
        else:
            ERRORS.append(f"Unexpected output: {path.relative_to(ROOT)}")


def verify_tracked_outputs() -> None:
    """A clean checkout must contain every generated reference, even on Windows."""
    tracked = set(
        subprocess.check_output(
            ["git", "ls-files", "-z", "--", "docs/reference"], cwd=ROOT
        )
        .decode("utf-8")
        .split("\0")
    )
    for name in sorted(GENERATED):
        relative = "docs/reference/" + name
        if relative not in tracked:
            ERRORS.append(
                f"Untracked generated output: {relative}; add it to Git before checking"
            )


def api_domain_filename(group: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")
    # The root .gitignore ignores AGENTS.md at every depth. On a case-insensitive
    # Git checkout that also ignores agents.md, which would leave CI without it.
    if slug == "agents":
        slug = "agent-operations"
    return f"api/{slug}.md"


def cell(value: object) -> str:
    return str(value).replace("|", "&#124;").replace("\n", "<br>").replace("\r", "")


def source(path: str, line: int | None = None) -> str:
    suffix = f"#L{line}" if line else ""
    return f"[{path}{':' + str(line) if line else ''}](../../{path}{suffix})"


def header(title: str) -> str:
    return f"# {title}\n\n[Documentation home](../README.md) · [Regeneration](../maintainers/maintenance.md)\n\nGenerated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../maintainers/verification.md).\n\n"


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
    from fastapi.routing import APIRoute

    spec = app.openapi()
    write(
        "openapi.json", json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False)
    )
    by_group: dict[str, list[str]] = {}
    index = header("REST endpoint index")
    index += "This indexes every OpenAPI HTTP operation. Exact parameters, body schemas and declared responses are in the linked domain pages and [OpenAPI JSON](openapi.json). Source descriptions are declarations; see [known summary-report behavior gaps](../pipelines/reporting.md#aggregation-and-evidence). Authentication dependencies and handler-raised errors supplement OpenAPI: absence of `security` does **not** imply anonymous access. See [authentication and errors](../api/README.md).\n\n"
    index += "| Method | Path | Summary | Reference |\n|---|---|---|---|\n"
    route_lookup = {
        (r.path, m.lower()): r
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
    }
    operations = 0
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
            filename = api_domain_filename(group)
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
            .replace("../maintainers/", "../../maintainers/")
            + "\n".join(sections),
        )
    index += f"\nTotal: **{operations} HTTP operations**, **{len(spec['paths'])} paths**, **{len(by_group)} domain pages**.\n"
    write("api-index.md", index)
    schemas = header("HTTP request and response schemas")
    schemas += "These are the full generated JSON Schema definitions, including required fields, defaults, enums, nested references and declared constraints. Custom validators and cross-field checks live in the Pydantic models under `backend/app/models/`; JSON Schema does not encode every service-level rule.\n\n"
    for name, schema in sorted(spec["components"]["schemas"].items()):
        schemas += f"## {name}\n\n" + code_json(schema) + "\n"
    write("schemas.md", schemas)
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
    config = header("Environment settings reference")
    config += "Source declarations only; no `.env` or running secrets are read. Compose/Helm/Kustomize can override these defaults. `Field` aliases and constraints are retained. What each setting does is explained in the [configuration reference](../operations/configuration.md); production requirements are in [deployment](../operations/deployment.md).\n\n| Setting | Type | Source default/constraints |\n|---|---|---|\n"
    for node in ast.walk(tree_for(ROOT / "backend/app/core/config.py")):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for n in node.body:
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                    config += f"| `{n.target.id}` | `{cell(ast.unparse(n.annotation))}` | `{cell(ast.unparse(n.value) if n.value else 'required')}` |\n"
    write("configuration.md", config)

    migrations = header("Database migration inventory")
    migrations += "Alembic revisions extracted without executing migrations. An inventory is not evidence that a live database is upgraded. Run `alembic current` and `alembic heads` in the target deployment.\n\n| File | Revision | Down revision | Purpose |\n|---|---|---|---|\n"
    migration_files = sorted(
        p
        for p in tracked
        if p.startswith("backend/migrations/versions/") and p.endswith(".py")
    )
    for rel in migration_files:
        tree = tree_for(ROOT / rel)
        description = (ast.get_docstring(tree) or "").split("\n")[0]
        vals = {}
        for n in tree.body:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id in {"revision", "down_revision"}:
                        vals[t.id] = ast.unparse(n.value)
            elif (
                isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name)
                and n.target.id in {"revision", "down_revision"}
            ):
                vals[n.target.id] = ast.unparse(n.value)
        migrations += f"| {source(rel)} | `{cell(vals.get('revision', ''))}` | `{cell(vals.get('down_revision', ''))}` | {cell(description)} |\n"
    write("migrations.md", migrations)

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
    task_count = 0
    for rel in tracked:
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
                    task_count += 1
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
    return {"migrations": len(migration_files), "worker_tasks": task_count}


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
    reconcile_outputs()
    if CHECK:
        verify_tracked_outputs()
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
