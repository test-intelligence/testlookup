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


def _number_array(name: str) -> tuple[int, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {TYPES_TS.name}"
    return tuple(int(n) for n in re.findall(r"\d+", match.group(1)))


@pytest.mark.parametrize(
    ("name", "server"),
    [("JOB_KINDS", v.JOB_KINDS), ("JOB_STATUSES", v.JOB_STATUSES), ("TERMINAL_STATUSES", v.TERMINAL_STATUSES),
     ("JOB_PHASES", v.JOB_PHASES)],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


def test_the_history_windows_equal_the_server():
    assert _number_array("HISTORY_WINDOWS_DAYS") == v.HISTORY_WINDOWS_DAYS
    assert v.HISTORY_DEFAULT_DAYS in v.HISTORY_WINDOWS_DAYS


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
        ("HISTORY_DEFAULT_DAYS", v.HISTORY_DEFAULT_DAYS),
        ("HISTORY_MAX_RUNS", v.HISTORY_MAX_RUNS),
        ("IMPORT_PART_MAX_BYTES", v.IMPORT_PART_MAX_BYTES),
        ("IMPORT_MAX_PARTS", v.IMPORT_MAX_PARTS),
        ("IMPORT_ARCHIVE_MAX_BYTES", v.IMPORT_ARCHIVE_MAX_BYTES),
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


# ── W30 ADM-05 phase 2: the history caps agree with every file they promise something about ──

CLI_PACK_VERIFY = REPO / "cli" / "testlookup_cli" / "pack_verify.py"
_MIB = 1024 * 1024


def _cli_manifest_cap() -> int:
    match = re.search(r"^MAX_MANIFEST_BYTES\s*=\s*([\d_]+)\s*$", CLI_PACK_VERIFY.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"MAX_MANIFEST_BYTES is not a number literal in {CLI_PACK_VERIFY.name}"
    return int(match.group(1).replace("_", ""))


def test_the_history_vocabulary_agrees_with_the_enums_and_the_retention_pause():
    from app.models.enums import PipelineRunStatus

    assert set(v.HISTORY_RUN_STATUSES) == {s.value for s in pg.LaunchStatus} - {"IN_PROGRESS"}
    assert set(v.TERMINAL_PIPELINE_STATUSES) < {s.value for s in PipelineRunStatus}
    assert v.IMPORTED_PURGE_PAUSE_DAYS == retention_vocab.PAUSE_DAYS_MAX
    assert v.CATALOG_SCHEMA_VERSION in v.SUPPORTED_SCHEMA_VERSIONS and v.SCHEMA_VERSION == max(v.SUPPORTED_SCHEMA_VERSIONS)
    assert v.JOB_STATUSES == ("uploading", "queued", "running", "succeeded", "failed", "expired")
    assert {"ingest_machinery", "external_tracker", "run_history"} <= set(v.EXCLUSION_REASONS)


def test_every_history_member_fits_both_verifiers_manifest_cap():
    from app.services import compliance_pack_verify

    assert compliance_pack_verify.MAX_MANIFEST_BYTES == _cli_manifest_cap()
    assert v.HISTORY_MAX_MEMBERS * 190 + 200_000 < compliance_pack_verify.MAX_MANIFEST_BYTES
    assert v.IMPORT_HISTORY_MAX_ENTRIES == v.HISTORY_MAX_MEMBERS


def test_a_part_fits_under_every_proxy_and_the_parts_make_the_archive_cap():
    caps = re.findall(r"client_max_body_size\s+(\d+)m\s*;", NGINX.read_text(encoding="utf-8"))
    assert caps, f"no client_max_body_size in {NGINX.name}"
    assert v.IMPORT_PART_MAX_BYTES + 5 * _MIB <= min(int(c) for c in caps) * _MIB   # nginx's multipart margin
    assert v.HISTORY_EXPORT_MAX_BYTES == v.IMPORT_ARCHIVE_MAX_BYTES == v.IMPORT_MAX_PARTS * v.IMPORT_PART_MAX_BYTES
    assert v.OBJECT_MAX_BYTES == v.IMPORT_HISTORY_MAX_ENTRY_UNCOMPRESSED
    assert v.MONGO_DOC_MAX_BYTES == v.ROW_BYTES_MAX
    assert v.IMPORT_HISTORY_MAX_TOTAL_UNCOMPRESSED >= v.IMPORT_ARCHIVE_MAX_BYTES
    assert v.HISTORY_TIME_LIMIT_SECONDS - 300 > 0 and v.HISTORY_JOB_STALE_MINUTES * 60 > v.HISTORY_TIME_LIMIT_SECONDS
    assert v.STORAGE_PREFIX_IMPORT_PARTS.startswith(v.STORAGE_PREFIX_IMPORT) and v.STORAGE_PREFIX_IMPORT_PARTS.endswith("/")


def test_the_history_sentences_state_the_caps():
    assert "2 GB" in v.ERROR_SENTENCES["history_too_large"] and v.HISTORY_EXPORT_MAX_BYTES == 2 * 1024 * _MIB
    assert "2 GB" in v.ERROR_SENTENCES["upload_too_large_history"] and "2 GB" in v.UI_SENTENCES["estimate_over"]
    assert "32 MB" in v.ERROR_SENTENCES["part_too_large"] and v.IMPORT_PART_MAX_BYTES == 32 * _MIB
    assert f"1 to {v.IMPORT_MAX_PARTS} parts" in v.ERROR_SENTENCES["upload_parts_invalid"]
    assert f"between 1 and {v.HISTORY_MAX_RUNS}" in v.ERROR_SENTENCES["max_runs_invalid"]
    assert "256 MB" in v.SKIP_REASONS["too_large"] and v.OBJECT_MAX_BYTES == 256 * _MIB
    windows = ", ".join(str(d) for d in v.HISTORY_WINDOWS_DAYS[:-1]) + f" or {v.HISTORY_WINDOWS_DAYS[-1]} days"
    assert windows in v.ERROR_SENTENCES["history_window_invalid"]
    assert f"last {v.DIGEST_LOOKBACK_DAYS} days" in v.UI_SENTENCES["recent_runs_note"]
    for phase in v.JOB_PHASES:
        assert v.UI_SENTENCES[f"phase_{phase}"], phase


def test_the_schemas_use_the_vocabulary():
    assert schemas.ProjectExportRequest().history_days == v.HISTORY_DEFAULT_DAYS
    assert schemas.ProjectExportRequest().max_runs == v.HISTORY_MAX_RUNS
    bounds = {type(m).__name__: m for m in schemas.ProjectImportUploadStart.model_fields["bytes"].metadata}
    assert bounds["Le"].le == v.IMPORT_ARCHIVE_MAX_BYTES
    bounds = {type(m).__name__: m for m in schemas.ProjectImportUploadStart.model_fields["parts"].metadata}
    assert bounds["Le"].le == v.IMPORT_MAX_PARTS


def test_the_vocabulary_imports_nothing_from_the_application():
    tree = ast.parse(VOCAB_PY.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert modules == ["__future__"], f"project_export_vocab.py is constants only; it imports {modules}"
