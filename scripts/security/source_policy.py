"""Run the existing pinned Ruff engine's narrow source-security policy.

Reports contain locations and fingerprints, never source excerpts or tool logs.
CodeQL remains the broad Python/JavaScript/TypeScript source analyzer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ROOTS = ("backend/app", "mcp", "cli/testlookup_cli")
RULES = frozenset({"S501", "S506"})
EXCLUDED = frozenset({"tests", "__pycache__", ".venv"})
MAX_EXCEPTION_DAYS = 90


class PolicyError(Exception):
    """A safe, finite error code suitable for public evidence."""


def _run(argv: list[str], root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=120, check=False)


def _source_files(root: Path) -> dict[str, str]:
    files = {}
    for folder in ROOTS:
        directory = root / folder
        if not directory.is_dir() or directory.is_symlink():
            raise PolicyError("source_root_missing")
        for path in directory.rglob("*"):
            relative = path.relative_to(root)
            if EXCLUDED.intersection(relative.parts):
                continue
            if path.is_symlink():
                raise PolicyError("source_file_not_regular")
            if path.suffix not in {".py", ".pyi"} or not path.is_file():
                continue
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise PolicyError("source_inventory_empty")
    return files


def _fingerprint(rule: str, path: str, line: int, column: int, file_sha: str) -> str:
    return hashlib.sha256(json.dumps([rule, path, line, column, file_sha], separators=(",", ":")).encode()).hexdigest()


def _exceptions(raw: dict, findings: list[dict], today: dt.date) -> list[dict]:
    if set(raw) != {"schema_version", "exceptions"} or raw["schema_version"] != 1 or not isinstance(raw["exceptions"], list):
        raise PolicyError("exception_schema_invalid")
    by_fingerprint = {finding["fingerprint"]: finding for finding in findings}
    seen = set()
    accepted = []
    for entry in raw["exceptions"]:
        if not isinstance(entry, dict) or set(entry) != {"fingerprint", "owner", "reason", "expires"}:
            raise PolicyError("exception_schema_invalid")
        fingerprint = entry["fingerprint"]
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or fingerprint in seen:
            raise PolicyError("exception_fingerprint_invalid")
        if fingerprint not in by_fingerprint:
            raise PolicyError("exception_stale")
        if any(not isinstance(entry[key], str) or not entry[key].strip() or len(entry[key]) > bound for key, bound in (("owner", 128), ("reason", 500))):
            raise PolicyError("exception_accountability_missing")
        try:
            expiry = dt.date.fromisoformat(entry["expires"])
        except (ValueError, TypeError):
            raise PolicyError("exception_expiry_invalid") from None
        if not today <= expiry <= today + dt.timedelta(days=MAX_EXCEPTION_DAYS):
            raise PolicyError("exception_expiry_out_of_policy")
        seen.add(fingerprint)
        by_fingerprint[fingerprint]["disposition"] = "accepted_until_expiry"
        accepted.append(dict(entry))
    return accepted


def analyze(root: Path, source_sha: str, *, ruff: list[str] | None = None, allow_dirty: bool = False) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise PolicyError("source_sha_invalid")
    head = _run(["git", "rev-parse", "HEAD"], root)
    if head.returncode or head.stdout.strip() != source_sha:
        raise PolicyError("source_sha_mismatch")
    tracked_scope = [*ROOTS, "scripts/security"]
    status = _run(["git", "status", "--porcelain", "--untracked-files=all", "--", *tracked_scope], root)
    if status.returncode:
        raise PolicyError("source_status_unavailable")
    tracked = _run(["git", "ls-files", "-z", "--", *ROOTS], root)
    if tracked.returncode:
        raise PolicyError("source_inventory_unavailable")
    files = _source_files(root)
    # Ignored local source must not become commit-bound merely because status
    # omits it. The normal candidate lane scans selected committed files only.
    tracked_paths = set(tracked.stdout.split("\0"))
    dirty = bool(status.stdout.strip()) or bool(set(files) - tracked_paths)
    if dirty and not allow_dirty:
        raise PolicyError("candidate_source_dirty")
    policy = HERE / "source-policy.toml"
    exceptions = HERE / "source-policy-exceptions.json"
    policy_bytes, exception_bytes = policy.read_bytes(), exceptions.read_bytes()
    config = tomllib.loads(policy_bytes.decode("utf-8"))
    version = config["required-version"].removeprefix("==")
    command = ruff or [sys.executable, "-m", "ruff"]
    observed = _run([*command, "--version"], root)
    if observed.returncode or observed.stdout.strip() != "ruff " + version:
        raise PolicyError("analyzer_version_mismatch")
    scan = _run([
        *command, "check", "--config", str(policy), "--no-cache", "--no-respect-gitignore",
        "--ignore-noqa", "--output-format=json", *ROOTS,
    ], root)
    if scan.returncode not in {0, 1}:
        raise PolicyError("analyzer_execution_failed")
    try:
        raw_findings = json.loads(scan.stdout)
        if not isinstance(raw_findings, list):
            raise ValueError
        findings = []
        for raw in raw_findings:
            path = Path(raw["filename"]).resolve().relative_to(root.resolve()).as_posix()
            rule = raw["code"]
            line, column = raw["location"]["row"], raw["location"]["column"]
            if rule not in RULES or path not in files or type(line) is not int or type(column) is not int or line < 1 or column < 1:
                raise ValueError
            findings.append({
                "rule": rule, "path": path, "line": line, "column": column,
                "fingerprint": _fingerprint(rule, path, line, column, files[path]), "disposition": "blocking",
            })
        if (scan.returncode == 0) != (not findings):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise PolicyError("analyzer_output_invalid") from None
    accepted = _exceptions(json.loads(exception_bytes), findings, dt.datetime.now(dt.timezone.utc).date())
    # An edit during analysis invalidates the candidate even if Ruff succeeded.
    if _source_files(root) != files:
        raise PolicyError("source_changed_during_analysis")
    if policy.read_bytes() != policy_bytes or exceptions.read_bytes() != exception_bytes:
        raise PolicyError("policy_changed_during_analysis")
    final_status = _run(["git", "status", "--porcelain", "--untracked-files=all", "--", *tracked_scope], root)
    if final_status.returncode or (not dirty and final_status.stdout.strip()):
        raise PolicyError("candidate_changed_during_analysis")
    blocking = any(finding["disposition"] == "blocking" for finding in findings)
    policy_sha = hashlib.sha256(policy_bytes + b"\0" + exception_bytes).hexdigest()
    return {
        "schema_version": 1, "source_sha": source_sha,
        "source_snapshot_sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "policy_sha256": policy_sha, "tool": {"name": "ruff", "version": version},
        "checked_roots": list(ROOTS), "checked_files": len(files),
        "snapshot_kind": "worktree" if dirty else "commit", "candidate_eligible": not dirty,
        "status": "FAIL" if blocking else "WORKTREE_PASS" if dirty else "PASS",
        "findings": findings, "exceptions": accepted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ruff", type=Path, help="Explicit verified executable for local checks")
    parser.add_argument("--allow-dirty", action="store_true", help="Local worktree evidence only, never candidate-eligible")
    args = parser.parse_args(argv)
    try:
        report = analyze(ROOT, args.source_sha, ruff=[str(args.ruff)] if args.ruff else None, allow_dirty=args.allow_dirty)
    except PolicyError as exc:
        report = {"schema_version": 1, "source_sha": args.source_sha if re.fullmatch(r"[0-9a-f]{40}", args.source_sha) else None, "candidate_eligible": False, "status": "ERROR", "error_code": str(exc)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        report = {"schema_version": 1, "candidate_eligible": False, "status": "ERROR", "error_code": "policy_prerequisite_failed"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print("Source policy: " + report["status"])
    return 0 if report["status"] in {"PASS", "WORKTREE_PASS"} else 1


if __name__ == "__main__":
    sys.exit(main())
