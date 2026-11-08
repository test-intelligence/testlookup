"""W31 STRAT-12: the subject-rights vocabulary the UI renders is the server's.

``frontend/src/types/subjectRights.ts`` mirrors ``services/subject_rights_vocab.py``
constant for constant: the job kinds / statuses / phases, the erase actions,
the erased display / pseudonym prefix, the export TTL, the error sentences and
the UI sentences. Read as TEXT (no Node in the backend suite), the W24 regex
reader. The four values the vocabulary copies from ``project_export_vocab`` are
asserted equal here (the vocabulary itself imports nothing).
"""
from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

from app.services import project_export_vocab, retention_vocab
from app.services import subject_rights_vocab as v

REPO = Path(__file__).resolve().parents[2]
TYPES_TS = REPO / "frontend" / "src" / "types" / "subjectRights.ts"
VOCAB_PY = REPO / "backend" / "app" / "services" / "subject_rights_vocab.py"
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


def _text(name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*{_STRING}\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a string literal in {TYPES_TS.name}"
    return match.group(1) if match.group(1) is not None else match.group(2)


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
    [
        ("JOB_KINDS", v.JOB_KINDS),
        ("JOB_STATUSES", v.JOB_STATUSES),
        ("TERMINAL_STATUSES", v.TERMINAL_STATUSES),
        ("ACTIVE_STATUSES", v.LIVE_STATUSES),
        ("ERASURE_PHASES", v.ERASURE_PHASES),
        ("REQUEST_SOURCES", v.REQUEST_SOURCES),
        ("ERASE_ACTIONS", v.ERASE_ACTIONS),
        ("AUDIT_ACTIONS", v.AUDIT_ACTIONS),
        # W38 (W31 F-1, 2026-11-07): the erasure request's statuses and decline codes
        ("ERASURE_REQUEST_STATUSES", v.ERASURE_REQUEST_STATUSES),
        ("DECLINE_CODES", v.DECLINE_CODES),
    ],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


def test_live_and_terminal_statuses_partition_the_statuses():
    assert set(v.LIVE_STATUSES) | set(v.TERMINAL_STATUSES) == set(v.JOB_STATUSES)
    assert not set(v.LIVE_STATUSES) & set(v.TERMINAL_STATUSES)
    assert v.ERASURE_PHASES[-1] == "done" and v.ERASURE_PHASES[0] == "lockout"


@pytest.mark.parametrize(
    ("name", "server"),
    [("ERASED_DISPLAY", v.ERASED_DISPLAY), ("PSEUDONYM_PREFIX", v.PSEUDONYM_PREFIX),
     ("ERASED_EMAIL_DOMAIN", v.ERASED_EMAIL_DOMAIN)],
)
def test_the_frontend_strings_equal_the_server(name, server):
    assert _text(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [("EXPORT_TTL_HOURS", v.EXPORT_TTL_HOURS), ("JOBS_LIST_LIMIT", v.JOBS_LIST_LIMIT)],
)
def test_the_frontend_numbers_equal_the_server(name, server):
    assert _number(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [("ERROR_SENTENCES", v.ERROR_SENTENCES), ("UI_SENTENCES", v.UI_SENTENCES),
     ("DECLINE_SENTENCES", v.DECLINE_SENTENCES)],   # W38 2026-11-07
)
def test_the_frontend_records_equal_the_server(name, server):
    assert _record(name) == server


def test_the_erasure_request_vocabulary_is_closed():
    # W38 (W31 F-1): every decline code has the sentence the person reads; the decided statuses are the rest.
    assert tuple(v.DECLINE_SENTENCES) == v.DECLINE_CODES
    assert v.ERASURE_REQUEST_STATUSES[0] == "pending"
    assert v.DECIDED_REQUEST_STATUSES == v.ERASURE_REQUEST_STATUSES[1:]
    assert v.ERASURE_REQUEST_FILTERS == ("pending", "decided", "all")
    assert v.SOURCE_SCIM in v.REQUEST_SOURCES and v.REQUEST_SOURCES[:2] == ("self", "admin")
    assert set(v.BELL_APP_PATHS) == {"erasure_request_waiting", "erasure_request_declined"} < set(v.BELL_SENTENCES)


def test_the_vocabulary_agrees_with_the_modules_it_copies():
    # The four "asserted" equalities of contract §1.1 (the vocabulary imports nothing).
    assert v.EXPORT_TTL_HOURS == project_export_vocab.EXPORT_TTL_HOURS
    assert v.PART_ROWS_MAX == project_export_vocab.PART_ROWS_MAX
    assert v.PART_BYTES_MAX == project_export_vocab.PART_BYTES_MAX
    assert v.JOB_STALE_MINUTES == project_export_vocab.JOB_STALE_MINUTES
    assert v.ERROR_SENTENCES["archive_unreadable"] == retention_vocab.EXPORT_ERROR_SENTENCES["archive_unreadable"]
    assert v.ERROR_SENTENCES["archive_unreadable"] == project_export_vocab.ERROR_SENTENCES["archive_unreadable"]


def test_the_sentences_state_the_caps_and_the_markers():
    assert f"{v.EXPORT_TTL_HOURS} hours" in v.ERROR_SENTENCES["export_expired"]
    assert "256 MB" in v.ERROR_SENTENCES["export_too_large"] and v.EXPORT_MAX_BYTES == 256 * 1024 * 1024
    assert f'"{v.PSEUDONYM_PREFIX}"' in v.ERROR_SENTENCES["username_reserved"]
    assert f"“{v.ERASED_DISPLAY}”" in v.UI_SENTENCES["erase_done"]
    assert v.STORAGE_PREFIX_EXPORT.endswith("/") and v.PSEUDONYM_PREFIX.endswith("-")
    assert list(v.PSEUDONYM_HEX_LENGTHS) == sorted(v.PSEUDONYM_HEX_LENGTHS) and v.PSEUDONYM_HEX_LENGTHS[-1] == 32
    # The pseudonym fits users.username (100) and the address users.email (255) at every length.
    longest = v.PSEUDONYM_PREFIX + "f" * v.PSEUDONYM_HEX_LENGTHS[-1]
    assert len(longest) <= 100 and len(f"{longest}@{v.ERASED_EMAIL_DOMAIN}") <= 255
    # The marker can never be a bcrypt hash (verify_password returns False for it).
    assert not v.ERASED_HASH_MARKER.startswith("$2")
    assert v.ERASED_EMAIL_DOMAIN.endswith(".invalid")   # RFC 2606: never routable
    assert v.JOB_STALE_MINUTES > v.QUEUED_REDISPATCH_MINUTES and v.REDISPATCH_MAX >= 1


@pytest.mark.parametrize("table", ["ERROR_SENTENCES", "UI_SENTENCES", "DECLINE_SENTENCES", "BELL_SENTENCES"])
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
    assert modules == ["__future__"], f"subject_rights_vocab.py is constants only; it imports {modules}"


def test_no_constant_name_reads_as_a_password():
    # CodeQL reads names: the marker written to hashed_password must not be called *Password*.
    tree = ast.parse(VOCAB_PY.read_text(encoding="utf-8"))
    names = [t.id for node in ast.walk(tree) if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)]
    assert names and not [n for n in names if "PASSWORD" in n.upper()], names
