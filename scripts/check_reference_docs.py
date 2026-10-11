#!/usr/bin/env python3
"""Check documentation links, the generated API reference and representative API payloads."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
sys.dont_write_bytecode = True
errors: list[str] = []
pages = [
    ROOT / name
    for name in (
        "README.md",
        "GETTING_STARTED.md",
        "ARCHITECTURE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "GOVERNANCE.md",
        "MAINTAINERS.md",
        "UPGRADING.md",
        "RELEASING.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/README.md",
    )
    if (ROOT / name).is_file()
]
for folder in (
    "product",
    "architecture",
    "pipelines",
    "api",
    "operations",
    "maintainers",
    "reference",
):
    pages.extend((DOCS / folder).rglob("*.md"))
# The operator guide and the component READMEs ship too.
pages.extend((ROOT / "user-guide").glob("*.md"))
for readme in ("k8s/README.md", "cli/README.md", "mcp/README.md", "samples/README.md"):
    if (ROOT / readme).is_file():
        pages.append(ROOT / readme)
pages.extend((ROOT / "client").rglob("README.md"))


def git_visible(paths: list[Path]) -> list[Path]:
    """Only files Git sees (tracked, or untracked and not ignored). An ignored
    file on a maintainer's disk is absent from every clean checkout."""
    listed = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).decode("utf-8")
    visible = {(ROOT / name).resolve() for name in listed.split("\0") if name}
    return [path for path in paths if path.resolve() in visible]


pages = git_visible(pages)


def prose_only(raw: str) -> str:
    return re.sub(r"^```[^\n]*\n.*?^```\s*$", "", raw, flags=re.MULTILINE | re.DOTALL)


@cache
def markdown_anchors(path: Path) -> set[str]:
    """ATX heading slugs used by this suite, plus explicit HTML anchors."""
    prose = prose_only(path.read_text(encoding="utf-8"))
    anchors = set(re.findall(r'(?:id|name)=["\']([^"\']+)', prose))
    for heading in re.findall(r"^#{1,6}\s+(.+)", prose, re.MULTILINE):
        heading = re.sub(r"<[^>]+>", "", heading).strip().rstrip("#").strip().lower()
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        slug = "".join(
            c for c in heading if c in " _-" or unicodedata.category(c)[0] in "LN"
        ).replace(" ", "-")
        candidate, duplicate = slug, 0
        while candidate in anchors:
            duplicate += 1
            candidate = f"{slug}-{duplicate}"
        anchors.add(candidate)
    return anchors


links = 0
for page in sorted(set(pages)):
    raw = page.read_text(encoding="utf-8")
    prose = prose_only(raw)
    for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", prose):
        target = target.strip().strip("<>")
        split = urlsplit(target)
        if split.scheme or split.netloc:
            continue
        links += 1
        resolved = (
            (page.parent / unquote(split.path)).resolve()
            if split.path
            else page.resolve()
        )
        if not resolved.exists():
            errors.append(f"{page.relative_to(ROOT)}: missing link {target}")
        if (
            resolved.is_file()
            and resolved.suffix == ".md"
            and split.fragment
            and unquote(split.fragment) not in markdown_anchors(resolved)
        ):
            errors.append(f"{page.relative_to(ROOT)}: missing heading {target}")
        if (
            resolved.suffix != ".md"
            and split.fragment.startswith("L")
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
domain_operations = Counter()
for domain in git_visible(list((DOCS / "reference/api").glob("*.md"))):
    domain_operations.update(
        re.findall(
            r"^## (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE) `([^`]+)`",
            domain.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
dictionary = (DOCS / "reference/data-dictionary.md").read_text(encoding="utf-8")
table_sections = re.findall(r"^## (\S+)", dictionary, re.MULTILINE)
table_index = re.findall(r"^\| \[([^\]]+)\]\(#", dictionary, re.MULTILINE)
schemas_page = (DOCS / "reference/schemas.md").read_text(encoding="utf-8")
schema_sections = set(re.findall(r"^## (\S+)", schemas_page, re.MULTILINE))
for name, actual, expected in (
    ("endpoint index", documented, operations),
    ("domain operation coverage", set(domain_operations), operations),
    ("domain operation multiplicity", set(domain_operations.values()), {1}),
    ("schema catalogue", schema_sections, set(spec["components"]["schemas"])),
    ("data dictionary index", table_index, table_sections),
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
# Import from an empty directory so local .env files cannot affect checks.
previous_cwd = Path.cwd()
with tempfile.TemporaryDirectory(prefix="testlookup-docs-check-") as directory:
    try:
        os.chdir(directory)
        from app.models.schemas import IngestPayload, IngestResponse
        from pydantic import ValidationError
    finally:
        os.chdir(previous_cwd)

api_guide = (DOCS / "api/README.md").read_text(encoding="utf-8")
request_example = re.search(
    r"--data-binary @- <<JSON\n(.*?)\nJSON", api_guide, re.DOTALL
)
if request_example is None:
    raise SystemExit("API guide lacks the ingest request heredoc")
payload = json.loads(
    request_example.group(1).replace(
        "$TL_PROJECT", "00000000-0000-0000-0000-000000000001"
    )
)
IngestPayload.model_validate(payload)
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
    f"Checked {len(set(pages))} Markdown pages, {links} local links, {len(operations)} operations, {len(spec['components']['schemas'])} schemas, {len(table_sections)} tables, and ingestion examples."
)
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print("Reference documentation checks passed.")
