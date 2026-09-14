"""Runtime provenance for the eval manifest that admitted an agent stack."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIEvalGateRun, AgentPipelineRun

_MODULE_DIR = Path(__file__).resolve().parent
CURRENT_ATTESTATION_PATH = _MODULE_DIR / "prompt_manifest_eval.json"
EVAL_MANIFEST_ARCHIVE_DIR = _MODULE_DIR / "eval_manifests"
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvalManifestError(ValueError):
    """The bundled eval manifest is absent, malformed, or tampered with."""


def eval_manifest_checksum(manifest: Mapping[str, Any]) -> str:
    """Hash all immutable attestation content except the checksum itself."""
    payload = dict(manifest)
    payload.pop("eval_manifest_checksum", None)
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stamp_and_archive_eval_manifest(
    manifest: Mapping[str, Any],
    *,
    current_path: Path = CURRENT_ATTESTATION_PATH,
    archive_dir: Path = EVAL_MANIFEST_ARCHIVE_DIR,
) -> dict[str, Any]:
    """Stamp an attestation and retain an immutable, checksum-addressed copy."""
    stamped = dict(manifest)
    stamped["eval_manifest_checksum"] = eval_manifest_checksum(stamped)
    serialized = json.dumps(stamped, indent=2, sort_keys=True) + "\n"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{stamped['eval_manifest_checksum']}.json"
    if archive_path.exists() and archive_path.read_text(encoding="utf-8") != serialized:
        raise EvalManifestError("eval manifest checksum collision or archive corruption")
    archive_path.write_text(serialized, encoding="utf-8")
    current_path.write_text(serialized, encoding="utf-8")
    return stamped


def load_bundled_eval_manifest(
    checksum: str,
    *,
    archive_dir: Path = EVAL_MANIFEST_ARCHIVE_DIR,
) -> dict[str, Any] | None:
    """Resolve one checksum from the version-controlled manifest archive."""
    if not _CHECKSUM_PATTERN.fullmatch(checksum):
        return None
    path = archive_dir / f"{checksum}.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalManifestError(f"eval manifest archive is unreadable: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("eval_manifest_checksum") != checksum:
        raise EvalManifestError("eval manifest archive checksum field does not match its name")
    if eval_manifest_checksum(manifest) != checksum:
        raise EvalManifestError("eval manifest archive content does not match its checksum")
    return manifest


def current_eval_manifest(
    *,
    current_path: Path = CURRENT_ATTESTATION_PATH,
    archive_dir: Path = EVAL_MANIFEST_ARCHIVE_DIR,
) -> dict[str, Any]:
    """Load the current passing manifest and verify its durable archive copy."""
    try:
        manifest = json.loads(current_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalManifestError(f"current eval manifest is unreadable: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("verdict") != "pass":
        raise EvalManifestError("current eval manifest is not a passing attestation")
    checksum = manifest.get("eval_manifest_checksum")
    if not isinstance(checksum, str) or not _CHECKSUM_PATTERN.fullmatch(checksum):
        raise EvalManifestError("current eval manifest has no valid checksum")
    if eval_manifest_checksum(manifest) != checksum:
        raise EvalManifestError("current eval manifest content does not match its checksum")
    archived = load_bundled_eval_manifest(checksum, archive_dir=archive_dir)
    if archived != manifest:
        raise EvalManifestError("current eval manifest is missing from the immutable archive")
    return manifest


def current_eval_manifest_checksum() -> str:
    """Return the verified checksum frozen into every new pipeline run."""
    return str(current_eval_manifest()["eval_manifest_checksum"])


async def resolve_eval_manifest(
    db: AsyncSession,
    checksum: str,
) -> dict[str, Any] | None:
    """Resolve a DB-backed gate or a bundled offline/source-review attestation."""
    if not _CHECKSUM_PATTERN.fullmatch(checksum):
        return None
    row = (
        await db.execute(
            select(AIEvalGateRun)
            .where(AIEvalGateRun.manifest_checksum_sha256 == checksum)
            .order_by(AIEvalGateRun.evaluated_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is not None:
        return {
            "eval_manifest_checksum": checksum,
            "source": "database_gate",
            "status": row.status,
            "manifest": row.manifest,
            "gate_run_id": row.id,
            "evaluated_at": row.evaluated_at,
        }
    bundled = load_bundled_eval_manifest(checksum)
    if bundled is None:
        return None
    gate_run_id = bundled.get("eval_gate_run_id")
    try:
        parsed_gate_run_id = uuid.UUID(str(gate_run_id)) if gate_run_id else None
    except (TypeError, ValueError, AttributeError):
        parsed_gate_run_id = None
    return {
        "eval_manifest_checksum": checksum,
        "source": "bundled_attestation",
        "status": str(bundled.get("verdict") or "unknown"),
        "manifest": bundled,
        "gate_run_id": parsed_gate_run_id,
        "evaluated_at": bundled.get("attested_at"),
    }


async def eval_provenance_health(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    window_days: int = 7,
) -> dict[str, Any]:
    """Report whether every recent pipeline resolves to its frozen eval manifest."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)
    metadata_rows = (
        await db.execute(
            select(AgentPipelineRun.execution_metadata).where(AgentPipelineRun.created_at >= start)
        )
    ).scalars().all()

    checksums: list[str] = []
    missing_checksum_count = 0
    for metadata in metadata_rows:
        checksum = metadata.get("eval_manifest_checksum") if isinstance(metadata, Mapping) else None
        if isinstance(checksum, str) and _CHECKSUM_PATTERN.fullmatch(checksum):
            checksums.append(checksum)
        else:
            missing_checksum_count += 1

    distinct = set(checksums)
    database_checksums: set[str] = set()
    if distinct:
        database_checksums = set(
            (
                await db.execute(
                    select(AIEvalGateRun.manifest_checksum_sha256).where(
                        AIEvalGateRun.manifest_checksum_sha256.in_(distinct)
                    )
                )
            ).scalars().all()
        )
    bundled_checksums = {
        checksum for checksum in distinct if load_bundled_eval_manifest(checksum) is not None
    }
    resolved = database_checksums | bundled_checksums
    unresolved = distinct - resolved
    unresolved_run_count = missing_checksum_count + sum(
        1 for checksum in checksums if checksum in unresolved
    )
    total_runs = len(metadata_rows)
    return {
        "window_days": window_days,
        "window_start": start,
        "window_end": end,
        "total_runs": total_runs,
        "stamped_runs": len(checksums),
        "resolved_runs": total_runs - unresolved_run_count,
        "missing_checksum_count": missing_checksum_count,
        "unresolvable_run_count": unresolved_run_count,
        "unresolvable_checksums": sorted(unresolved),
        "has_unresolvable_checksums": unresolved_run_count > 0,
    }


__all__ = [
    "EvalManifestError",
    "current_eval_manifest",
    "current_eval_manifest_checksum",
    "eval_manifest_checksum",
    "eval_provenance_health",
    "load_bundled_eval_manifest",
    "resolve_eval_manifest",
    "stamp_and_archive_eval_manifest",
]
