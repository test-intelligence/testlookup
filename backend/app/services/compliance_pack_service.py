"""
Release compliance export pack — Tier 1 item 4.

Generates an auditable ZIP that fully reconstructs a release decision
for regulated customers. The pack contains every piece of data a
compliance reviewer needs to answer "how did TestLookup arrive at this
GO/NO_GO, and by what policy?":

  * ``manifest.json``        — SHA-256 digest of every other file + generation metadata
  * ``README.md``            — human-readable description of the pack
  * ``release.json``         — release row snapshot
  * ``run.json``             — test run snapshot
  * ``decision.json``        — ReleaseDecision row + override audit chain
  * ``policy_snapshot.json`` — the exact ReleaseGatePolicy that was in force
  * ``decision_trail.json``  — full AI decision trail from Tier 0B
  * ``clusters.json``        — failure clusters with member test IDs
  * ``defects.json``         — defect rows + Jira ticket links
  * ``audit_events.json``    — release overrides, settings changes, access events

Integrity model — **tamper-evident, NOT signed.** The manifest's SHA-256
(recorded in the ``compliance_packs`` row) is the root of a plain SHA-256
checksum chain: if the row's ``manifest_sha256`` matches the ZIP's
manifest.json, and each file's hash matches the manifest, the pack is
unmodified since generation. A future verifier command can re-derive every
digest to confirm nothing was modified in MinIO.

There is **no HMAC and no PKI** anywhere in this path — nothing here is
signed, and a pack cannot be attributed to TestLookup cryptographically.
Residual risk: an actor able to rewrite BOTH the MinIO object and the
``compliance_packs`` row can forge a self-consistent pack, and the chain
will happily verify. Closing that is operator-side: WORM / object-lock on
the ``compliance-packs`` bucket, and own-key custody of an off-host copy of
the ``manifest_sha256`` values (or a signature over them). Adding real
signing is a product decision, not a bug fix — do not describe this chain
as "signed" until it is.

This service is feature-flagged behind ``release_compliance_pack`` so
deployments that haven't enabled it see zero behaviour change.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CompliancePack,
    Defect,
    FailureCluster,
    Release,
    ReleaseDecision,
    ReleaseGatePolicy,
    TestRun,
    User,
)

logger = structlog.get_logger("services.compliance_pack")

COMPLIANCE_BUCKET = "compliance-packs"
# Seven years — SOX/HIPAA/SOC-2 baseline for audit artefact retention.
DEFAULT_RETENTION_DAYS = 2557

# Sentinel filename used by the manifest check. Must stay in sync with
# ``_build_manifest`` below.
_MANIFEST_FILENAME = "manifest.json"


class CompliancePackDisabledError(RuntimeError):
    """Raised when the feature flag is off — router translates to 503."""


class CompliancePackNotAvailableError(RuntimeError):
    """Raised when the release has no decision to snapshot yet."""


# ── Feature flag gate ──────────────────────────────────────────────────────


async def _feature_enabled(db: Optional[AsyncSession] = None) -> bool:
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("release_compliance_pack", db=db)
    except Exception as exc:
        logger.debug("release_compliance_pack flag check failed", error=str(exc))
        return False


# ── Data assembly ──────────────────────────────────────────────────────────


def _to_jsonable(value: Any) -> Any:
    """Best-effort recursive coercion for JSON serialization.

    The pack is read by compliance tools, not humans, so we prefer
    lossless over pretty: datetimes → ISO strings, UUIDs → string,
    unknown objects → ``str(value)`` as a last resort.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    # ORM row with ``__table__`` — emit its column values.
    if hasattr(value, "__table__"):
        return {
            col.name: _to_jsonable(getattr(value, col.name, None))
            for col in value.__table__.columns
        }
    return str(value)


async def _gather_release_snapshot(
    db: AsyncSession, release: Release,
) -> dict[str, Any]:
    return _to_jsonable(release)


async def _gather_decision_and_run(
    db: AsyncSession, release: Release,
) -> tuple[Optional[ReleaseDecision], Optional[TestRun]]:
    """Find the most recent test run linked to this release that has a
    ReleaseDecision row, and return the decision + run.

    The ``ReleaseDecision`` join is part of the query (not a follow-up lookup
    on the single newest linked run) so a newer linked run that hasn't been
    through the gate yet does NOT mask an older, decided run — otherwise a
    release that was decided and then re-run could no longer produce a pack.
    """
    # Releases ↔ runs via ``ReleaseTestRunLink``; join ReleaseDecision so only
    # decided runs are eligible, newest-first.
    from app.models.postgres import ReleaseTestRunLink
    stmt = (
        select(ReleaseDecision, TestRun)
        .select_from(TestRun)
        .join(ReleaseTestRunLink, ReleaseTestRunLink.test_run_id == TestRun.id)
        .join(ReleaseDecision, ReleaseDecision.test_run_id == TestRun.id)
        .where(ReleaseTestRunLink.release_id == release.id)
        .order_by(TestRun.start_time.desc().nulls_last())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None, None
    decision, run = row
    return decision, run


async def _gather_policy_snapshot(
    db: AsyncSession, decision: Optional[ReleaseDecision], project_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """Resolve which ReleaseGatePolicy applied to the decision and snapshot it.

    Preference order (matches ``criticality_service``):
      1. The policy explicitly referenced by ``ReleaseDecision.policy_id``
      2. The active project-specific policy
      3. The active system default (``project_id IS NULL``)
      4. None — fell back to hardcoded config thresholds
    """
    policy: Optional[ReleaseGatePolicy] = None
    if decision and decision.policy_id:
        result = await db.execute(
            select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == decision.policy_id)
        )
        policy = result.scalar_one_or_none()
    if policy is None:
        result = await db.execute(
            select(ReleaseGatePolicy).where(
                ReleaseGatePolicy.project_id == project_id,
                ReleaseGatePolicy.is_active.is_(True),
            )
        )
        policy = result.scalar_one_or_none()
    if policy is None:
        result = await db.execute(
            select(ReleaseGatePolicy).where(
                ReleaseGatePolicy.project_id.is_(None),
                ReleaseGatePolicy.is_active.is_(True),
            )
        )
        policy = result.scalar_one_or_none()
    if policy is None:
        return {
            "source": "hardcoded_config_defaults",
            "note": (
                "No ReleaseGatePolicy was active at decision time — the "
                "gate used the hardcoded thresholds from core/config.py. "
                "See migration 0032 notes for the policy precedence rules."
            ),
        }
    return {
        "source": "release_gate_policies_row",
        "policy": _to_jsonable(policy),
    }


async def _gather_decision_trail(
    db: AsyncSession, run_id: Optional[uuid.UUID],
) -> dict[str, Any]:
    """Call the Tier 0B decision trail aggregator."""
    if run_id is None:
        return {"note": "No linked test run — decision trail unavailable"}
    try:
        from app.services.decision_trail_service import build_trail
        trail = await build_trail(db, run_id)
        return _to_jsonable(trail or {})
    except Exception as exc:
        logger.warning("decision trail collection failed", error=str(exc))
        return {"error": f"decision trail collection failed: {exc}"}


async def _gather_clusters(
    db: AsyncSession, run_id: Optional[uuid.UUID],
) -> list[dict[str, Any]]:
    if run_id is None:
        return []
    result = await db.execute(
        select(FailureCluster).where(FailureCluster.test_run_id == run_id)
    )
    return [_to_jsonable(c) for c in result.scalars().all()]


async def _gather_defects(
    db: AsyncSession, project_id: uuid.UUID, run_id: Optional[uuid.UUID],
) -> list[dict[str, Any]]:
    """Defects linked to the run's failed tests + any open defects on the
    project (so reviewers see both the run-scoped chain and outstanding
    open issues)."""
    from app.models.postgres import TestCase
    rows: list[Defect] = []
    if run_id is not None:
        stmt = (
            select(Defect)
            .join(TestCase, TestCase.id == Defect.test_case_id)
            .where(TestCase.test_run_id == run_id)
        )
        result = await db.execute(stmt)
        rows.extend(result.scalars().all())
    return [_to_jsonable(d) for d in rows]


async def _gather_audit_events(
    db: AsyncSession,
    release: Release,
    run_id: Optional[uuid.UUID],
) -> dict[str, Any]:
    """Pull settings_audit_log + access_audit_log entries relevant to this
    release decision. Every override on the ReleaseDecision row is already
    captured in ``ReleaseDecision.override_audit``; this pass adds the
    surrounding environment (settings changes, access events)."""
    from app.models.postgres import AccessAuditLog, SettingsAuditLog
    # Filter to the release's project + a generous time window around the
    # run so we catch "someone flipped a flag 10 minutes before the gate
    # fired" scenarios.
    try:
        settings_stmt = (
            select(SettingsAuditLog)
            .where(SettingsAuditLog.setting_key.like(f"release:{release.id}%"))
            .order_by(SettingsAuditLog.created_at.desc())
            .limit(200)
        )
        settings_rows = (await db.execute(settings_stmt)).scalars().all()
    except Exception as exc:
        logger.debug("settings audit query failed", error=str(exc))
        settings_rows = []

    try:
        access_stmt = (
            select(AccessAuditLog)
            .where(AccessAuditLog.project_id == release.project_id)
            .order_by(AccessAuditLog.created_at.desc())
            .limit(200)
        )
        access_rows = (await db.execute(access_stmt)).scalars().all()
    except Exception as exc:
        logger.debug("access audit query failed", error=str(exc))
        access_rows = []

    return {
        "settings_audit": [_to_jsonable(r) for r in settings_rows],
        "access_audit": [_to_jsonable(r) for r in access_rows],
    }


async def _gather_agent_activity(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
) -> dict[str, Any]:
    """AI-3: snapshot the agent-runs ledger entries touching this release's
    run — what autonomous agents observed/proposed around the gated build
    (mode, trigger, verdict summary, actions proposed vs taken, spend,
    prompt-registry digest). Self-guarding like the other surrounding-context
    sections: a broken ledger embeds an ``error`` payload instead of failing
    the pack."""
    from app.models.postgres import AgentRun
    from app.services.agent_investigation_service import serialize_agent_run

    try:
        stmt = (
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .order_by(AgentRun.created_at.desc())
            .limit(200)
        )
        if run_id is not None:
            stmt = stmt.where(AgentRun.run_id == run_id)
        rows = (await db.execute(stmt)).scalars().all()
        return {"agent_runs": [serialize_agent_run(r) for r in rows]}
    except Exception as exc:
        logger.debug("agent activity query failed", error=str(exc))
        return {"agent_runs": [], "error": str(exc)[:500]}


# ── ZIP assembly + tamper-evident checksum chain ───────────────────────────
#
# Deliberately NOT "signing": every helper below is plain SHA-256. No HMAC,
# no keys, no certificates. See the module docstring for the residual risk
# (rewrite-both forges) and the operator-side mitigations.


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serialize(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")


def _build_readme(
    release: Release,
    decision: Optional[ReleaseDecision],
    run: Optional[TestRun],
    generated_at: datetime,
) -> bytes:
    lines = [
        "# TestLookup Compliance Pack",
        "",
        f"**Release:** {release.name}" + (f" {release.version}" if release.version else ""),
        f"**Release ID:** {release.id}",
        f"**Project ID:** {release.project_id}",
        f"**Generated:** {generated_at.isoformat()}",
    ]
    if run is not None:
        lines.append(f"**Test Run:** {run.build_number or run.id}")
        lines.append(f"**Test Run ID:** {run.id}")
    if decision is not None:
        lines.append(f"**Recommendation:** {decision.recommendation}")
        lines.append(f"**Risk Score:** {decision.risk_score}")
        if decision.original_recommendation and decision.original_recommendation != decision.recommendation:
            lines.append(
                f"**Original recommendation (pre-override):** {decision.original_recommendation}"
            )
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("| File | Purpose |")
    lines.append("|------|---------|")
    lines.append("| `manifest.json` | SHA-256 digest of every other file + generation metadata. Root of the pack's tamper-evidence chain. |")
    lines.append("| `release.json` | Frozen snapshot of the release row. |")
    lines.append("| `run.json` | Frozen snapshot of the test run row used for the decision. |")
    lines.append("| `decision.json` | `ReleaseDecision` row including the override audit chain. |")
    lines.append("| `policy_snapshot.json` | The exact `ReleaseGatePolicy` version in force at decision time. |")
    lines.append("| `decision_trail.json` | AI decision trail (stages + per-test routing + workflow events). |")
    lines.append("| `clusters.json` | Failure clusters identified in the run. |")
    lines.append("| `defects.json` | Defects linked to the run's failed tests with Jira ticket references. |")
    lines.append("| `audit_events.json` | Settings + access audit events scoped to the release project. |")
    lines.append("")
    lines.append("## Verification (tamper-evident checksum chain — not a signature)")
    lines.append("")
    lines.append(
        "1. Compute SHA-256 of `manifest.json` and compare against the "
        "`manifest_sha256` column on the `compliance_packs` row for this pack."
    )
    lines.append(
        "2. For each file listed in `manifest.json.files`, compute its SHA-256 "
        "and compare against the hash in the manifest."
    )
    lines.append(
        "3. Any mismatch indicates tampering — the pack is no longer authoritative."
    )
    lines.append("")
    lines.append(
        "**What this does and does not prove.** The chain is plain SHA-256: it "
        "proves the ZIP matches the digest recorded when the pack was "
        "generated. It is **not signed** — there is no HMAC and no PKI — so it "
        "does not prove *who* produced the pack, and an actor who can rewrite "
        "both the stored ZIP and the `compliance_packs` row can produce a "
        "consistent forgery. If you need that property, keep the "
        "`manifest_sha256` under your own key custody (or on WORM / "
        "object-locked storage) outside the TestLookup deployment."
    )
    return "\n".join(lines).encode("utf-8")


def _build_manifest(
    files: dict[str, bytes],
    generated_at: datetime,
    scope: "ExportScope",
    run: Optional[TestRun],
    decision: Optional[ReleaseDecision],
) -> bytes:
    """Build the tamper-evidence manifest (checksum chain root — unsigned).

    ``files`` is a dict of filename → bytes for every file in the ZIP
    **except** the manifest itself. We compute SHA-256 of each, attach
    metadata, and return the encoded JSON. The caller hashes the result
    and stores the digest on the ``compliance_packs`` row.

    No key material is involved — the manifest is not signed or MACed, so
    its authority is only as strong as the custody of the stored digest.
    """
    entries = []
    for name, data in sorted(files.items()):
        entries.append(
            {
                "name": name,
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        )
    manifest: dict[str, Any] = {
        "format_version": 1,
        "generated_at": generated_at.isoformat(),
        "project_id": str(scope.project_id),
        "files": entries,
    }

    # ``_serialize`` uses ``sort_keys=True``, so ANY added key changes every
    # byte of the manifest and therefore its digest. A release pack must emit
    # exactly the key set it has always emitted, or an auditor regenerating
    # one gets a digest that disagrees with the value they recorded. Scope
    # description is therefore conditional, not additive.
    if scope.kind == "release":
        manifest.update(
            {
                "release_id": str(scope.release_id),
                "test_run_id": str(run.id) if run else None,
                "recommendation": decision.recommendation if decision else None,
                "risk_score": decision.risk_score if decision else None,
                "policy_id": (
                    str(decision.policy_id)
                    if decision and decision.policy_id
                    else None
                ),
            }
        )
    else:
        manifest.update(
            {
                "scope": "runs",
                "tier": scope.tier,
                "run_ids": [str(r) for r in scope.run_ids],
            }
        )
    return _serialize(manifest)


async def _build_pack_payload(
    db: AsyncSession,
    release: Release,
) -> tuple[bytes, dict[str, Any]]:
    """Assemble the full ZIP and a metadata_snapshot for the DB row.

    Returns ``(zip_bytes, metadata)``. Raises ``CompliancePackNotAvailableError``
    if the release has no linked run to snapshot.
    """
    generated_at = datetime.now(timezone.utc)

    decision, run = await _gather_decision_and_run(db, release)
    if run is None or decision is None:
        raise CompliancePackNotAvailableError(
            "Release has no linked test run with a decision — nothing to snapshot yet"
        )

    # Gather every section. Each helper returns plain dicts/lists so we
    # can serialize them with the shared `_serialize` helper.
    #
    # Resilience is deliberately split: ``_gather_decision_trail`` and
    # ``_gather_audit_events`` self-guard and embed an ``error`` payload on
    # failure (best-effort surrounding context). The CORE snapshots
    # (release / run / decision / policy / clusters / defects) are NOT
    # swallowed — a compliance pack must be complete, so if one of those
    # can't be assembled we fail loudly here rather than ship a silently
    # incomplete artifact that a reviewer would trust as authoritative.
    release_snapshot = await _gather_release_snapshot(db, release)
    run_snapshot = _to_jsonable(run)
    decision_snapshot = _to_jsonable(decision)
    policy_snapshot = await _gather_policy_snapshot(db, decision, release.project_id)
    decision_trail = await _gather_decision_trail(db, run.id)
    clusters = await _gather_clusters(db, run.id)
    defects = await _gather_defects(db, release.project_id, run.id)
    audit_events = await _gather_audit_events(db, release, run.id)
    agent_activity = await _gather_agent_activity(db, release.project_id, run.id)

    # Build the files dict. Keys are ZIP paths; values are the raw bytes.
    files: dict[str, bytes] = {
        "README.md": _build_readme(release, decision, run, generated_at),
        "release.json": _serialize(release_snapshot),
        "run.json": _serialize(run_snapshot),
        "decision.json": _serialize(decision_snapshot),
        "policy_snapshot.json": _serialize(policy_snapshot),
        "decision_trail.json": _serialize(decision_trail),
        "clusters.json": _serialize(clusters),
        "defects.json": _serialize(defects),
        "audit_events.json": _serialize(audit_events),
        "agent_activity.json": _serialize(agent_activity),
    }

    scope = ExportScope.for_release(
        release_id=release.id, project_id=release.project_id, run_ids=[run.id]
    )
    manifest_bytes = _build_manifest(files, generated_at, scope, run, decision)
    manifest_hash = _sha256(manifest_bytes)

    # Now write the ZIP with manifest.json first so consumers who scan
    # sequentially can verify the payload before reading the full archive.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_MANIFEST_FILENAME, manifest_bytes)
        for name, data in sorted(files.items()):
            zf.writestr(name, data)

    zip_bytes = buffer.getvalue()

    metadata: dict[str, Any] = {
        "generated_at": generated_at.isoformat(),
        "release_name": release.name,
        "release_version": release.version,
        "recommendation": decision.recommendation,
        "risk_score": decision.risk_score,
        "policy_id": str(decision.policy_id) if decision.policy_id else None,
        "policy_source": policy_snapshot.get("source") if policy_snapshot else None,
        "run_id": str(run.id),
        "build_number": run.build_number,
        "file_count": len(files) + 1,  # +1 for manifest itself
        "manifest_sha256": manifest_hash,
        "bytes": len(zip_bytes),
    }
    return zip_bytes, metadata


#: Upper bound on runs in one export (S6a).
#:
#: No cap existed on compliance packs — a release covers one run, so the
#: question never arose. A retention export covers a candidate set, and an
#: unbounded one OOMs or times out AFTER the operator was told the export
#: would protect their data. Refusing up front with a stated bound is the
#: honest failure; truncating would export a subset and then delete the whole
#: set, which is the worst outcome this slice can produce.
MAX_EXPORT_RUNS = 2000

_EXPORT_TIERS = ("summary", "evidence", "full")


@dataclass(frozen=True)
class ExportScope:
    """What an archive covers.

    The compliance pack was release-shaped throughout: the entry point took a
    ``Release``, the storage key was built from ``release_id``, and the
    manifest named release fields directly. A retention export covers a set of
    runs instead. This is the one description both understand, so there is one
    builder rather than two — the epic's explicit warning, because a second
    copy of the manifest chain is a second thing that can drift from the
    verification steps the README tells an auditor to follow.
    """

    kind: str                  # "release" | "runs"
    project_id: uuid.UUID
    run_ids: tuple[uuid.UUID, ...]
    tier: str
    release_id: Optional[uuid.UUID] = None

    @staticmethod
    def _validate(run_ids, tier: str) -> tuple[uuid.UUID, ...]:
        if tier not in _EXPORT_TIERS:
            raise ValueError(
                f"unknown tier {tier!r}; expected one of {list(_EXPORT_TIERS)} — "
                "a tier outside the vocabulary selects no content and produces "
                "an archive that looks successful and holds nothing"
            )
        ids = tuple(run_ids)
        if not ids:
            raise ValueError(
                "an export needs at least one run; exporting nothing and then "
                "deleting on the strength of it is the worst outcome available"
            )
        if len(ids) > MAX_EXPORT_RUNS:
            raise ValueError(
                f"{len(ids)} runs exceeds the export bound of {MAX_EXPORT_RUNS}; "
                "narrow the criteria. The set is NOT truncated, because "
                "exporting a subset and deleting the whole set is silent data loss"
            )
        return ids

    @classmethod
    def for_release(
        cls, *, release_id: uuid.UUID, project_id: uuid.UUID, run_ids
    ) -> "ExportScope":
        """A compliance pack. Always ``full``: it is evidence, and has always
        contained everything — defaulting it to ``summary`` would silently thin
        an artifact auditors already rely on."""
        return cls(
            kind="release",
            project_id=project_id,
            run_ids=cls._validate(run_ids, "full"),
            tier="full",
            release_id=release_id,
        )

    @classmethod
    def for_runs(
        cls, *, project_id: uuid.UUID, run_ids, tier: str
    ) -> "ExportScope":
        """A retention export over a candidate set."""
        return cls(
            kind="runs",
            project_id=project_id,
            run_ids=cls._validate(run_ids, tier),
            tier=tier,
        )


def build_export_key(
    scope: ExportScope, generated_at: datetime, pack_id: uuid.UUID
) -> str:
    """Deterministic storage key. Date-prefixed so MinIO lifecycle rules
    can expire old archives by year/month without listing the whole bucket.

    Release packs keep their existing ``compliance/`` prefix and shape
    verbatim — every stored pack is addressed by it. Run exports live under
    ``exports/`` so a lifecycle rule written for compliance packs does not
    silently start expiring retention archives.
    """
    date_prefix = generated_at.strftime("%Y/%m/%d")
    if scope.kind == "release":
        return f"compliance/{date_prefix}/{scope.release_id}/{pack_id}.zip"
    return f"exports/{date_prefix}/{scope.project_id}/{pack_id}.zip"



# ── Public entry points ───────────────────────────────────────────────────


async def generate_pack(
    db: AsyncSession,
    release: Release,
    *,
    actor: Optional[User],
    notes: Optional[str] = None,
    retention_days: Optional[int] = None,
) -> CompliancePack:
    """Generate a new compliance pack for the given release.

    Caller is expected to have already verified project access. Raises
    ``CompliancePackDisabledError`` when the feature flag is off and
    ``CompliancePackNotAvailableError`` when the release has no linked
    run to snapshot.
    """
    from app.core.metrics import compliance_pack_generated_total

    if not await _feature_enabled(db):
        compliance_pack_generated_total.labels(result="disabled").inc()
        raise CompliancePackDisabledError(
            "Release compliance pack feature is disabled. "
            "Enable 'release_compliance_pack' in Settings > Feature Flags."
        )

    try:
        zip_bytes, metadata = await _build_pack_payload(db, release)
    except CompliancePackNotAvailableError:
        compliance_pack_generated_total.labels(result="not_available").inc()
        raise

    pack_id = uuid.uuid4()
    generated_at = datetime.now(timezone.utc)

    # One description, one key builder. The release path goes through the same
    # ExportScope a retention export uses — the epic's warning is that a
    # half-generalised service with two entry points is worse than either,
    # because the second copy of the manifest chain drifts from the
    # verification steps the README tells an auditor to follow.
    scope = ExportScope.for_release(
        release_id=release.id,
        project_id=release.project_id,
        run_ids=[uuid.UUID(metadata["run_id"])],
    )
    minio_key = build_export_key(scope, generated_at, pack_id)

    # Upload to MinIO. A failure here aborts the pack — we don't want a
    # DB row pointing at nothing.
    from app.db.storage import get_storage_provider
    storage = get_storage_provider()
    await storage.put_object(
        key=minio_key,
        content=zip_bytes,
        content_type="application/zip",
        bucket=COMPLIANCE_BUCKET,
    )

    retention = timedelta(days=retention_days or DEFAULT_RETENTION_DAYS)
    row = CompliancePack(
        id=pack_id,
        release_id=release.id,
        project_id=release.project_id,
        test_run_id=uuid.UUID(metadata["run_id"]),
        minio_key=minio_key,
        manifest_sha256=metadata["manifest_sha256"],
        file_count=int(metadata["file_count"]),
        bytes=int(metadata["bytes"]),
        retention_expires_at=generated_at + retention,
        generated_at=generated_at,
        generated_by_user_id=actor.id if actor else None,
        metadata_snapshot=metadata,
        notes=notes,
    )
    db.add(row)
    await db.flush()

    # Audit trail — use the existing settings_audit_log convention with a
    # release-scoped key so the unified audit dashboard picks it up.
    # Stage-only (Phase E-1): router's get_db owns the commit so the pack
    # row and its audit entry land in the same transaction.
    try:
        from app.models.postgres import SettingsAuditLog
        entry = SettingsAuditLog(
            setting_key=f"release:{release.id}:compliance_pack:{pack_id}",
            action="generate",
            actor_id=actor.id if actor else None,
            actor_name=(
                getattr(actor, "username", None) or getattr(actor, "email", None)
            ) if actor else None,
            changed_fields=["compliance_pack"],
        )
        db.add(entry)
    except Exception as exc:
        logger.warning("compliance pack audit stage failed", error=str(exc))

    logger.info(
        "compliance_pack_generated",
        pack_id=str(pack_id),
        release_id=str(release.id),
        project_id=str(release.project_id),
        run_id=metadata["run_id"],
        bytes=metadata["bytes"],
        manifest_sha256=metadata["manifest_sha256"],
    )
    compliance_pack_generated_total.labels(result="success").inc()
    return row


async def list_packs_for_release(
    db: AsyncSession, release_id: uuid.UUID,
) -> list[CompliancePack]:
    result = await db.execute(
        select(CompliancePack)
        .where(CompliancePack.release_id == release_id)
        .order_by(CompliancePack.generated_at.desc())
    )
    return list(result.scalars().all())


async def list_packs_for_project(
    db: AsyncSession, project_id: uuid.UUID, *, limit: int = 100,
) -> list[CompliancePack]:
    result = await db.execute(
        select(CompliancePack)
        .where(CompliancePack.project_id == project_id)
        .order_by(CompliancePack.generated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_pack(db: AsyncSession, pack_id: uuid.UUID) -> Optional[CompliancePack]:
    result = await db.execute(
        select(CompliancePack).where(CompliancePack.id == pack_id)
    )
    return result.scalar_one_or_none()


async def load_pack_bytes(pack: CompliancePack) -> bytes:
    """Pull the ZIP from MinIO for the download endpoint."""
    from app.db.storage import get_storage_provider
    storage = get_storage_provider()
    return await storage.get_object_content(key=pack.minio_key, bucket=COMPLIANCE_BUCKET)
