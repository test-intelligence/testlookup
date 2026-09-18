#!/usr/bin/env python3
"""Check handoff links, generated coverage and representative API payloads."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
sys.dont_write_bytecode = True
errors: list[str] = []
pages = [ROOT / "README.md", DOCS / "README.md"]
for folder in (
    "product",
    "architecture",
    "pipelines",
    "api",
    "operations",
    "handoff",
    "reference",
    "wiki",
):
    pages.extend((DOCS / folder).rglob("*.md"))
pages.extend((DOCS / "reviews").glob("2026-09-18-*.md"))

links = 0
for page in sorted(set(pages)):
    raw = page.read_text(encoding="utf-8")
    prose = re.sub(r"^```[^\n]*\n.*?^```\s*$", "", raw, flags=re.MULTILINE | re.DOTALL)
    for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", prose):
        target = target.strip().strip("<>")
        split = urlsplit(target)
        if split.scheme or split.netloc or not split.path:
            continue
        links += 1
        resolved = (page.parent / unquote(split.path)).resolve()
        if not resolved.exists():
            errors.append(f"{page.relative_to(ROOT)}: missing link {target}")
        if (
            split.fragment.startswith("L")
            and split.fragment[1:].isdigit()
            and resolved.is_file()
        ):
            line = int(split.fragment[1:])
            if line < 1 or line > len(
                resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            ):
                errors.append(
                    f"{page.relative_to(ROOT)}: source line out of range {target}"
                )

spec = json.loads((DOCS / "reference/openapi.json").read_text(encoding="utf-8"))
counts = json.loads(
    (DOCS / "reference/inventory-counts.json").read_text(encoding="utf-8")
)


def refs(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str) and child.startswith("#/"):
                current = spec
                try:
                    for part in child[2:].split("/"):
                        current = current[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    errors.append(f"Unresolved OpenAPI ref: {child}")
            else:
                refs(child)
    elif isinstance(value, list):
        for child in value:
            refs(child)


refs(spec)
verbs = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
operations = {
    (method.upper(), path)
    for path, methods in spec["paths"].items()
    for method in methods
    if method in verbs
}
index = (DOCS / "reference/api-index.md").read_text(encoding="utf-8")
documented = set(
    re.findall(
        r"^\| (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE) \| `([^`]+)`",
        index,
        re.MULTILINE,
    )
)
domain_operations = set()
for domain in (DOCS / "reference/api").glob("*.md"):
    domain_operations.update(
        re.findall(
            r"^## (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE) `([^`]+)`",
            domain.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
for name, actual, expected in (
    ("endpoint index", documented, operations),
    ("domain operation coverage", domain_operations, operations),
    ("HTTP operation count", len(operations), counts["http_operations"]),
    ("HTTP path count", len(spec["paths"]), counts["http_paths"]),
    ("schema count", len(spec["components"]["schemas"]), counts["openapi_schemas"]),
    (
        "table count",
        len(
            re.findall(
                r"^## ",
                (DOCS / "reference/data-dictionary.md").read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        ),
        counts["sqlalchemy_tables"],
    ),
):
    if actual != expected:
        errors.append(f"{name} mismatch: {actual} != {expected}")

# Validate a representative batch against the real source model, without services.
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update(
    DATABASE_URL="postgresql+asyncpg://docs:docs@db.invalid:5432/docs",
    TESTING="true",
    OTEL_ENABLED="false",
    METRICS_ENABLED="false",
)
from app.models.schemas import IngestPayload, IngestResponse
from pydantic import ValidationError

payload = {
    "project_id": "00000000-0000-0000-0000-000000000001",
    "build_number": "docs-example-1",
    "framework": "pytest",
    "environment": "staging",
    "results": [
        {
            "test_name": "test_checkout",
            "status": "FAILED",
            "duration_ms": 120,
            "error_message": "Expected 200, received 500",
        }
    ],
}
IngestPayload.model_validate(payload)
api_guide = (DOCS / "api/README.md").read_text(encoding="utf-8")
response_example = re.search(r"```json\n(.*?)\n```", api_guide, re.DOTALL)
if response_example is None:
    errors.append("API guide lacks accepted-ingest JSON example")
else:
    IngestResponse.model_validate_json(response_example.group(1))
try:
    IngestPayload.model_validate(
        {**payload, "results": [{**payload["results"][0], "status": "UNKNOWN"}]}
    )
    errors.append("Documented JSON input status boundary drifted: UNKNOWN accepted")
except ValidationError:
    pass
for method, path in [
    ("POST", "/api/v1/ingest"),
    ("POST", "/api/v1/ingest/file"),
    ("GET", "/api/v1/ingest/uploads/{task_id}"),
    ("GET", "/api/v1/runs/{run_id}"),
    ("POST", "/api/v1/auth/login"),
]:
    if (method, path) not in operations:
        errors.append(f"Documented operation missing: {method} {path}")

print(
    f"Checked {len(set(pages))} Markdown pages, {links} local links, {len(operations)} operations, {counts['openapi_schemas']} schemas, {counts['sqlalchemy_tables']} tables, and ingestion examples."
)
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print("Handoff documentation checks passed.")
