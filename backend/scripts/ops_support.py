"""Support logic for the ops backup/restore scripts (scripts/ops/*.sh).

Runs INSIDE the backend container (``compose run --rm --no-deps backend
python /app/scripts/ops_support.py ...``) so the host needs no Python or
alembic install. Both commands read only the migration *scripts* via
``ScriptDirectory`` — no database connection is made.

Commands
--------
compare-head <backup_head>
    Classify a backup's alembic head against the deployed code's migration
    tree. Prints exactly one token on stdout (restore.sh switches on it):

    - ``equal``                    backup head == deployed code head
    - ``stack_newer``              backup head is an ancestor of the code
                                   head (the deployed code has migrations the
                                   backup predates — restoring rolls back)
    - ``backup_newer_or_unknown``  backup head is not in the deployed
                                   migration tree (backup from a newer or
                                   unrelated version)

validate-manifest
    Read a backup manifest.json from stdin; print ``ok`` or the list of
    problems (exit 1).
"""

from __future__ import annotations

import json
import sys

TOKEN_EQUAL = "equal"
TOKEN_STACK_NEWER = "stack_newer"
TOKEN_BACKUP_NEWER_OR_UNKNOWN = "backup_newer_or_unknown"

MANIFEST_SCHEMA_VERSION = 1
REQUIRED_COMPONENTS = ("postgres", "mongo", "minio")


def classify_backup_head(
    backup_head: str,
    code_heads: list[str],
    code_ancestry: set[str],
) -> str:
    """Pure classification of a backup's migration head vs the deployed code.

    ``code_heads``   — the head revision(s) of the deployed migration scripts.
    ``code_ancestry``— every revision id reachable walking down from those
                       heads (heads included).
    """
    if not backup_head or not code_heads:
        return TOKEN_BACKUP_NEWER_OR_UNKNOWN
    if backup_head in code_heads:
        return TOKEN_EQUAL
    if backup_head in code_ancestry:
        return TOKEN_STACK_NEWER
    return TOKEN_BACKUP_NEWER_OR_UNKNOWN


def validate_manifest(data: object) -> list[str]:
    """Return a list of problems with a backup manifest (empty = valid)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["manifest is not a JSON object"]
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append(
            f"unsupported schema_version {data.get('schema_version')!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )
    for key in ("created_at", "alembic_head"):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            problems.append(f"missing or empty '{key}'")
    components = data.get("components")
    if not isinstance(components, list) or not components:
        problems.append("missing or empty 'components'")
    else:
        names = set()
        for entry in components:
            if not isinstance(entry, dict) or not entry.get("name") or not entry.get("file"):
                problems.append(f"malformed component entry: {entry!r}")
                continue
            names.add(entry["name"])
        for required in REQUIRED_COMPONENTS:
            if required not in names:
                problems.append(f"missing component '{required}'")
    return problems


def _load_code_revisions() -> tuple[list[str], set[str]]:
    """Read the deployed migration scripts (cwd must be /app — alembic.ini)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = list(script_dir.get_heads())
    ancestry = {rev.revision for rev in script_dir.walk_revisions()}
    return heads, ancestry


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "compare-head":
        if len(argv) != 3:
            print("usage: ops_support.py compare-head <backup_head>", file=sys.stderr)
            return 2
        heads, ancestry = _load_code_revisions()
        print(classify_backup_head(argv[2], heads, ancestry))
        return 0
    if len(argv) >= 2 and argv[1] == "validate-manifest":
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            print(f"invalid JSON: {exc}", file=sys.stderr)
            return 1
        problems = validate_manifest(data)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print("ok")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
