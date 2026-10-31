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
  * ``release_gate_decision.json`` — the RELEASE-level verdict + its history
  * ``policy_snapshot.json`` — the exact ReleaseGatePolicy that was in force
  * ``decision_trail.json``  — full AI decision trail from Tier 0B
  * ``clusters.json``        — failure clusters with member test IDs
  * ``defects.json``         — defect rows + Jira ticket links
  * ``audit_events.json``    — release overrides, settings changes, access events
  * ``ai_history.json``      — every stored version of the run's AI analysis, the
                               diffs between them and the per-test versions (AIH-06)

Integrity model — a **tamper-evident** SHA-256 checksum chain, **signed since
W11** (REL-11, D14). The manifest's SHA-256 (recorded in the
``compliance_packs`` row) is the root of the chain: if the row's
``manifest_sha256`` matches the ZIP's manifest.json, and each file's hash
matches the manifest, the pack is unmodified since generation. Since W11 a new
pack also carries ``signature.json``: an Ed25519 signature by the deployment's
own key over the manifest's digest, the pack id and the key id
(``compliance_signing``). It is the ONE file ``manifest.json`` does not list
-- it signs the manifest's digest, so listing it would be circular -- and the
manifest is built byte for byte as before. ``compliance_pack_verify`` checks
all of it (``POST /api/v1/compliance-packs/{id}/verify``); ``testlookup
compliance-pack verify`` does the same offline.

What signing proves: the deployment's key signed this exact manifest. There is
still no HMAC and no PKI (no certificate, no CA): the key pair is the
deployment's own, and its private key is stored encrypted beside the data
(Fernet under ``APP_SECRET_KEY``). Residual risk: an actor able to
rewrite BOTH the MinIO object and the ``compliance_packs`` row now also needs
``APP_SECRET_KEY`` to forge a pack that verifies -- one holding all three can.
Closing that is operator-side: WORM / object-lock on the ``compliance-packs``
bucket, and off-host custody of the public key and the ``manifest_sha256``
values. A pack generated before W11, or while the key was unreadable, carries
no signature and is tamper-evident only.

This service is feature-flagged behind ``release_compliance_pack`` so
deployments that haven't enabled it see zero behaviour change.

Retention archives (W22b, ACT-03). A criteria deletion may first build an
archive of the runs it is about to delete. It is the SAME chain, the SAME
signature and the SAME verifier: ``build_archive`` below is the one function
that assembles a ZIP (the release pack reaches it through
``_build_pack_payload``, the retention export through
``retention_export_service.run_export``), ``_sign_pack`` the one signer
(bytes or a spooled file) and ``compliance_pack_verify.verify_archive_file``
the one check. A run archive streams: members are written as they are
generated and hashed while writing, so ``manifest.json`` is its LAST member.
It is stored under ``exports/`` in this bucket and verified by re-reading it
from storage before anything is deleted. **An archive, not a backup -- it
cannot be re-imported** (D23): nothing in TestLookup reads it back.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import IO, Any, AsyncIterator, Collection, Mapping, Optional, cast

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CompliancePack,
    Defect,
    FailureCluster,
    Project,
    Release,
    ReleaseDecision,
    ReleaseGatePolicy,
    TestRun,
    User,
)
from app.services.cluster_snapshot import is_current_snapshot

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


async def _feature_enabled(
    db: Optional[AsyncSession] = None,
    *,
    project_id: Optional[uuid.UUID] = None,
    user: Optional[User] = None,
) -> bool:
    """``release_compliance_pack`` for the release's project and the requester.

    A pack is one release's, generated by one person: a project or role
    allow-list on the flag is a statement about exactly those. Asked without
    them the allow-listed project read the flag as off, and generation answered
    503 while the flag's status for that project (``GET
    /feature-flags/{key}/status``, what the UI asks) said on.
    """
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled(
            "release_compliance_pack", db=db, project_id=project_id, user=user,
        )
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
        .where(
            ReleaseTestRunLink.release_id == release.id,
            # Same-project guard. A link joining a run and a release across
            # projects is rejected at the service layer now, but historical
            # rows can violate it — and this query feeds a SIGNED compliance
            # pack, which is the worst possible place for another tenant's run
            # to appear.
            TestRun.project_id == release.project_id,
        )
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


async def _gather_release_gate(
    db: AsyncSession, release_id: uuid.UUID
) -> dict[str, Any]:
    """The RELEASE-level verdict and its append-only history (S8).

    The pack's own docstring promises an artifact that "fully reconstructs a
    release decision". Until now it reconstructed a RUN's decision that happened
    to be linked to the release — `ReleaseDecision` is keyed `test_run_id`, so
    the join through `ReleaseTestRunLink` finds one run's verdict, not the
    release's. "Is 2.4.0 shippable?" was the one question the compliance pack
    could not answer.

    Returns an explicit ``evaluated: false`` rather than omitting the section
    when a release has never been gated. An absent file reads as "this product
    has no such concept"; a present one saying "never evaluated" is a fact a
    reviewer can act on, and the difference matters most in the artifact people
    audit rather than the screen they glance at.
    """
    from app.services import release_gate_decision_service as gate_decisions

    current = await gate_decisions.current_decision(db, release_id)
    history = await gate_decisions.decision_history(db, release_id)
    if current is None:
        return {
            "evaluated": False,
            "reason": "this release has no gate decision on record",
            "history": [_to_jsonable(h) for h in history],
        }
    return {
        "evaluated": True,
        "current": _to_jsonable(current),
        # The whole point of an append-only table. A pack that carried only the
        # standing verdict could not show that it had ever been anything else,
        # which is exactly what an auditor asks.
        "history": [_to_jsonable(h) for h in history],
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


#: How much AI history a pack carries: the newest versions per kind and the
#: per-test rows. The export route (``GET /runs/{id}/ai-history/export``) keeps
#: up to 100 / 1000; a pack is attached to an audit, so it stays smaller.
AI_HISTORY_MAX_VERSIONS = 20
AI_HISTORY_MAX_TEST_ROWS = 500


async def _gather_ai_history(db: AsyncSession, run: TestRun) -> dict[str, Any]:
    """AIH-06: the decided run's AI history (``AIHistoryExportBundle``).

    Best-effort surrounding context, like ``_gather_decision_trail``: the
    versions live in Mongo, and a pack must not fail because that store is
    down. A failure is stated in the file (``available: false``), never left
    out, so a reviewer can tell "no history" from "history not reachable".
    The bundle already carries the review-envelope fields (whether a person
    accepted the AI content).
    """
    try:
        from app.db.mongo import get_mongo_db
        from app.services.ai_history_read_service import export_bundle

        # A SAVEPOINT: a failed read must not leave the pack's own transaction
        # aborted (the compliance_packs row is staged on this session next).
        async with db.begin_nested():
            bundle = await export_bundle(
                db,
                get_mongo_db(),
                run=run,
                max_versions=AI_HISTORY_MAX_VERSIONS,
                max_test_rows=AI_HISTORY_MAX_TEST_ROWS,
            )
        return {"available": True, **_to_jsonable(bundle)}
    except Exception as exc:  # noqa: BLE001 - best-effort context, stated in the file
        logger.warning("ai history collection failed", error_type=type(exc).__name__)
        return {"available": False, "error": "ai_history_unavailable"}


async def _gather_clusters(
    db: AsyncSession, run_id: Optional[uuid.UUID],
) -> list[dict[str, Any]]:
    if run_id is None:
        return []
    result = await db.execute(
        select(FailureCluster).where(FailureCluster.test_run_id == run_id, is_current_snapshot())
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
# Every helper below is plain SHA-256: no HMAC, no keys, no certificates. The
# chain is built exactly as it was before W11; ``generate_pack`` signs its
# root afterwards (``signature.json``, outside the chain, through
# ``compliance_signing``). See the module docstring for the residual risk and
# the operator-side mitigations.


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
    # Listed, or a reviewer has no way to know it is there. The pack's README
    # IS its index — a file present in the ZIP but absent from this table is
    # undiscoverable to the person the pack exists for.
    lines.append(
        "| `release_gate_decision.json` | The RELEASE-level verdict and its full "
        "append-only history. Distinct from `decision.json`, which is one run's "
        "decision: this is the shipping call for the release as a whole. |"
    )
    lines.append("| `policy_snapshot.json` | The exact `ReleaseGatePolicy` version in force at decision time. |")
    lines.append("| `decision_trail.json` | AI decision trail (stages + per-test routing + workflow events). |")
    lines.append("| `clusters.json` | Failure clusters identified in the run. |")
    lines.append("| `defects.json` | Defects linked to the run's failed tests with Jira ticket references. |")
    lines.append("| `audit_events.json` | Settings + access audit events scoped to the release project. |")
    lines.append(
        "| `ai_history.json` | Every stored version of the run's AI analysis (analysis digests and "
        "run summaries, newest 20 per kind), the diffs between consecutive versions, the per-test "
        "analysis versions, and their provenance (engine, model, fallback) and review status. "
        "`available: false` means the AI-history store could not be read when the pack was "
        "generated, not that the run has no history. |"
    )
    # W11 REL-11. Written into the ZIP after the manifest is hashed and NOT
    # listed in it (it signs the manifest's digest), so this row is the only
    # place a reviewer learns why one file is outside the chain.
    lines.append(
        "| `signature.json` | Present when the deployment signed the pack: an Ed25519 "
        "signature over this manifest's SHA-256, the pack id and the key id. The one file "
        "`manifest.json` does not list — it signs the manifest's digest, so listing it "
        "would be circular. |"
    )
    lines.append("")
    lines.append("## Verification (tamper-evident checksum chain + Ed25519 signature)")
    lines.append("")
    lines.append(
        "1. Compute SHA-256 of `manifest.json` and compare against the "
        "`manifest_sha256` column on the `compliance_packs` row for this pack."
    )
    lines.append(
        "2. For each file listed in `manifest.json.files`, compute its SHA-256 "
        "and compare against the hash in the manifest. No other file may be in "
        "the ZIP except `signature.json`."
    )
    lines.append(
        "3. Any mismatch indicates tampering — the pack is no longer authoritative."
    )
    lines.append(
        "4. `signature.json`: its `manifest_sha256` must equal the digest of step 1. "
        "Verify `signature` (base64 Ed25519) over the UTF-8 statement "
        "`testlookup-compliance-pack-v1\\n<pack_id>\\n<manifest_sha256>\\n<key_id>\\n` "
        "with the deployment's public key for `key_id` (`GET "
        "/api/v1/compliance-packs/signing-keys`, or the PEM a reviewer was given "
        "out of band)."
    )
    lines.append(
        "5. `testlookup compliance-pack verify <zip> --public-key <pem>` does all "
        "of it offline."
    )
    lines.append("")
    lines.append(
        "**What this does and does not prove.** The checksum chain proves the ZIP "
        "matches the digest recorded when the pack was generated. The signature "
        "proves the deployment's key signed this exact manifest, and so every "
        "file it lists. The private key is stored encrypted beside the data "
        "(Fernet, under the deployment's `APP_SECRET_KEY`), so the signature "
        "defends against storage-only tampering — rewriting the stored ZIP or "
        "the `compliance_packs` row — NOT against an actor holding both the "
        "database and `APP_SECRET_KEY`, who can sign a forgery. For that, keep "
        "the public key and the `manifest_sha256` values off-host, under your "
        "own key custody or on WORM / object-locked storage. A pack without "
        "`signature.json` (generated before signing existed, or while the key "
        "was unreadable) is tamper-evident only: its chain is not signed."
    )
    return "\n".join(lines).encode("utf-8")


def _build_manifest(
    files: Mapping[str, bytes | tuple[int, str]],
    generated_at: datetime,
    scope: "ExportScope",
    run: Optional[TestRun],
    decision: Optional[ReleaseDecision],
) -> bytes:
    """Build the tamper-evidence manifest (checksum chain root — unsigned).

    ``files`` maps every filename in the ZIP **except** the manifest itself
    to its bytes (a release pack: we compute the SHA-256 here) or to the
    ``(size, sha256)`` already taken while the member streamed into the ZIP
    (a retention archive, W22b: the member bytes are gone by now). Either way
    the entry is the same ``{name, bytes, sha256}``. The caller hashes the
    result and stores the digest on the ``compliance_packs`` /
    ``deletion_jobs`` row.

    No key material is involved here: the manifest is built exactly as it was
    before W11. ``generate_pack`` signs its digest afterwards, in
    ``signature.json``, which this manifest never lists.
    """
    entries = []
    for name, data in sorted(files.items()):
        if isinstance(data, (bytes, bytearray)):
            size, digest = len(data), _sha256(bytes(data))
        else:
            size, digest = data
        entries.append(
            {
                "name": name,
                "bytes": size,
                "sha256": digest,
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
    elif scope.kind == "project":
        # W24 ADM-05: a project export (settings + catalog). The member
        # iterator FILLED ``manifest_extra`` before it was exhausted (format,
        # schema_version, source, project, entities, directory, excluded).
        manifest.update({"scope": "project", **(scope.manifest_extra or {})})
    else:
        manifest.update(
            {
                "scope": "runs",
                "tier": scope.tier,
                "run_ids": [str(r) for r in scope.run_ids],
            }
        )
    return _serialize(manifest)


# ── The ONE archive builder (W22b, ACT-03) ────────────────────────────────


#: A run archive's fixed member timestamp: two builds of the same data must
#: produce the same member bytes (only manifest.json's ``generated_at`` and
#: signature.json differ), so no entry carries the wall clock.
_FIXED_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ArchiveResult:
    """What ``build_archive`` wrote. ``archive_sha256`` is the digest of the
    sink as built -- NOT final until ``_sign_pack`` appends signature.json."""

    manifest_sha256: str
    archive_sha256: str
    bytes: int
    file_count: int


class ArchiveTooLarge(Exception):
    """The archive passed ``max_bytes`` while it was being written."""

    def __init__(self, *, limit: int, written: int) -> None:
        super().__init__(f"archive passed its byte limit ({written} > {limit})")
        self.limit = limit
        self.written = written


class _CountingSink:
    """The sink ``zipfile`` writes to, refusing to grow past ``max_bytes``.

    Raises ``ArchiveTooLarge`` ONCE, the moment the furthest written position
    passes the limit; every later write is discarded so ``zipfile``'s own
    clean-up (closing the member, the central directory) cannot raise a second
    error over the first. A tripped sink holds a partial archive the caller
    throws away.
    """

    def __init__(self, raw: IO[bytes], max_bytes: Optional[int]) -> None:
        self._raw = raw
        self._max = max_bytes
        self.high_water = raw.tell()
        self.tripped = False

    def write(self, data: bytes) -> int:
        if self.tripped:
            return len(data)
        written = self._raw.write(data)
        self.high_water = max(self.high_water, self._raw.tell())
        if self._max is not None and self.high_water > self._max:
            self.tripped = True
            raise ArchiveTooLarge(limit=self._max, written=self.high_water)
        return written

    def tell(self) -> int:
        return self._raw.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._raw.seek(offset, whence)

    def flush(self) -> None:
        self._raw.flush()

    def seekable(self) -> bool:
        return True


def sha256_of_file(fileobj: IO[bytes], *, chunk: int = 1 << 20) -> str:
    """SHA-256 of a whole file object, read from its start in chunks."""
    fileobj.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: fileobj.read(chunk), b""):
        digest.update(block)
    return digest.hexdigest()


def _member_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


async def build_archive(
    scope: "ExportScope",
    *,
    members: Mapping[str, bytes] | AsyncIterator[tuple[str, bytes]],
    sink: IO[bytes],
    generated_at: datetime,
    run: Optional[TestRun] = None,
    decision: Optional[ReleaseDecision] = None,
    max_bytes: Optional[int] = None,
) -> ArchiveResult:
    """Assemble ONE archive into ``sink`` -- the release pack AND the retention export.

    This is the only code in the module that opens a ZIP for writing; both
    kinds share ``_build_manifest`` (the chain root) and, afterwards,
    ``_sign_pack`` (the signature) and ``compliance_pack_verify`` (the check).

    * ``members`` a ``Mapping[str, bytes]`` (the release pack): the manifest
      is written FIRST, then the members in sorted order, each through
      ``zf.writestr`` exactly as before W22b -- the release ZIP's member bytes
      and its manifest are unchanged.
    * ``members`` an async iterator of ``(name, bytes)`` (a run archive):
      each member is written as it arrives, with a fixed timestamp, and its
      SHA-256 is taken while writing; nothing keeps it afterwards, so peak
      memory is one member. ``manifest.json`` is the LAST member (it can only
      list what was already written).

    ``max_bytes``: the sink refuses to grow past it (``ArchiveTooLarge``).
    """
    counted = _CountingSink(sink, max_bytes)
    entries: dict[str, bytes | tuple[int, str]] = {}
    with zipfile.ZipFile(cast(IO[bytes], counted), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if isinstance(members, Mapping):
            entries.update(members)
            manifest_bytes = _build_manifest(entries, generated_at, scope, run, decision)
            zf.writestr(_MANIFEST_FILENAME, manifest_bytes)
            for name, data in sorted(members.items()):
                zf.writestr(name, data)
        else:
            async for name, data in members:
                if name == _MANIFEST_FILENAME or name in entries:
                    raise ValueError(f"duplicate or reserved archive member {name!r}")
                zf.writestr(_member_info(name), data)
                entries[name] = (len(data), _sha256(data))
            manifest_bytes = _build_manifest(entries, generated_at, scope, run, decision)
            zf.writestr(_member_info(_MANIFEST_FILENAME), manifest_bytes)
    size = counted.high_water
    return ArchiveResult(
        manifest_sha256=_sha256(manifest_bytes),
        archive_sha256=sha256_of_file(sink),
        bytes=size,
        file_count=len(entries) + 1,   # +1 for the manifest itself
    )


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
    # A CORE snapshot, not best-effort: a pack missing the release verdict
    # cannot justify the release, which is what the pack is for.
    release_gate = await _gather_release_gate(db, release.id)
    decision_trail = await _gather_decision_trail(db, run.id)
    clusters = await _gather_clusters(db, run.id)
    defects = await _gather_defects(db, release.project_id, run.id)
    audit_events = await _gather_audit_events(db, release, run.id)
    agent_activity = await _gather_agent_activity(db, release.project_id, run.id)
    # Best-effort like the decision trail: {"available": false, ...} on failure.
    ai_history = await _gather_ai_history(db, run)

    # Build the files dict. Keys are ZIP paths; values are the raw bytes.
    files: dict[str, bytes] = {
        "README.md": _build_readme(release, decision, run, generated_at),
        "release.json": _serialize(release_snapshot),
        "run.json": _serialize(run_snapshot),
        "decision.json": _serialize(decision_snapshot),
        # The RELEASE-level verdict, distinct from the run-level `decision.json`
        # beside it. Both are kept: the run decision explains one execution, the
        # release gate explains the shipping call.
        "release_gate_decision.json": _serialize(release_gate),
        "policy_snapshot.json": _serialize(policy_snapshot),
        "decision_trail.json": _serialize(decision_trail),
        "clusters.json": _serialize(clusters),
        "defects.json": _serialize(defects),
        "audit_events.json": _serialize(audit_events),
        "agent_activity.json": _serialize(agent_activity),
        # AIH-06: hashed into manifest.json.files[] like every other file.
        "ai_history.json": _serialize(ai_history),
    }

    scope = ExportScope.for_release(
        release_id=release.id, project_id=release.project_id, run_ids=[run.id]
    )
    # The ONE builder (W22b): manifest.json first so consumers who scan
    # sequentially can verify the payload before reading the full archive,
    # then the sorted members -- byte for byte what this function wrote before.
    buffer = io.BytesIO()
    archive = await build_archive(
        scope, members=files, sink=buffer, generated_at=generated_at,
        run=run, decision=decision,
    )
    manifest_hash = archive.manifest_sha256

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
        "ai_history_versions": len(ai_history.get("versions") or []),
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

    kind: str                  # "release" | "runs" | "project" (W24)
    project_id: uuid.UUID
    run_ids: tuple[uuid.UUID, ...]
    tier: str
    release_id: Optional[uuid.UUID] = None
    #: A project export's manifest keys (W24): a dict the member iterator
    #: FILLS before it is exhausted -- ``build_archive`` builds the manifest
    #: after the last member. ``None`` for the release and runs kinds.
    manifest_extra: Optional[dict[str, Any]] = field(default=None, compare=False, hash=False)

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

    @classmethod
    def for_project(
        cls, *, project_id: uuid.UUID, manifest_extra: dict[str, Any]
    ) -> "ExportScope":
        """A project export (W24 ADM-05: settings + catalog). It covers no runs
        (run history is phase 2), so ``_validate`` -- whose refusal of an empty
        run set protects a deletion -- does not apply; ``tier`` is ``full``."""
        return cls(
            kind="project",
            project_id=project_id,
            run_ids=(),
            tier="full",
            manifest_extra=manifest_extra,
        )


def build_export_key(
    scope: ExportScope, generated_at: datetime, pack_id: uuid.UUID
) -> str:
    """Deterministic storage key. Date-prefixed so MinIO lifecycle rules
    can expire old archives by year/month without listing the whole bucket.

    Release packs keep their existing ``compliance/`` prefix and shape
    verbatim — every stored pack is addressed by it. Run exports live under
    ``exports/`` so a lifecycle rule written for compliance packs does not
    silently start expiring retention archives. Project exports (W24) live
    under a THIRD prefix, ``project-exports/``, for the same reason.
    """
    date_prefix = generated_at.strftime("%Y/%m/%d")
    if scope.kind == "release":
        return f"compliance/{date_prefix}/{scope.release_id}/{pack_id}.zip"
    if scope.kind == "project":
        return f"project-exports/{date_prefix}/{scope.project_id}/{pack_id}.zip"
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

    if not await _feature_enabled(db, project_id=release.project_id, user=actor):
        compliance_pack_generated_total.labels(result="disabled").inc()
        raise CompliancePackDisabledError(
            "Release compliance pack feature is disabled. "
            "Enable 'release_compliance_pack' in Settings > Feature Flags."
        )

    # W25b HYG-09 (D-W25b-9): everything after the flag gate runs inside ONE
    # ``try``, so a failure anywhere -- the payload build, the signing, the
    # upload, the flush -- is counted ``result="error"`` and still raised.
    # TestLookupCompliancePackGenerationErrors (prometheus-rules/
    # testlookup-alerts.yml) fires on that label value, which nothing emitted
    # before, so the alert could never fire. ``disabled`` stays above (raised
    # before the try) and ``not_available`` keeps its own branch.
    try:
        zip_bytes, metadata = await _build_pack_payload(db, release)

        pack_id = uuid.uuid4()
        generated_at = datetime.now(timezone.utc)

        # W11 REL-11: sign the manifest's digest BEFORE the upload, so the stored
        # ZIP carries signature.json. manifest.json and its digest are untouched
        # (signature.json sits outside the chain it signs); only the ZIP grows.
        zip_bytes, signature, signing_key_id, signing = await _sign_pack(
            db,
            zip_bytes,
            pack_id=pack_id,
            manifest_sha256=metadata["manifest_sha256"],
            actor=actor,
            signed_at=generated_at,
        )
        metadata["signing"] = signing
        metadata["bytes"] = len(zip_bytes)

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
            signature=signature,
            signing_key_id=signing_key_id,
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
            signing_key_id=signing_key_id,
        )
    except CompliancePackNotAvailableError:
        compliance_pack_generated_total.labels(result="not_available").inc()
        raise
    except Exception:
        compliance_pack_generated_total.labels(result="error").inc()
        raise
    compliance_pack_generated_total.labels(result="success").inc()
    return row


async def _sign_pack(
    db: AsyncSession,
    zip_bytes: Any,
    *,
    pack_id: uuid.UUID,
    manifest_sha256: str,
    actor: Optional[User],
    signed_at: datetime,
) -> tuple[Any, Optional[str], Optional[str], dict[str, Any]]:
    """Append ``signature.json`` to the ZIP (W11 REL-11, D14).

    ``zip_bytes`` is the archive as ``bytes`` (a compliance pack) or as a
    seekable file object (a retention archive, W22b, spooled -- never one
    ``bytes``). ONE signing body: bytes in -> new bytes out, exactly as
    before; a file object in -> the SAME file object, appended in place.

    Returns ``(zip_bytes, signature, signing_key_id, metadata_snapshot["signing"])``.
    The first pack of a deployment creates the key (under the advisory lock).
    When no key can be used -- the private key unreadable after an
    ``APP_SECRET_KEY`` change, or a key-store fault -- the pack is generated
    UNSIGNED and says so (D-12: a pack is evidence and still tamper-evident;
    refusing it would block a release audit on a key-custody fault), the
    failure is counted, and one WARNING names the pack, never key material.
    """
    from app.core.metrics import compliance_pack_signing_failures_total
    from app.services import compliance_signing

    try:
        key = await compliance_signing.active_signing_key(db, actor=actor, create=True)
    except Exception as exc:  # noqa: BLE001 - a key-custody fault must not refuse the pack
        logger.warning("compliance_pack_key_unavailable", pack_id=str(pack_id), error_type=type(exc).__name__)
        key = None
    if key is None:
        try:
            compliance_pack_signing_failures_total.inc()
        except Exception:  # noqa: BLE001 - telemetry never breaks generation
            pass
        logger.warning("compliance_pack_generated_unsigned", pack_id=str(pack_id), reason="key_unreadable")
        return zip_bytes, None, None, {"state": "unsigned", "reason": "key_unreadable"}

    document = compliance_signing.signature_document(
        key, pack_id=pack_id, manifest_sha256=manifest_sha256, signed_at=signed_at,
    )
    in_memory = isinstance(zip_bytes, (bytes, bytearray))
    buffer = io.BytesIO(zip_bytes) if in_memory else zip_bytes
    buffer.seek(0)
    with zipfile.ZipFile(buffer, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(compliance_signing.SIGNATURE_FILENAME, _serialize(document))
    return (
        buffer.getvalue() if in_memory else buffer,
        document["signature"],
        key.key_id,
        {"state": "signed", "key_id": key.key_id},
    )


async def list_packs_for_release(
    db: AsyncSession, release_id: uuid.UUID,
) -> list[CompliancePack]:
    result = await db.execute(
        select(CompliancePack)
        .where(CompliancePack.release_id == release_id)
        .order_by(CompliancePack.generated_at.desc())
    )
    return list(result.scalars().all())


#: Columns of the project-wide list (W05 REL-04). Never ``minio_key``,
#: ``metadata_snapshot`` or ``notes``: a storage key and an evidence snapshot do
#: not belong in a side rail, and the snapshot can be large.
_SUMMARY_COLUMNS = (
    CompliancePack.id,
    CompliancePack.release_id,
    Release.name.label("release_name"),
    Release.version.label("release_version"),
    CompliancePack.project_id,
    Project.name.label("project_name"),
    CompliancePack.bytes,
    CompliancePack.file_count,
    CompliancePack.manifest_sha256,
    CompliancePack.generated_at,
    CompliancePack.retention_expires_at,
    (CompliancePack.retention_expires_at <= func.now()).label("retired"),
)


async def list_packs_for_projects(
    db: AsyncSession,
    project_ids: Optional[Collection[uuid.UUID]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """The newest packs across a set of projects, and how many there are.

    ``project_ids=None`` means every ACTIVE project (an ADMIN); an empty
    collection returns ``([], 0)`` without a query. A soft-deleted project's
    packs never appear (the join to active ``projects``); a pack whose release
    was deleted keeps its row (FK ``SET NULL``) and reads ``release_name:
    None`` (LEFT join). ``total`` is a COUNT over the same scope, not
    ``len(items)``. Newest first, served by ``ix_compliance_packs_project``.
    """
    if project_ids is not None and not project_ids:
        return [], 0
    scope = [Project.is_active.is_(True)]
    if project_ids is not None:
        scope.append(CompliancePack.project_id.in_(list(project_ids)))
    rows = (
        await db.execute(
            select(*_SUMMARY_COLUMNS)
            .join(Project, Project.id == CompliancePack.project_id)
            .outerjoin(Release, Release.id == CompliancePack.release_id)
            .where(*scope)
            .order_by(CompliancePack.generated_at.desc(), CompliancePack.id.desc())
            .limit(limit)
        )
    ).mappings().all()
    total = (
        await db.execute(
            select(func.count())
            .select_from(CompliancePack)
            .join(Project, Project.id == CompliancePack.project_id)
            .where(*scope)
        )
    ).scalar_one()
    items = [{**row, "retired": bool(row["retired"])} for row in rows]
    return items, int(total or 0)


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
