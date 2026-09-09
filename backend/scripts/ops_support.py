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

import hashlib
import json
from pathlib import PurePosixPath
import sys
import tarfile

TOKEN_EQUAL = "equal"
TOKEN_STACK_NEWER = "stack_newer"
TOKEN_BACKUP_NEWER_OR_UNKNOWN = "backup_newer_or_unknown"

MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_MANIFEST_SCHEMAS = (1, 2)
REQUIRED_COMPONENTS = ("postgres", "mongo", "minio")
COMPONENT_FILES = {
    "postgres": "postgres.dump",
    "mongo": "mongo.archive.gz",
    "minio": "minio_data.tar.gz",
}
SHA256_HEX_LENGTH = 64


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


def validate_manifest(data: object, *, allow_unverified_v1: bool = False) -> list[str]:
    """Return a list of problems with a backup manifest (empty = valid)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["manifest is not a JSON object"]
    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_MANIFEST_SCHEMAS:
        problems.append(
            f"unsupported schema_version {data.get('schema_version')!r} "
            f"(supported: {SUPPORTED_MANIFEST_SCHEMAS})"
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
            name = entry["name"]
            filename = entry["file"]
            if name in names:
                problems.append(f"duplicate component '{name}'")
            names.add(name)
            expected_file = COMPONENT_FILES.get(name)
            if expected_file is not None and filename != expected_file:
                problems.append(
                    f"component '{name}' must use file '{expected_file}', got {filename!r}"
                )
            digest = entry.get("sha256")
            digest_is_valid = (
                isinstance(digest, str)
                and len(digest) == SHA256_HEX_LENGTH
                and all(char in "0123456789abcdef" for char in digest)
            )
            if not digest_is_valid and not (
                schema_version == 1 and allow_unverified_v1
            ):
                problems.append(f"component '{name}' has an invalid sha256")
        for required in REQUIRED_COMPONENTS:
            if required not in names:
                problems.append(f"missing component '{required}'")
    return problems


def _normalized_member_name(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name


def verify_archive(
    archive_path: str, *, allow_unverified_v1: bool = False
) -> list[str]:
    """Validate a backup archive without extracting attacker-controlled paths."""
    allowed = {"manifest.json", *COMPONENT_FILES.values()}
    problems: list[str] = []
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            for member in archive.getmembers():
                name = _normalized_member_name(member.name)
                if name in ("", ".") and member.isdir():
                    continue
                path = PurePosixPath(name)
                if (
                    not name
                    or path.is_absolute()
                    or "\\" in name
                    or ".." in path.parts
                    or name not in allowed
                ):
                    problems.append(f"unexpected archive member: {member.name!r}")
                    continue
                if not member.isfile():
                    problems.append(f"archive member is not a regular file: {member.name!r}")
                    continue
                if name in members:
                    problems.append(f"duplicate archive member: {name!r}")
                    continue
                if member.size <= 0:
                    problems.append(f"archive member is empty: {name!r}")
                    continue
                members[name] = member

            if problems:
                return problems
            missing = sorted(allowed - set(members))
            if missing:
                return [f"archive is missing member: {name}" for name in missing]

            manifest_stream = archive.extractfile(members["manifest.json"])
            if manifest_stream is None:
                return ["could not read manifest.json"]
            try:
                manifest = json.load(manifest_stream)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return [f"invalid manifest.json: {exc}"]

            problems.extend(
                validate_manifest(manifest, allow_unverified_v1=allow_unverified_v1)
            )
            if problems or not isinstance(manifest, dict):
                return problems

            components = {
                entry["name"]: entry
                for entry in manifest["components"]
                if isinstance(entry, dict) and entry.get("name") in COMPONENT_FILES
            }
            for name, filename in COMPONENT_FILES.items():
                stream = archive.extractfile(members[filename])
                if stream is None:
                    problems.append(f"could not read component '{name}'")
                    continue
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
                expected = components[name].get("sha256")
                if (
                    isinstance(expected, str)
                    and len(expected) == SHA256_HEX_LENGTH
                    and actual != expected
                ):
                    problems.append(
                        f"sha256 mismatch for component '{name}': "
                        f"expected {expected}, got {actual}"
                    )
    except (OSError, tarfile.TarError) as exc:
        return [f"invalid backup archive: {exc}"]
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
    if len(argv) >= 2 and argv[1] == "verify-archive":
        if len(argv) not in (3, 4):
            print(
                "usage: ops_support.py verify-archive <archive> "
                "[--allow-unverified-v1]",
                file=sys.stderr,
            )
            return 2
        allow_unverified = len(argv) == 4 and argv[3] == "--allow-unverified-v1"
        if len(argv) == 4 and not allow_unverified:
            print("unknown verify-archive option", file=sys.stderr)
            return 2
        problems = verify_archive(
            argv[2], allow_unverified_v1=allow_unverified
        )
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
