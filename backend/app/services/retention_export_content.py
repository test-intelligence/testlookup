"""What each retention-export tier contains (W22b, ACT-03 E-3).

``run_members`` yields an archive's members one at a time, for
``compliance_pack_service.build_archive`` to write and hash as they arrive
(``manifest.json`` is added LAST by the builder). Deterministic: rows by id,
keys sorted, ``json.dumps(sort_keys=True, indent=2)``, no wall-clock value in
any member -- two builds of the same data differ only in ``manifest.json``'s
``generated_at`` and in ``signature.json``.

Every tier: ``README.md``, ``deletion.json``, ``runs.json`` (an explicit
column allow-list). **summary** adds ``test_cases/<run_id>.json`` (names
through ``redact_text``; no error text). **evidence** adds
``evidence/<run_id>.json`` (failure text, decision reports and evidence-artifact
metadata through ``evidence_sanitizer.sanitize_persistence_payload``; the AI
run report through ``report_export_sanitizer.sanitize_report_export_payload``).
**full** adds ``raw/<run_id>/<collection>.jsonl`` (raw Mongo documents through
``redact_tree`` -- ``redaction_service``'s key and pattern rules over the
whole tree, no truncation, NOT the report projection),
``objects/<run_id>/<key>`` for every text object of at most 25 MiB (through
``redact_text``; a ``.json`` one that parses through ``redact_tree``) and
``objects_not_archived.json`` for the rest: a binary cannot be scanned for
secrets, so it is listed, never copied. Nothing here reads a secret store.

Stage-only and read-only (``STAGE_ONLY_SERVICES``); never records activity.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, AsyncIterator, Mapping, Optional, Sequence

import structlog
from sqlalchemy import select

from app.db.mongo import Collections
from app.models.postgres import EvidenceArtifact, TestCase, TestRun
from app.services.retention_vocab import ARCHIVE_NOT_BACKUP

_slog = structlog.get_logger(__name__)

#: ``runs.json``: the explicit allow-list (nothing else of a run row leaves).
RUN_COLUMNS = (
    "id", "build_number", "status", "branch", "commit_hash", "environment",
    "trigger_source", "ci_provider", "created_at", "exec_started_at", "exec_finished_at",
    "duration_ms", "total_tests", "passed_tests", "failed_tests", "skipped_tests",
    "broken_tests", "unknown_tests", "pass_rate", "primary_suite_name", "suite_names",
)
#: Run columns a person types (or a CI writes from one): redacted.
RUN_FREE_TEXT = ("build_number", "branch", "environment", "primary_suite_name", "suite_names")
#: ``test_cases/<run_id>.json``: the allow-list. NO error text in summary.
CASE_COLUMNS = (
    "id", "test_name", "full_name", "suite_name", "class_name", "status", "duration_ms",
    "failure_category", "retry_count", "is_flaky_run",
)
CASE_FREE_TEXT = ("test_name", "full_name", "suite_name", "class_name")
#: evidence: the failure text read beside the case columns.
FAILURE_COLUMNS = ("error_message", "stack_trace")
FAILED_STATUSES = ("FAILED", "BROKEN")
#: evidence: an artifact's METADATA (never its content).
EVIDENCE_ARTIFACT_COLUMNS = (
    "id", "test_case_id", "cluster_id", "artifact_type", "source_system", "uri_or_ref",
    "summary_excerpt", "relevance_score", "content_sha256", "content_size_bytes",
    "media_type", "sensitivity", "observed_at", "created_at",
)
#: full: raw collections, keyed by the run or by its test cases.
#: (W25d HYG-02: execution_logs and raw_testng_xml left these -- nothing ever
#: wrote them. An older archive that lists their parts still verifies: the
#: verifier reads the archive's own manifest, not these tuples.)
RAW_BY_RUN = (Collections.RAW_ALLURE_JSON,)
RAW_BY_CASE = (Collections.REST_API_PAYLOADS,)
MONGO_ID_CHUNK = 500

TIER_FILES = {
    "summary": ("test_cases/<run_id>.json",),
    "evidence": ("test_cases/<run_id>.json", "evidence/<run_id>.json"),
    "full": (
        "test_cases/<run_id>.json", "evidence/<run_id>.json",
        "raw/<run_id>/<collection>.jsonl", "objects/<run_id>/<key>", "objects_not_archived.json",
    ),
}
_FILE_PURPOSES = {
    "README.md": "This file.",
    "deletion.json": "The deletion job: criteria, who asked and when, tier, target, runs excluded by a hold, runs already gone.",
    "runs.json": "Each archived run's identifying columns and totals.",
    "test_cases/<run_id>.json": "Each test case of the run: names, status, duration, failure category. No error text.",
    "evidence/<run_id>.json": "Failure messages and stack traces, decision reports, evidence-artifact metadata and the AI run report — redacted and bounded.",
    "raw/<run_id>/<collection>.jsonl": "The run's raw stored documents, one per line, secrets redacted.",
    "objects/<run_id>/<key>": "Each text attachment of at most 25 MiB, secrets redacted.",
    "objects_not_archived.json": "Every other attachment (binary or too large): listed with its size, not copied.",
}


def _jsonable(value: Any) -> Any:
    """Plain JSON types only (ObjectId, Decimal, bytes -> str; enums -> value)."""
    if value is None or (isinstance(value, (bool, int, float, str)) and not isinstance(value, Enum)):
        return value
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _dump(value: Any) -> bytes:
    return json.dumps(_jsonable(value), sort_keys=True, indent=2).encode("utf-8")


def _redacted(value: Any) -> Any:
    from app.services.redaction_service import redact_text

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redacted(v) for v in value]
    return value


def build_readme(*, tier: str, deletion: Mapping[str, Any], run_count: int) -> bytes:
    """The archive's index. Deterministic: no wall-clock value."""
    lines = [
        "# TestLookup deletion archive",
        "",
        f"**Project:** {deletion.get('project_name') or ''} ({deletion.get('project_id')})",
        f"**Deletion job:** {deletion.get('job_id')}",
        f"**Runs archived:** {run_count}",
        f"**Tier:** {tier}",
        f"**Target:** {deletion.get('target')}",
        "",
        f"**{ARCHIVE_NOT_BACKUP}** Nothing in TestLookup reads this archive back; it is a",
        "record of what was deleted, kept for audit and reference.",
        "",
        "## Redaction",
        "",
        "Secret-like values (passwords, tokens, API keys, credentials, private keys,",
        "authorization headers) are replaced with `[REDACTED]` in every text field of",
        "every member. Binary attachments (screenshots, ZIP files, anything without a",
        "text suffix) cannot be scanned for secrets, so they are never copied: they are",
        "listed in `objects_not_archived.json` with their size. The deployment's own",
        "keys and secrets never enter an archive.",
        "",
        "## Files",
        "",
        "| File | Purpose |",
        "|------|---------|",
        "| `manifest.json` | SHA-256 of every other file; the LAST member (members are hashed as they are written). |",
        "| `signature.json` | When the deployment signed the archive: an Ed25519 signature over the manifest's SHA-256. |",
    ]
    for name in ("README.md", "deletion.json", "runs.json", *TIER_FILES.get(tier, ())):
        lines.append(f"| `{name}` | {_FILE_PURPOSES[name]} |")
    lines += [
        "",
        "## Verify",
        "",
        "Run `testlookup compliance-pack verify <zip> --manifest-sha256 <digest>` with the",
        "manifest digest TestLookup showed when the archive was collected (the",
        "`X-Manifest-Sha256` header of the download). It checks the same chain as a",
        "compliance pack: the manifest's SHA-256, every file it lists, and the signature.",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


async def _run_rows(db: Any, project_id: Any, run_ids: Sequence[uuid.UUID]) -> list[dict[str, Any]]:
    """The archived runs' allow-listed columns, ordered by id, free text redacted."""
    columns = [getattr(TestRun, name) for name in RUN_COLUMNS]
    rows = (
        await db.execute(
            select(*columns)
            .where(TestRun.project_id == project_id, TestRun.id.in_(list(run_ids)))
            .order_by(TestRun.id)
        )
    ).all()
    out = []
    for row in rows:
        item = dict(zip(RUN_COLUMNS, row))
        for name in RUN_FREE_TEXT:
            item[name] = _redacted(item.get(name))
        out.append(item)
    return out


async def _case_rows(db: Any, run_id: uuid.UUID, *, with_failures: bool) -> list[dict[str, Any]]:
    names = CASE_COLUMNS + (FAILURE_COLUMNS if with_failures else ())
    rows = (
        await db.execute(
            select(*[getattr(TestCase, name) for name in names])
            .where(TestCase.test_run_id == run_id)
            .order_by(TestCase.id)
        )
    ).all()
    out = []
    for row in rows:
        item = dict(zip(names, row))
        for name in CASE_FREE_TEXT:
            item[name] = _redacted(item.get(name))
        out.append(item)
    return out


async def _find_all(collection: Any, query: Mapping[str, Any]) -> list[Any]:
    return [doc async for doc in collection.find(dict(query), sort=[("_id", 1)])]


async def _evidence_member(db: Any, mongo: Any, run_id: uuid.UUID,
                           cases: Sequence[Mapping[str, Any]]) -> bytes:
    """``evidence/<run_id>.json``: bounded, redacted failure evidence."""
    from app.services.evidence_sanitizer import sanitize_persistence_payload
    from app.services.report_export_sanitizer import sanitize_report_export_payload

    failures = [
        {name: case.get(name) for name in ("id", "test_name", "status", *FAILURE_COLUMNS)}
        for case in cases
        if _jsonable(case.get("status")) in FAILED_STATUSES
    ]
    artifact_rows = (
        await db.execute(
            select(*[getattr(EvidenceArtifact, name) for name in EVIDENCE_ARTIFACT_COLUMNS])
            .where(EvidenceArtifact.run_id == run_id)
            .order_by(EvidenceArtifact.id)
        )
    ).all()
    artifacts = [dict(zip(EVIDENCE_ARTIFACT_COLUMNS, row)) for row in artifact_rows]
    reports = await _find_all(mongo[Collections.DECISION_REPORTS], {"test_run_id": str(run_id)})
    summary = await mongo[Collections.RUN_SUMMARIES].find_one({"test_run_id": str(run_id)})
    payload = {
        "run_id": str(run_id),
        "failures": sanitize_persistence_payload(_jsonable(failures))[0],
        "decision_reports": sanitize_persistence_payload(_jsonable(reports))[0],
        "evidence_artifacts": sanitize_persistence_payload(_jsonable(artifacts))[0],
        "ai_report": (
            sanitize_report_export_payload(_jsonable(summary)) if isinstance(summary, Mapping) else None
        ),
    }
    return _dump(payload)


_REDACT_MAX_DEPTH = 10


def redact_tree(value: Any, *, _depth: int = 0) -> Any:
    """Key + pattern redaction of a whole JSON-like tree, with NO truncation.

    The rules are ``redaction_service``'s own (``SENSITIVE_KEYS`` by exact key,
    normalised like ``redact_dict``; ``redact_text`` on every string), applied
    to EVERY list item: ``redact_dict`` keeps only the first 100 items of a
    list, which would silently cut a ``full`` copy. Past the depth cap the
    subtree is replaced (fail closed), as ``redact_dict`` does.
    """
    from app.services.redaction_service import REDACTED, SENSITIVE_KEYS, redact_text

    if _depth >= _REDACT_MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, nested in value.items():
            name = str(key)
            if name.lower().replace("-", "_") in SENSITIVE_KEYS:
                out[name] = REDACTED
            else:
                out[name] = redact_tree(nested, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_tree(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _jsonl(docs: Sequence[Any]) -> bytes:
    """One raw document per line, redacted by key and pattern (no truncation)."""
    lines = [json.dumps(_jsonable(redact_tree(doc)), sort_keys=True) for doc in docs]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _redacted_object(key: str, raw: bytes) -> bytes:
    """A copied text attachment. A ``.json`` one that parses is redacted as a
    tree (``redact_text`` alone misses ``"password": "…"``); anything else as
    text (UTF-8, ``errors="replace"``)."""
    from app.services.redaction_service import redact_text

    text = raw.decode("utf-8", errors="replace")
    if key.lower().endswith(".json"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_jsonable(redact_tree(parsed)), sort_keys=True, indent=2).encode("utf-8")
    return redact_text(text).encode("utf-8")


async def _raw_members(mongo: Any, run_id: uuid.UUID,
                       case_ids: Sequence[str]) -> AsyncIterator[tuple[str, bytes]]:
    for name in RAW_BY_RUN:
        docs = await _find_all(mongo[name], {"test_run_id": str(run_id)})
        yield f"raw/{run_id}/{name}.jsonl", _jsonl(docs)
    for name in RAW_BY_CASE:
        docs = []
        for i in range(0, len(case_ids), MONGO_ID_CHUNK):
            chunk = list(case_ids[i:i + MONGO_ID_CHUNK])
            docs.extend(await _find_all(mongo[name], {"test_case_id": {"$in": chunk}}))
        docs.sort(key=lambda doc: str(doc.get("_id")) if isinstance(doc, Mapping) else "")
        yield f"raw/{run_id}/{name}.jsonl", _jsonl(docs)


async def _object_members(storage: Any, listing: Sequence[dict], *, project_id: Any,
                          run_id: uuid.UUID, minio_prefix: Optional[str],
                          not_archived: list[dict[str, Any]]) -> AsyncIterator[tuple[str, bytes]]:
    """Text attachments copied (redacted); every other one listed, never copied."""
    from app.services.retention_export_service import is_text_object, run_object_keys
    from app.services.retention_vocab import EXPORT_TEXT_SUFFIXES

    used: set[str] = set()
    for key, size, prefix in run_object_keys(listing, project_id=project_id, run_id=run_id,
                                             minio_prefix=minio_prefix):
        if not is_text_object(key, size):
            reason = "binary" if not key.lower().endswith(EXPORT_TEXT_SUFFIXES) else "too_large"
            not_archived.append({"run_id": str(run_id), "key": key, "size": size, "reason": reason})
            continue
        content = await storage.get_object_content(key)
        name = f"objects/{run_id}/{key[len(prefix):]}"
        if name in used:
            name = f"objects/{run_id}/{key}"
        used.add(name)
        yield name, _redacted_object(key, content)


async def run_members(
    db: Any,
    scope: Any,
    *,
    mongo: Any,
    storage: Any,
    deletion: Mapping[str, Any],
    stats: Optional[dict[str, int]] = None,
) -> AsyncIterator[tuple[str, bytes]]:
    """The archive's members for ``scope`` (``ExportScope`` kind ``runs``), in
    order: README, deletion, runs, then per run (by id) its tier members, then
    (full) ``objects_not_archived.json``. ``stats["objects_not_archived"]`` is
    filled for the job record. Any read failure propagates: an archive missing
    what its tier promises must fail the export, not ship incomplete."""
    from app.services.retention_export_service import list_project_objects

    tier = scope.tier
    runs = await _run_rows(db, scope.project_id, scope.run_ids)
    yield "README.md", build_readme(tier=tier, deletion=deletion, run_count=len(runs))
    yield "deletion.json", _dump(deletion)
    yield "runs.json", _dump(runs)

    listing: list[dict] = []
    prefixes: dict[Any, Optional[str]] = {}
    if tier == "full":
        listing = await list_project_objects(storage, project_id=scope.project_id)
        prefix_rows = (
            await db.execute(
                select(TestRun.id, TestRun.minio_prefix).where(TestRun.id.in_(list(scope.run_ids)))
            )
        ).all()
        prefixes = {row[0]: row[1] for row in prefix_rows}

    not_archived: list[dict[str, Any]] = []
    for run in runs:
        run_id = run["id"]
        cases = await _case_rows(db, run_id, with_failures=tier != "summary")
        yield f"test_cases/{run_id}.json", _dump([{k: c.get(k) for k in CASE_COLUMNS} for c in cases])
        if tier in ("evidence", "full"):
            yield f"evidence/{run_id}.json", await _evidence_member(db, mongo, run_id, cases)
        if tier == "full":
            async for member in _raw_members(mongo, run_id, [str(c["id"]) for c in cases]):
                yield member
            async for member in _object_members(storage, listing, project_id=scope.project_id,
                                                run_id=run_id, minio_prefix=prefixes.get(run_id),
                                                not_archived=not_archived):
                yield member
    if tier == "full":
        not_archived.sort(key=lambda entry: (entry["run_id"], entry["key"]))
        yield "objects_not_archived.json", _dump(not_archived)
    if stats is not None:
        stats["objects_not_archived"] = len(not_archived)
