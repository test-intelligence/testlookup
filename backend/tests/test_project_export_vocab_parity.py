"""W24 ADM-05: the project export / import vocabulary the UI renders is the server's.

``frontend/src/types/projectData.ts`` mirrors ``services/project_export_vocab.py``
constant for constant: the job kinds / statuses, the upload cap, the export TTL,
the list limit, the error sentences, the UI sentences and the skip reasons.
Read as TEXT (no Node in the backend suite), like the other frontend-parity
pins (the W22b regex readers). The vocabulary's own cross-module facts are
pinned here too: the redaction marker, the retention TTL, the cadence pause
cap, the nginx body cap and the project-name bounds.
"""
from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

from app.models import schemas
from app.models import postgres as pg
from app.services import project_export_vocab as v
from app.services import redaction_service, retention_vocab, run_cadence_vocab

REPO = Path(__file__).resolve().parents[2]
TYPES_TS = REPO / "frontend" / "src" / "types" / "projectData.ts"
NGINX = REPO / "frontend" / "nginx.conf.template"
VOCAB_PY = REPO / "backend" / "app" / "services" / "project_export_vocab.py"
_STRING = r"""(?:'([^']*)'|"([^"]*)")"""


def _source() -> str:
    if not TYPES_TS.exists():
        pytest.fail(f"{TYPES_TS} is missing: the parity this test pins has nothing to read")
    return TYPES_TS.read_text(encoding="utf-8")


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {TYPES_TS.name}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _number(name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {TYPES_TS.name}"
    return int(match.group(1))


def _record(name: str) -> dict[str, str]:
    # Values carry `{name}` placeholders, so the literal ends at the first column-0 `} as const`.
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _source(), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {TYPES_TS.name}"
    body = match.group(1)
    found = re.findall(rf"^\s*'?([A-Za-z_]+)'?:\s*{_STRING},?\s*$", body, re.MULTILINE)
    entries = {key: single or double for key, single, double in found}
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


@pytest.mark.parametrize(
    ("name", "server"),
    [("JOB_KINDS", v.JOB_KINDS), ("JOB_STATUSES", v.JOB_STATUSES), ("TERMINAL_STATUSES", v.TERMINAL_STATUSES)],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


def test_active_and_terminal_statuses_partition_the_statuses():
    active = _array("ACTIVE_STATUSES")
    assert set(active) | set(v.TERMINAL_STATUSES) == set(v.JOB_STATUSES)
    assert not set(active) & set(v.TERMINAL_STATUSES)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("IMPORT_UPLOAD_MAX_BYTES", v.IMPORT_UPLOAD_MAX_BYTES),
        ("EXPORT_TTL_HOURS", v.EXPORT_TTL_HOURS),
        ("JOBS_LIST_LIMIT", v.JOBS_LIST_LIMIT),
    ],
)
def test_the_frontend_numbers_equal_the_server(name, server):
    assert _number(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [("ERROR_SENTENCES", v.ERROR_SENTENCES), ("UI_SENTENCES", v.UI_SENTENCES), ("SKIP_REASONS", v.SKIP_REASONS)],
)
def test_the_frontend_records_equal_the_server(name, server):
    assert _record(name) == server


def test_the_vocabulary_agrees_with_the_modules_it_copies():
    assert v.REDACTED == redaction_service.REDACTED
    assert v.EXPORT_TTL_HOURS == retention_vocab.EXPORT_COLLECT_TTL_HOURS_DEFAULT
    assert v.IMPORTED_PAUSE_DAYS == run_cadence_vocab.PAUSE_MAX_DAYS
    assert len(v.IMPORTED_PAUSE_REASON) <= run_cadence_vocab.PAUSE_REASON_MAX
    assert v.ERROR_SENTENCES["archive_unreadable"] == retention_vocab.EXPORT_ERROR_SENTENCES["archive_unreadable"]
    assert set(v.REVIEW_RESET_STATES) <= {state.value for state in pg.TestCaseLifecycleState}
    assert v.SCHEMA_VERSION in v.SUPPORTED_SCHEMA_VERSIONS


def test_the_upload_cap_fits_under_every_proxy():
    assert v.IMPORT_UPLOAD_MAX_BYTES == v.EXPORT_MAX_BYTES   # every archive this deployment writes can be imported
    caps = re.findall(r"client_max_body_size\s+(\d+)m\s*;", NGINX.read_text(encoding="utf-8"))
    assert caps, f"no client_max_body_size in {NGINX.name}"
    assert v.IMPORT_UPLOAD_MAX_BYTES <= min(int(c) for c in caps) * 1024 * 1024
    assert "50 MB" in v.ERROR_SENTENCES["upload_too_large"] and "50 MB" in v.ERROR_SENTENCES["export_too_large"]
    assert v.IMPORT_UPLOAD_MAX_BYTES == 50 * 1024 * 1024


def test_the_name_sentence_matches_project_create():
    constraints = {type(m).__name__: m for m in schemas.ProjectCreate.model_fields["name"].metadata}
    low = constraints["MinLen"].min_length
    high = constraints["MaxLen"].max_length
    assert f"{low} to {high} characters" in v.ERROR_SENTENCES["name_invalid"]


@pytest.mark.parametrize("table", ["ERROR_SENTENCES", "UI_SENTENCES", "SKIP_REASONS"])
def test_every_placeholder_is_well_formed(table):
    for key, sentence in getattr(v, table).items():
        fields = [name for _, name, spec, conv in string.Formatter().parse(sentence) if name is not None]
        for name in fields:
            assert name.isidentifier(), f"{table}[{key!r}] has a malformed placeholder {{{name}}}"
        filled = sentence.format(**{name: "Xq" for name in fields})
        assert "{" not in filled and "}" not in filled, f"{table}[{key!r}] leaves a brace after formatting"
        assert sentence.strip() == sentence and sentence, f"{table}[{key!r}] is empty or padded"


def test_the_vocabulary_imports_nothing_from_the_application():
    tree = ast.parse(VOCAB_PY.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert modules == ["__future__"], f"project_export_vocab.py is constants only; it imports {modules}"
