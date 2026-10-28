"""W22b: the export / schedule / instance-default vocabulary the UI renders is the server's.

``frontend/src/types/retentionExport.ts`` mirrors ``services/retention_vocab.py``
constant for constant (tiers, targets, caps, the not-a-backup sentence and the
export notes, the export refusal and job-error sentences, the inheritable
fields and their sources, the window / pause bounds, the enable phrases, the
policy refusal sentences and the marker / schedule copy), and the
``DeletionJobStatus`` union of ``frontend/src/types/retention.ts`` equals
``deletion_job_service.REACHABLE_STATUSES``. Read as TEXT (no Node in the
backend suite), like W22a's ``test_retention_vocab_frontend_parity.py``. A
drifted copy would show one sentence on the client and refuse with another on
the server.

The server-side copies agree with each other too: ``EXPORT_TIERS`` is
``compliance_pack_service._EXPORT_TIERS`` and ``EXPORT_MAX_RUNS`` is
``MAX_EXPORT_RUNS`` (S6a's scope), and the schema / settings defaults read the
vocabulary.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import schemas
from app.services import retention_vocab as v

FRONTEND_TYPES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types"
EXPORT_TS = FRONTEND_TYPES / "retentionExport.ts"
RETENTION_TS = FRONTEND_TYPES / "retention.ts"

# A single- or double-quoted TS string literal (a value carrying an apostrophe is double-quoted).
_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _source() -> str:
    return _read(EXPORT_TS)


def _strings(text: str) -> tuple[str, ...]:
    return tuple(a or b for a, b in re.findall(_STR, text))


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {EXPORT_TS.name}"
    return _strings(match.group(1))


def _number(name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {EXPORT_TS.name}"
    return int(match.group(1))


def _string(name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*{_STR}\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a one-piece string literal in {EXPORT_TS.name}"
    return match.group(1) or match.group(2) or ""


def _record(name: str) -> dict[str, str]:
    # The values carry `{placeholder}`s, so the literal ends at the first column-0 `} as const`.
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _source(), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {EXPORT_TS.name}"
    body = match.group(1)
    entries = {
        key: a or b
        for key, a, b in re.findall(rf"^\s*'?([A-Za-z_.]+)'?:\s*{_STR},?\s*$", body, re.MULTILINE)
    }
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


def _deletion_job_status_union() -> tuple[str, ...]:
    match = re.search(r"export type DeletionJobStatus\s*=([^;]*?)(?:\n\s*\n|\n/\*\*|\nexport )", _read(RETENTION_TS))
    assert match, f"DeletionJobStatus is not declared as a string-literal union in {RETENTION_TS.name}"
    return _strings(match.group(1))


# ── the frontend copies ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("EXPORT_TIERS", v.EXPORT_TIERS),
        ("EXPORT_TARGETS", v.EXPORT_TARGETS),
        ("INHERITABLE_FIELDS", v.INHERITABLE_FIELDS),
        ("FIELD_SOURCES", v.FIELD_SOURCES),
    ],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("EXPORT_MAX_RUNS", v.EXPORT_MAX_RUNS),
        ("EXPORT_MAX_BYTES_DEFAULT", v.EXPORT_MAX_BYTES_DEFAULT),
        ("EXPORT_COLLECT_TTL_HOURS_DEFAULT", v.EXPORT_COLLECT_TTL_HOURS_DEFAULT),
        ("PURGE_WINDOW_DEFAULT_HOUR", v.PURGE_WINDOW_DEFAULT_HOUR),
        ("PURGE_WINDOW_MIN", v.PURGE_WINDOW_MIN),
        ("PURGE_WINDOW_MAX", v.PURGE_WINDOW_MAX),
        ("PAUSE_DAYS_DEFAULT", v.PAUSE_DAYS_DEFAULT),
        ("PAUSE_DAYS_MIN", v.PAUSE_DAYS_MIN),
        ("PAUSE_DAYS_MAX", v.PAUSE_DAYS_MAX),
        ("INSTANCE_PREVIEW_MAX_ITEMS", v.INSTANCE_PREVIEW_MAX_ITEMS),
    ],
)
def test_the_frontend_numbers_equal_the_server(name, server):
    assert _number(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("EXPORT_TIER_DEFAULT", v.EXPORT_TIER_DEFAULT),
        ("ARCHIVE_NOT_BACKUP", v.ARCHIVE_NOT_BACKUP),
        ("EXPORT_JOB_EXPIRED_ONE", v.EXPORT_JOB_EXPIRED_ONE),
        ("EXPORT_FULL_WARNING", v.EXPORT_FULL_WARNING),
        ("EXPORT_RETAIN_NOTE", v.EXPORT_RETAIN_NOTE),
        ("EXPORT_COLLECT_NOTE", v.EXPORT_COLLECT_NOTE),
        ("INSTANCE_ENABLE_PHRASE", v.INSTANCE_ENABLE_PHRASE),
        ("INSTANCE_ENABLE_PHRASE_ONE", v.INSTANCE_ENABLE_PHRASE_ONE),
        ("INHERITED_LABEL", v.INHERITED_LABEL),
        ("DEFAULT_LABEL", v.DEFAULT_LABEL),
        ("PAUSED_SENTENCE", v.PAUSED_SENTENCE),
        ("NEXT_PURGE_SENTENCE", v.NEXT_PURGE_SENTENCE),
    ],
)
def test_the_frontend_sentences_equal_the_server(name, server):
    assert _string(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("EXPORT_ERROR_SENTENCES", v.EXPORT_ERROR_SENTENCES),
        ("EXPORT_JOB_ERRORS", v.EXPORT_JOB_ERRORS),
        ("POLICY_ERROR_SENTENCES", v.POLICY_ERROR_SENTENCES),
    ],
)
def test_the_frontend_records_equal_the_server(name, server):
    assert _record(name) == server


def test_the_deletion_job_status_union_is_every_reachable_status():
    """Waiting for G-B until ``deletion_job_service`` declares awaiting_collection / expired."""
    from app.services import deletion_job_service as djs

    union = _deletion_job_status_union()
    assert len(union) == len(set(union)), union
    assert set(union) == set(djs.REACHABLE_STATUSES)
    assert {"awaiting_collection", "expired"} <= set(union)
    assert "expired" in djs.TERMINAL_STATUSES and "awaiting_collection" not in djs.TERMINAL_STATUSES


# ── the server copies agree with each other ──────────────────────────────────

def test_the_server_copies_agree_with_each_other():
    from app.core.config import Settings
    from app.services import compliance_pack_service as cps

    assert v.EXPORT_TIERS == cps._EXPORT_TIERS
    assert v.EXPORT_MAX_RUNS == cps.MAX_EXPORT_RUNS
    assert v.EXPORT_TIER_DEFAULT in v.EXPORT_TIERS
    assert v.EXPORT_ERROR_SENTENCES["unknown_tier"].endswith(", ".join(v.EXPORT_TIERS) + ".")
    assert v.EXPORT_ERROR_SENTENCES["unknown_target"].endswith(", ".join(v.EXPORT_TARGETS) + ".")
    assert str(v.EXPORT_MAX_RUNS) in v.EXPORT_ERROR_SENTENCES["too_many_runs"]
    assert v.POLICY_ERROR_SENTENCES["unknown_inherit_field"].endswith(", ".join(v.INHERITABLE_FIELDS) + ".")
    assert all("nothing was deleted" in sentence for sentence in v.EXPORT_JOB_ERRORS.values())
    assert v.INSTANCE_ENABLE_PHRASE.format(count=1) != v.INSTANCE_ENABLE_PHRASE_ONE   # the singular is its own copy
    assert v.EXPORT_MAX_BYTES_DEFAULT == 512 * 1024 * 1024
    assert (v.PURGE_WINDOW_MIN, v.PURGE_WINDOW_MAX) == (0, 23)
    # The defaults that read the vocabulary.
    assert Settings.model_fields["RETENTION_EXPORT_MAX_BYTES"].default == v.EXPORT_MAX_BYTES_DEFAULT
    assert Settings.model_fields["RETENTION_EXPORT_COLLECT_TTL_HOURS"].default == v.EXPORT_COLLECT_TTL_HOURS_DEFAULT
    assert schemas.ExportEstimate.model_fields["note"].default == v.ARCHIVE_NOT_BACKUP
    assert schemas.RetentionPolicyRead.model_fields["purge_window_start_utc"].default == v.PURGE_WINDOW_DEFAULT_HOUR
    write = schemas.RetentionPolicyWrite.model_fields
    assert write["inherit"].metadata[0].max_length == len(v.INHERITABLE_FIELDS)
