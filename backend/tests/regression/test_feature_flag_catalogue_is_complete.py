"""Every feature flag is in the operator catalogue.

Flags are rows seeded by Alembic migrations, plus keys the code reads that no
migration seeds (those stay off until an admin creates the row). Before
``docs/operations/feature-flags.md`` existed nothing listed them: an operator
could not tell that the quarantine workflow, outbound webhooks or the manual
upload button shipped OFF, or that a pipeline stage needed a flag nobody had
created (OSS-readiness audit, section D3).

These tests read the migrations and the code, not a hand-kept list, so a new
flag fails here until the catalogue names it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = REPO_ROOT / "docs" / "operations" / "feature-flags.md"
MIGRATIONS = REPO_ROOT / "backend" / "migrations" / "versions"
BACKEND_APP = REPO_ROOT / "backend" / "app"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

_KEY = r"[a-z][a-z0-9_]*"
#: ``VALUES (gen_random_uuid(), 'key', ...`` / ``VALUES (CAST(:id AS uuid), 'key', ...``
_SQL_VALUES_RE = re.compile(
    r"VALUES\s*\(\s*(?:gen_random_uuid\(\)|CAST\(\s*:\w+\s+AS\s+uuid\s*\))\s*,\s*'(" + _KEY + r")'",
    re.IGNORECASE,
)
#: ``... feature_flags ... WHERE key = 'key'`` (seed flips such as 0099 and 0201).
_SQL_WHERE_KEY_RE = re.compile(r"WHERE\s+key\s*=\s*'(" + _KEY + r")'", re.IGNORECASE)
#: Module constants that hold one flag key, or a collection of them.
_SINGLE_KEY_NAMES = {"_KEY", "_FLAG_KEY"}
_KEY_COLLECTION_NAMES = {"_FLAGS", "_FLAG_KEYS", "_RETIRED"}
#: ``is_enabled("key"`` (the call may wrap onto the next line).
_IS_ENABLED_RE = re.compile(r"\bis_enabled\(\s*[\"'](" + _KEY + r")[\"']")
#: Module constants naming a flag the code evaluates under another name.
_FLAG_CONSTANT_RE = re.compile(
    r"^(?:FEATURE_FLAG|LIFECYCLE_FLAG_KEY)\s*=\s*[\"'](" + _KEY + r")[\"']", re.MULTILINE
)
#: ``useFeatureEnabled('key')`` / ``useFeatureFlagStatus('key')`` in the SPA.
_FRONTEND_FLAG_RE = re.compile(r"\buseFeature(?:Enabled|FlagStatus)\(\s*['\"](" + _KEY + r")['\"]")


def _catalogue() -> str:
    assert CATALOGUE.is_file(), f"missing {CATALOGUE.relative_to(REPO_ROOT)}"
    return CATALOGUE.read_text(encoding="utf-8")


def _first_string(node: ast.AST) -> str | None:
    """The key a collection element names: ``"key"`` or ``("key", "description", ...)``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)) and node.elts:
        first = node.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _keys_in_migration(source: str) -> set[str]:
    keys: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # SQL text, after Python has joined adjacent string literals.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "feature_flags" in node.value:
                keys.update(_SQL_VALUES_RE.findall(node.value))
                keys.update(_SQL_WHERE_KEY_RE.findall(node.value))
        # _KEY = "x" / _FLAGS = (("x", "description"), ...)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in _SINGLE_KEY_NAMES:
                    value = _first_string(node.value)
                    if value:
                        keys.add(value)
                elif target.id in _KEY_COLLECTION_NAMES and isinstance(
                    node.value, (ast.Tuple, ast.List)
                ):
                    keys.update(k for k in map(_first_string, node.value.elts) if k)
        # for key, description in [("x", "..."), ...]:
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
            target = node.target
            first_target = target.elts[0] if isinstance(target, ast.Tuple) else target
            if isinstance(first_target, ast.Name) and first_target.id == "key":
                keys.update(k for k in map(_first_string, node.iter.elts) if k)
    return {k for k in keys if re.fullmatch(_KEY, k)}


def seeded_flag_keys() -> set[str]:
    keys: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "feature_flags" in source and re.search(
            r"INSERT\s+INTO\s+feature_flags|UPDATE\s+feature_flags", source
        ):
            keys |= _keys_in_migration(source)
    return keys


def flag_keys_read_by_code() -> set[str]:
    keys: set[str] = set()
    for path in BACKEND_APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        keys.update(_IS_ENABLED_RE.findall(text))
        keys.update(_FLAG_CONSTANT_RE.findall(text))
    for pattern in ("*.ts", "*.tsx"):
        for path in FRONTEND_SRC.rglob(pattern):
            if ".test." in path.name or "__tests__" in path.parts:
                continue
            keys.update(_FRONTEND_FLAG_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return keys


def _missing(keys: set[str], text: str) -> list[str]:
    return sorted(key for key in keys if f"`{key}`" not in text)


def test_the_migration_scan_finds_the_known_flags():
    """A scan that silently matched nothing would let every later test pass."""
    keys = seeded_flag_keys()
    expected = {
        "knowledge_rag",            # SQL literal in VALUES (0062)
        "cypress_ingest",           # _FLAGS tuple list (0063)
        "llm_cost_budget",          # SQL literal (0064)
        "rag_faithfulness_gate",    # for key, description in [...] (0069)
        "manual_upload",            # SQL literal (0092)
        "ask_ai_chat",              # _KEY constant + bound parameter (0100)
        "test_case_lifecycle_v2",   # _FLAG_KEY + CAST(:id AS uuid) (0144)
        "viz_three_d",              # _FLAGS tuple list (0192)
    }
    assert expected <= keys, f"scan missed {sorted(expected - keys)}; found {sorted(keys)}"
    assert len(keys) >= 20, sorted(keys)


def test_every_seeded_flag_is_in_the_catalogue():
    missing = _missing(seeded_flag_keys(), _catalogue())
    assert not missing, (
        "docs/operations/feature-flags.md does not list these flags seeded by "
        "backend/migrations/versions. Add each with its default and what it gates:\n  "
        + "\n  ".join(missing)
    )


def test_every_flag_the_code_reads_is_in_the_catalogue():
    keys = flag_keys_read_by_code()
    # Flags that no migration seeds: the code must still be found reading them.
    assert {"defect_commander", "flaky_detection_timing", "cluster_investigator_children"} <= keys, (
        sorted(keys)
    )
    missing = _missing(keys, _catalogue())
    assert not missing, (
        "the code evaluates these feature flags but docs/operations/feature-flags.md "
        "does not list them (unseeded flags are OFF until an admin creates the row; "
        "say so):\n  " + "\n  ".join(missing)
    )
