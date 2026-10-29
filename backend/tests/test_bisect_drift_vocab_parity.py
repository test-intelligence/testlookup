"""W23b: the bisect / drift / run-context vocabularies agree everywhere they are copied.

* ``frontend/src/types/bisect.ts`` mirrors ``services/bisect_vocab.py`` and
  ``frontend/src/types/configDrift.ts`` mirrors the drift half of
  ``services/run_context_vocab.py``, constant for constant (arrays, numbers and
  every sentence, ``BISECT_SENTENCES`` key for key). Read as TEXT (no Node in the
  backend suite), with the W22b regex readers: a drifted copy would show one
  sentence on the client and another on the server.
* ``sdk-vectors/run_context.json`` (the cross-language specification the SDKs
  build against) carries the SAME allowlists, caps, patterns, token shapes and
  userinfo rule as ``run_context_vocab``, and its refusal sentences ARE the
  vocabulary's.
* The server-side copies agree with each other: ``REDACTED`` is
  ``redaction_service.REDACTED``; the first six ``COMPARE_REASONS`` are
  ``github_target.GITHUB_UNAVAILABLE_REASONS`` (the SPA reuses their sentences
  from ``COMMIT_RANGE_REASON_TEXT``, which must carry all six);
  ``BISECT_FALLBACK_BRANCHES`` are the literals of ``_pick_anchor``'s fallback
  (source read: the service is imported, never edited).
* Both vocabularies are constants only: no ``app.*`` import.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.services import bisect_vocab as bv
from app.services import run_context_vocab as rv

ROOT = Path(__file__).resolve().parents[2]
BISECT_TS = ROOT / "frontend" / "src" / "types" / "bisect.ts"
DRIFT_TS = ROOT / "frontend" / "src" / "types" / "configDrift.ts"
REASONS_TS = ROOT / "frontend" / "src" / "pages" / "run" / "commitRangeReasons.ts"
VECTORS = ROOT / "sdk-vectors" / "run_context.json"
SERVICES = ROOT / "backend" / "app" / "services"

# A single- or double-quoted TS string literal (a value carrying an apostrophe is double-quoted).
_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _strings(text: str) -> tuple[str, ...]:
    return tuple(a or b for a, b in re.findall(_STR, text))


def _array(path: Path, name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _read(path))
    assert match, f"{name} is not declared as an `as const` array literal in {path.name}"
    return _strings(match.group(1))


def _number(path: Path, name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _read(path), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {path.name}"
    return int(match.group(1))


def _record(path: Path, name: str) -> dict[str, str]:
    # The values carry `{placeholder}`s, so the literal ends at the first column-0 `} as const`.
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _read(path), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {path.name}"
    body = match.group(1)
    entries = {
        key: a or b
        for key, a, b in re.findall(rf"^\s*'?([A-Za-z_.]+)'?:\s*{_STR},?\s*$", body, re.MULTILINE)
    }
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


def _vectors() -> dict:
    return json.loads(_read(VECTORS))


# ── the frontend copies ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("path", "name", "server"),
    [
        (BISECT_TS, "BISECT_STATES", bv.BISECT_STATES),
        (BISECT_TS, "ANCHOR_CLASSES", bv.ANCHOR_CLASSES),
        (BISECT_TS, "TEST_OUTCOMES", bv.TEST_OUTCOMES),
        (BISECT_TS, "COMPARE_STATES", bv.COMPARE_STATES),
        (BISECT_TS, "COMPARE_REASONS", bv.COMPARE_REASONS),
        (DRIFT_TS, "DRIFT_SECTIONS", rv.DRIFT_SECTIONS),
        (DRIFT_TS, "DRIFT_CHANGES", rv.DRIFT_CHANGES),
        (DRIFT_TS, "CONTEXT_STATES", rv.CONTEXT_STATES),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_the_frontend_arrays_equal_the_server(path, name, server):
    assert _array(path, name) == tuple(server)


@pytest.mark.parametrize(
    ("path", "name", "server"),
    [
        (BISECT_TS, "BISECT_MAX_RUNS", bv.BISECT_MAX_RUNS),
        (BISECT_TS, "UNTESTED_LIST_MAX", bv.UNTESTED_LIST_MAX),
        (DRIFT_TS, "DRIFT_ENTRIES_MAX", rv.DRIFT_ENTRIES_MAX),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_the_frontend_numbers_equal_the_server(path, name, server):
    assert _number(path, name) == server


@pytest.mark.parametrize(
    ("path", "name", "server"),
    [
        (BISECT_TS, "BISECT_SENTENCES", bv.BISECT_SENTENCES),
        (BISECT_TS, "BISECT_ERRORS", bv.BISECT_ERRORS),
        (DRIFT_TS, "DRIFT_SENTENCES", rv.DRIFT_SENTENCES),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_every_sentence_the_spa_mirrors_is_the_servers_word_for_word(path, name, server):
    assert _record(path, name) == dict(server)


def test_the_spa_has_a_sentence_for_every_shared_git_host_reason():
    match = re.search(
        r"export const COMMIT_RANGE_REASON_TEXT\b[^=]*=\s*\{(.*?)\n\}", _read(REASONS_TS), re.DOTALL
    )
    assert match, "COMMIT_RANGE_REASON_TEXT is not an object literal in commitRangeReasons.ts"
    keys = set(re.findall(r"^\s*'?([a-z_]+)'?:", match.group(1), re.MULTILINE))
    assert set(bv.COMPARE_REASONS[:6]) <= keys


# ── the SDK vector file ──────────────────────────────────────────────────────

def test_the_vector_file_carries_the_vocabularys_allowlists_caps_and_shapes():
    vectors = _vectors()
    assert vectors["version"] == rv.CONTEXT_VERSION
    assert vectors["env_allowlist"] == list(rv.ENV_ALLOWLIST)
    assert vectors["lockfile_names"] == list(rv.LOCKFILE_NAMES)
    assert vectors["caps"] == {
        "bytes": rv.CONTEXT_MAX_BYTES, "runtime": rv.RUNTIME_MAX, "images": rv.IMAGES_MAX, "env_value": rv.ENV_VALUE_MAX,
    }
    assert vectors["patterns"] == {
        "runtime_key": rv.RUNTIME_KEY_RE, "version": rv.VERSION_RE, "sha256": rv.SHA256_RE,
        "image_name": rv.IMAGE_NAME_RE, "digest": rv.DIGEST_RE,
    }
    assert vectors["token_shapes"] == list(rv.TOKEN_SHAPES)
    assert vectors["userinfo_rule"] == {"pattern": rv.USERINFO_RE, "replacement": "\\1" + rv.REDACTED + "@"}
    assert vectors["redacted"] == rv.REDACTED


def test_the_vector_files_refusal_sentences_are_the_vocabularys():
    vectors = _vectors()
    keys = ", ".join(rv.ENV_ALLOWLIST)
    sections = {"runtime": rv.RUNTIME_MAX, "image_digests": rv.IMAGES_MAX}
    assert vectors["refusals"], "the file pins at least one refusal"
    for refusal in vectors["refusals"]:
        sentence = rv.RUN_CONTEXT_ERRORS[refusal["error"]]
        if refusal["error"] == "env_key":
            sentence = sentence.format(keys=keys)
        elif refusal["error"] == "too_many":
            sentence = sentence.format(section=refusal["field"], max=sections[refusal["field"]])
        assert refusal["message"] == sentence, refusal["name"]
    for refusal in vectors["form_refusals"]:
        assert refusal["message"] == rv.RUN_CONTEXT_ERRORS[refusal["code"]], refusal["name"]


def test_the_vector_file_is_well_formed():
    vectors = _vectors()
    names = [c["name"] for group in ("cases", "refusals", "redactions", "form_refusals") for c in vectors[group]]
    assert len(names) == len(set(names)), "every case name is unique"
    for case in vectors["cases"]:
        assert set(case) >= {"name", "files", "env", "expect"}, case["name"]
        assert case["expect"]["v"] == rv.CONTEXT_VERSION, case["name"]
        assert set(case["expect"]) <= {"v", "runtime", "dependency_fingerprints", "image_digests", "env"}
    by_name = {c["name"]: c for c in vectors["cases"]}
    lf, crlf = by_name["poetry-lock-lf"], by_name["poetry-lock-crlf"]
    assert crlf["files"]["poetry.lock"] == lf["files"]["poetry.lock"].replace("\n", "\r\n")
    assert (
        crlf["expect"]["dependency_fingerprints"]["poetry.lock"]
        != lf["expect"]["dependency_fingerprints"]["poetry.lock"]
    ), "raw bytes are hashed: a CRLF twin hashes differently (by design)"
    for pattern in rv.TOKEN_SHAPES + (rv.USERINFO_RE,):
        re.compile(pattern)


# ── the server-side copies ───────────────────────────────────────────────────

def test_redacted_is_the_redaction_services_placeholder():
    from app.services import redaction_service

    assert rv.REDACTED == redaction_service.REDACTED


def test_the_first_six_compare_reasons_are_the_git_host_reasons():
    from app.services.github_target import GITHUB_UNAVAILABLE_REASONS

    assert bv.COMPARE_REASONS[:6] == tuple(GITHUB_UNAVAILABLE_REASONS)
    assert len(set(bv.COMPARE_REASONS)) == len(bv.COMPARE_REASONS)


def test_the_fallback_branches_are_pick_anchors_literals():
    tree = ast.parse((SERVICES / "commit_attribution_service.py").read_text(encoding="utf-8"))
    (pick,) = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_pick_anchor"]
    literals = [
        tuple(e.value for e in node.elts)
        for node in ast.walk(pick)
        if isinstance(node, ast.Tuple) and node.elts and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        )
    ]
    assert literals == [bv.BISECT_FALLBACK_BRANCHES]


@pytest.mark.parametrize("module", ["bisect_vocab.py", "run_context_vocab.py"])
def test_the_vocabularies_are_constants_only(module):
    tree = ast.parse((SERVICES / module).read_text(encoding="utf-8"))
    imported = [alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names]
    imported += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert all(name == "__future__" for name in imported), imported
    assert not [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def test_the_bisect_states_and_compare_states_have_their_sentences():
    # Every state the block renders without a template of its own has a fixed sentence.
    for key in ("never_passed", "no_pass_within_bound", "not_failing", "narrowed", "narrowed_main"):
        assert key in bv.BISECT_SENTENCES
    for reason in bv.COMPARE_REASONS[6:]:
        if reason != "not_narrowed":
            assert reason in bv.BISECT_SENTENCES, reason
    assert set(rv.CONTEXT_STATES) - {"recorded"} <= set(rv.DRIFT_SENTENCES)
