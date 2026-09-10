"""
Unified test data ingestion endpoint.

Accepts either:
  1. JSON body (IngestPayload) — batch of test results from SDKs or scripts
  2. Multipart file upload — JUnit XML, TestNG XML, or Allure JSON

Returns 202 Accepted with run_id. Processing is async via Celery.

Auth: JWT Bearer OR API key (via get_api_key_context).
Project-scoped API keys are restricted to their bound project; non-scoped
keys / JWTs must still be members of the target project (enforced by
``resolve_project_scope``).
"""
import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_api_key_context, get_db, resolve_project_scope
from app.models.postgres import IngestionSource, User
from app.services.activity.service import ActorRef, record as record_activity
from app.models.schemas import BUILD_NUMBER_MAX_LENGTH, IngestPayload, IngestResponse, UploadStatusResponse

router = APIRouter(prefix="/api/v1/ingest", tags=["Ingest"])
logger = structlog.get_logger("routers.ingest")

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
_READ_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB chunks when streaming uploads


async def _read_upload_bounded(file: UploadFile, max_size: int) -> bytes:
    """
    Read an uploaded file in chunks, aborting as soon as the running total
    exceeds ``max_size``. This prevents an attacker from OOM-ing the server
    by POSTing a multi-gigabyte file — the previous implementation called
    ``await file.read()`` unconditionally and only checked the length
    afterwards, so the entire payload was already resident in memory by
    the time the size check could fire.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {max_size // (1024 * 1024)}MB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _require_nonempty_upload(content: bytes) -> None:
    """Reject an empty upload up-front (400 otherwise).

    A zero-byte file auto-detects to ``junit`` (the ``_detect_format`` default)
    and would otherwise be accepted with a 202, then silently parse to zero
    results in the worker. A self-hoster curling the endpoint with a wrong or
    empty path (e.g. ``-F file=@results.xml`` where ``results.xml`` is empty)
    would see success and never learn nothing was ingested. Fail fast instead.
    """
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )


def _parse_project_uuid(raw: str) -> uuid.UUID:
    """Validate a user-supplied project_id string as a UUID (400 otherwise)."""
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project_id — expected a UUID",
        )


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest test results (JSON batch)",
)
async def ingest_batch(
    payload: IngestPayload,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
):
    """
    Accept a JSON batch of test results and queue for async processing.

    The batch is dispatched to a Celery worker which creates the TestRun,
    upserts test cases, runs post-ingestion tagging, and triggers the
    AI analysis pipeline.
    """
    current_user, bound_project_id = auth
    target_project_id = _parse_project_uuid(str(payload.project_id))

    # Project-scoped API key: enforce that ingestion targets the bound project
    if bound_project_id is not None and bound_project_id != target_project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is restricted to a different project",
        )

    # Tenant isolation: verify the caller is actually a member of the target
    # project (or ADMIN). Previously, a non-project-scoped API key or JWT
    # could POST test data into ANY project and pollute another tenant's
    # run history / trigger their AI pipeline.
    await resolve_project_scope(db, current_user, str(target_project_id))

    from app.worker.tasks import ingest_uploaded_results
    from app.db.storage import get_storage_provider

    run_id = await _resolve_run_id(
        db,
        target_project_id,
        payload.build_number,
        ci_provider=payload.ci_provider,
        ci_repo=payload.ci_repo,
        ci_run_url=payload.ci_run_url,
        jenkins_job=payload.jenkins_job,
    )
    import json
    batch_storage_key = f"uploads/{target_project_id}/{run_id}/queued/{uuid.uuid4().hex}.json"
    try:
        await get_storage_provider().put_object(
            batch_storage_key,
            json.dumps(payload.model_dump(), separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("batch_ingest_storage_failed", run_id=run_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload storage is temporarily unavailable",
        ) from exc
    task = ingest_uploaded_results.delay(
        run_id=run_id,
        payload_storage_key=batch_storage_key,
        user_id=str(current_user.id),
    )

    logger.info(
        "batch_ingest_accepted",
        run_id=run_id,
        project_id=payload.project_id,
        result_count=len(payload.results),
        user=current_user.username,
    )

    # Epic ACT: "did my run land?" is the most common question the feed has to
    # answer, and it must be answerable BEFORE the worker finishes. Recorded as
    # an attempt (own session, survives a caller rollback) precisely because a
    # report that arrived and then failed to ingest is the case worth seeing.
    await record_activity(
        None,
        project_id=target_project_id,
        event_type="run.received",
        actor=ActorRef.from_user(current_user),
        entity_id=run_id,
        entity_label=f"Build {payload.build_number}",
        context={"result_count": len(payload.results), "source": "json_batch"},
        group_key=f"run:{run_id}:received",
    )

    return IngestResponse(
        run_id=run_id,
        task_id=task.id,
        total_results=len(payload.results),
    )


def _one_scalar_or_none(result):
    """Return one id; ambiguous legacy duplicates must not pick arbitrarily."""
    from sqlalchemy.exc import MultipleResultsFound
    try:
        return result.scalar_one_or_none()
    except MultipleResultsFound:
        return None


async def _resolve_run_id(
    db,
    project_id,
    build_number: str | None,
    *,
    ci_provider: str | None = None,
    ci_repo: str | None = None,
    ci_run_url: str | None = None,
    jenkins_job: str | None = None,
) -> str:
    """The run id this ingest will actually land on.

    Ingest is asynchronous — 202 plus a Celery task — so the router used to
    mint a fresh ``uuid4()``, hand it to the worker and return it. But the
    pipeline dedupes on ``(project_id, build_number)`` and **reuses** the
    existing run, discarding the minted id. On a duplicate build number the
    caller therefore received a 202 and an id that was never persisted:
    ``GET /runs/{that_id}`` answered 404 and the row did not exist at all.

    That is precisely the CI-retry case — re-running a failed job reuses its
    build number — so the callers most likely to hit it are the automated ones
    that POST results and then poll or link the run.

    Returning the existing id makes the response truthful. New deliveries
    derive a deterministic UUID5 from the source identity, so concurrent first
    deliveries submit the same primary key and the database identity index
    selects one canonical row.
    """
    if not build_number:
        return str(uuid.uuid4())
    from app.models.postgres import TestRun
    from app.services.ingestion_pipeline import build_ingestion_identity

    identity = build_ingestion_identity(
        project_id=project_id,
        build_number=build_number,
        ingestion_source="sdk",
        ci_provider=ci_provider,
        ci_repo=ci_repo,
        ci_run_url=ci_run_url,
        jenkins_job=jenkins_job,
    )
    existing = _one_scalar_or_none(
        await db.execute(
            select(TestRun.id).where(
                TestRun.project_id == project_id,
                TestRun.ingestion_identity == identity,
            )
        )
    )
    if existing:
        return str(existing)
    if not (ci_provider or ci_repo or ci_run_url or jenkins_job):
        # Legacy rows have no identity. Reuse one only when the label is
        # unambiguous; multiple historical jobs require a new canonical id.
        legacy = _one_scalar_or_none(
            await db.execute(
                select(TestRun.id).where(
                    TestRun.project_id == project_id,
                    TestRun.ingestion_identity.is_(None),
                    TestRun.build_number == build_number,
                    TestRun.ingestion_source != IngestionSource.UPLOAD.value,
                )
            )
        )
        if legacy:
            return str(legacy)
    # UUID5 makes concurrent first deliveries for one identity submit the
    # same primary key; the worker's unique index still returns the winner.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:{project_id}:{identity}"))


_SUPPORTED_FORMATS = {
    "auto", "junit", "testng", "allure", "cypress", "playwright", "pytest",
    "robot", "cucumber", "nunit", "trx", "xunit",
}


@router.post(
    "/file",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload test result file (JUnit/TestNG XML, Allure/Cypress/Playwright JSON)",
)
async def ingest_file(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    build_number: str = Form(..., min_length=1, max_length=BUILD_NUMBER_MAX_LENGTH),
    # Bounded to match the JSON `IngestPayload` schema and the underlying
    # TestRun columns (branch String(255), commit_hash String(64)) / release
    # name String(255). Without these caps the multipart path let an over-long
    # value past the router's validation, only for it to blow up (or silently
    # truncate) at insert time in the worker — off the request path, so the
    # caller saw a 202 and never learned the run failed. The JSON path already
    # returns a clean 422 here; this makes the file path do the same.
    branch: str = Form(None, max_length=255),
    commit_hash: str = Form(None, max_length=64),
    release_name: str = Form(None, max_length=255),
    format: str = Form("auto"),
    run_ai: bool = Form(True),
    # CI context (US-4.3) — optional; the CLI auto-detects these from standard
    # CI env vars and forwards them here.
    ci_provider: str = Form(None, max_length=30),
    ci_repo: str = Form(None, max_length=300),
    pr_number: int = Form(None, ge=1),
    ci_actor: str = Form(None, max_length=120),
    ci_run_url: str = Form(None, max_length=1000),
    jenkins_job: str = Form(None, max_length=500),
    # Environment this run executed against (roadmap Phase 0). Optional —
    # omitting it records "not known" rather than a synthetic default.
    environment: str = Form(None, max_length=100),
    # When the run actually EXECUTED, ISO-8601. Optional; omitting it keeps the
    # pre-existing behaviour of stamping ingest time.
    #
    # This matters beyond accuracy: release attribution resolves which release
    # was active AS OF this moment, so for a batch upload the difference
    # between execution and ingest is the difference between the right release
    # and whichever one happens to be current. Bounded by
    # ``services/execution_time`` — a client value is not trusted, because a
    # bad one silently removes the run from every default time window rather
    # than raising.
    executed_at: datetime = Form(None),
    # Commit attribution (US-8.1, air-gapped path) — an optional JSON-encoded
    # list of ``{sha, author, message, files}`` the CLI can supply so suspect
    # ranking needs no outbound VCS call. Parsed + bounded below.
    commit_range: str = Form(None),
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
):
    """
    Upload a test result file for async parsing and ingestion.

    Supported formats: ``junit`` | ``testng`` | ``allure`` | ``cypress`` |
    ``playwright`` | ``pytest`` | ``robot`` | ``cucumber`` | ``nunit`` |
    ``trx`` | ``xunit``. Use
    ``format=auto`` (default) for content-based detection.
    The Cypress and Playwright parsers are gated behind the ``cypress_ingest``
    and ``playwright_ingest`` feature flags respectively — 503 is returned if
    a disabled format is requested.
    """
    if format not in _SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported format '{format}'. Expected one of: "
                + ", ".join(sorted(_SUPPORTED_FORMATS))
            ),
        )

    current_user, bound_project_id = auth
    target_project_id = _parse_project_uuid(project_id)

    # Project-scoped API key: enforce that ingestion targets the bound project
    if bound_project_id is not None and bound_project_id != target_project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is restricted to a different project",
        )

    # Tenant isolation: non-admin users can only ingest into projects they
    # are members of (raises 403 otherwise).
    await resolve_project_scope(db, current_user, str(target_project_id))

    from app.worker.tasks import ingest_uploaded_file

    # Stream-read the upload with a hard cap so an attacker cannot OOM the
    # server by POSTing a multi-gigabyte file.
    content = await _read_upload_bounded(file, MAX_FILE_SIZE)
    _require_nonempty_upload(content)

    # A ZIP is binary — detect it by magic bytes / extension BEFORE the utf-8
    # decode below (which would corrupt it), and route it to the archive parser
    # regardless of the requested format. The worker safe-extracts + dispatches.
    from app.services.safe_archive import looks_like_zip
    is_archive = looks_like_zip(content) or (file.filename or "").lower().endswith(".zip")

    # Feature-flag gate: cypress/playwright ingestion is admin-gated (flags seeded
    # by migration 0063; enabled by default since migration 0099 — an admin can
    # still disable either format from Settings > Feature Flags). For single
    # files we reject up-front; for a zip we can't know its contents here, so
    # resolve which gated formats are DISABLED and pass that set to the worker,
    # which skips matching tier-2 entries — otherwise zipping a Cypress/
    # Playwright report would bypass the gate.
    from app.services.feature_flags import is_enabled

    disabled_formats: list[str] = []
    if is_archive:
        detected_format = "archive"
        for gated in ("cypress", "playwright"):
            if not await is_enabled(
                f"{gated}_ingest", db=db, project_id=target_project_id, user=current_user,
            ):
                disabled_formats.append(gated)
    else:
        detected_format = format
        if format == "auto":
            detected_format = _detect_format(file.filename or "", content)

        if detected_format in ("cypress", "playwright"):
            flag_key = f"{detected_format}_ingest"
            if not await is_enabled(
                flag_key,
                db=db,
                project_id=target_project_id,
                user=current_user,
            ):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        f"{detected_format.title()} ingestion is disabled. "
                        f"Ask an admin to enable the '{flag_key}' feature flag."
                    ),
                )

    # Manual uploads are independent submissions. Mint one durable run ID per
    # request so a repeated build label cannot resume an unrelated CI/SDK run.
    # Celery retries keep this same ID and therefore resume only this job.
    run_id = str(uuid.uuid4())

    # Keep large uploads out of the Celery broker.  The worker receives only
    # this server-generated key and fetches the bytes from object storage.
    from app.db.storage import get_storage_provider
    queued_storage_key = (
        f"uploads/{target_project_id}/{run_id}/queued/{uuid.uuid4().hex}"
    )
    try:
        await get_storage_provider().put_object(
            queued_storage_key,
            content,
            content_type="application/zip" if is_archive else "application/octet-stream",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("file_ingest_storage_failed", run_id=run_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload storage is temporarily unavailable",
        ) from exc

    # US-8.1 — parse the optional supplied commit range (JSON string on the
    # multipart form). Malformed JSON is ignored rather than 400'd: attribution
    # is a best-effort enrichment, not a gate on ingesting the results.
    commit_range_arg = None
    if commit_range:
        import json
        try:
            parsed = json.loads(commit_range)
            if isinstance(parsed, list):
                commit_range_arg = parsed[:100]
            elif isinstance(parsed, dict):
                # Boundary-carrying shape ``{base, head, commits}`` — keep the
                # refs; without them the stored row can't say what the range
                # was measured from (and is useless as TIA training data).
                commits = parsed.get("commits")
                commit_range_arg = {
                    **parsed,
                    "commits": commits[:100] if isinstance(commits, list) else [],
                }
        except (ValueError, TypeError):
            logger.warning("file_ingest_commit_range_unparseable", run_id=run_id)

    # Allocate the Celery task ID and seed its status before publishing. A fast
    # worker can otherwise finish before this request writes "pending", which
    # would hide the terminal result from every subsequent poll.
    task_id = str(uuid.uuid4())
    from app.services import upload_status
    await upload_status.set_status(
        task_id, run_id=run_id, project_id=str(target_project_id),
        state=upload_status.STATE_PENDING,
    )

    task_kwargs = dict(
        run_id=run_id,
        file_storage_key=queued_storage_key,
        file_name=file.filename or "unknown",
        file_format=detected_format,
        # Canonical UUID string so worker writes match the seeded record
        # and the project-scoped-key check in the status poll.
        project_id=str(target_project_id),
        build_number=build_number,
        branch=branch,
        commit_hash=commit_hash,
        release_name=release_name,
        user_id=str(current_user.id),
        disabled_formats=disabled_formats,
        run_ai=run_ai,
        ci_provider=ci_provider,
        ci_repo=ci_repo,
        pr_number=pr_number,
        ci_actor=ci_actor,
        ci_run_url=ci_run_url,
        jenkins_job=jenkins_job,
        environment=environment,
        # ISO string, not a datetime: Celery serializes task kwargs as JSON.
        executed_at=executed_at.isoformat() if executed_at else None,
        commit_range=commit_range_arg,
    )
    try:
        ingest_uploaded_file.apply_async(kwargs=task_kwargs, task_id=task_id)
    except Exception as exc:  # noqa: BLE001 — normalize broker errors for clients
        # Publish failures can be ambiguous. Delete only a record that is still
        # pending; an already-started worker's parsing/terminal status survives.
        await upload_status.clear_pending_status(task_id)
        logger.error("file_ingest_dispatch_failed", run_id=run_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload could not be queued",
        ) from exc

    logger.info(
        "file_ingest_accepted",
        run_id=run_id,
        project_id=project_id,
        file_name=file.filename,
        format=detected_format,
        size_bytes=len(content),
        user=current_user.username,
    )

    # Epic ACT — see the note on the JSON path above.
    await record_activity(
        None,
        project_id=target_project_id,
        event_type="run.received",
        actor=ActorRef.from_user(current_user),
        entity_id=run_id,
        entity_label=f"Build {build_number}",
        context={"source_format": detected_format, "file_name": file.filename},
        group_key=f"run:{run_id}:received",
    )

    return IngestResponse(
        run_id=run_id,
        task_id=task_id,
        total_results=0,  # unknown until parsed
    )


@router.get(
    "/uploads/{task_id}",
    response_model=UploadStatusResponse,
    summary="Poll the status of an uploaded report's async processing",
)
async def get_upload_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    auth: "tuple[User, uuid.UUID | None]" = Depends(get_api_key_context),
):
    """Return the async parse/ingest status for an upload task.

    Project-scoped: 404 if unknown/expired, 403 if the caller can't access the
    run's project (so a leaked/guessed task_id can't reveal another tenant's
    run).
    """
    from app.services import upload_status

    record = await upload_status.get_status(task_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload task not found or expired",
        )

    current_user, bound_project_id = auth
    record_project_id = record.get("project_id")

    # Project-scoped API key: must match the record's project.
    if bound_project_id is not None and str(bound_project_id) != str(record_project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is restricted to a different project",
        )

    # Tenant isolation: caller must be a member of the run's project (403 else).
    if record_project_id:
        await resolve_project_scope(db, current_user, str(record_project_id))

    return UploadStatusResponse(
        task_id=record.get("task_id", task_id),
        run_id=record.get("run_id"),
        state=record.get("state", "pending"),
        progress=record.get("progress"),
        result=record.get("result"),
        error=record.get("error"),
    )


def _detect_format(filename: str, content: bytes) -> str:
    """Auto-detect test result file format from filename and first 4 KB of content.

    Detection order is deliberate — most-specific markers first so a file
    that looks like multiple formats (e.g. a Mochawesome payload that also
    contains a ``"status"`` key) lands on the right parser.
    """
    lower = filename.lower()
    text = content[:4096].decode("utf-8", errors="replace")

    # ── XML formats first — their markers are unambiguous. ─────────────────
    # TestNG has distinctive markers
    if "<testng-results" in text or "configurationmethod" in text.lower():
        return "testng"
    # Robot Framework output.xml — the root element is always <robot ...>.
    # Checked before the JUnit markers for most-specific-first ordering (a
    # Robot output file never contains <testsuite, but the discipline keeps
    # future formats honest).
    if "<robot" in text:
        return "robot"
    # NUnit3 result XML — the root element is always <test-run ...>. Checked
    # before the JUnit markers (an NUnit file never contains <testsuite, but
    # most-specific-first keeps the ordering honest).
    if "<test-run" in text:
        return "nunit"
    # Visual Studio TRX — root <TestRun ...> carrying the VisualStudio
    # TeamTest namespace. A TRX file contains NO <testsuite marker, so it
    # would otherwise fall through to the junit default and parse to zero
    # results — this check must run before the generic XML fallbacks.
    if "<TestRun" in text and "microsoft.com/schemas/VisualStudio/TeamTest" in text:
        return "trx"
    # xUnit.net v2 XML — root <assemblies>, or a bare <assembly> root that
    # carries the distinctive test-framework attribute.
    if "<assemblies" in text or ("<assembly" in text and "test-framework" in text):
        return "xunit"
    # Standard JUnit/Surefire XML
    if "<testsuite" in text or "<testsuites" in text:
        return "junit"

    # ── JSON formats — require the root to look like an object. ──────────
    stripped = text.lstrip()
    looks_like_json = stripped.startswith("{") or stripped.startswith("[")

    # Playwright's JSON reporter includes a top-level ``config`` object with
    # ``projects``. The combination is distinctive — Cypress Mochawesome
    # never has ``config.projects`` and Allure never has ``config``.
    if looks_like_json and (
        '"config"' in stripped[:2048]
        and '"projects"' in stripped[:2048]
        and '"suites"' in stripped[:2048]
    ):
        return "playwright"

    # Cypress Mochawesome ships a top-level ``stats`` object with
    # ``tests``/``passes``/``failures`` + a ``results`` array keyed by spec
    # ``file``. No other supported format has ``stats`` + ``passes`` at
    # the root, so this is an unambiguous marker.
    # pytest-json-report (`pytest --json-report`): top-level "exitcode" + "root"
    # — both unique to it among supported formats (playwright=config/projects,
    # cypress=stats/passes, allure=uuid/name, junit/testng=XML) and both emitted
    # at the very TOP of the doc. We deliberately do NOT require "summary": it is
    # emitted AFTER the (unbounded) "environment" block, which on package-heavy
    # CI images pushes it past the 4 KB sniff window → false-negative.
    if looks_like_json and '"exitcode"' in stripped[:4096] and '"root"' in stripped[:4096]:
        return "pytest"

    if looks_like_json and (
        '"stats"' in stripped[:2048]
        and '"passes"' in stripped[:2048]
        and '"results"' in stripped[:2048]
    ):
        return "cypress"

    # Cucumber JSON (`cucumber --format json`, also behave/SpecFlow): the root
    # is an ARRAY of Feature objects carrying ``elements`` + a Gherkin
    # ``keyword``. No other supported format is array-rooted with those keys.
    # MUST run before the .json→allure extension fallback below, which would
    # otherwise swallow every cucumber.json.
    if (
        looks_like_json
        and stripped.startswith("[")
        and '"elements"' in stripped[:4096]
        and '"keyword"' in stripped[:4096]
    ):
        return "cucumber"

    # Allure single-result JSON: ``uuid``/``name``/``status`` at root.
    if looks_like_json and '"uuid"' in stripped and '"name"' in stripped and '"status"' in stripped:
        return "allure"

    # Extension-based fallback — .json files that don't match any JSON
    # sniffer above are treated as Allure for backwards compatibility.
    if lower.endswith(".json"):
        return "allure"

    # Default to JUnit — most common format
    return "junit"
