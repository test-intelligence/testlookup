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

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_api_key_context, get_db, resolve_project_scope
from app.models.postgres import User
from app.models.schemas import IngestPayload, IngestResponse, UploadStatusResponse

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

    run_id = str(uuid.uuid4())
    task = ingest_uploaded_results.delay(
        run_id=run_id,
        payload=payload.model_dump(),
        user_id=str(current_user.id),
    )

    logger.info(
        "batch_ingest_accepted",
        run_id=run_id,
        project_id=payload.project_id,
        result_count=len(payload.results),
        user=current_user.username,
    )

    return IngestResponse(
        run_id=run_id,
        task_id=task.id,
        total_results=len(payload.results),
    )


_SUPPORTED_FORMATS = {"auto", "junit", "testng", "allure", "cypress", "playwright"}


@router.post(
    "/file",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload test result file (JUnit/TestNG XML, Allure/Cypress/Playwright JSON)",
)
async def ingest_file(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    build_number: str = Form(...),
    branch: str = Form(None),
    commit_hash: str = Form(None),
    release_name: str = Form(None),
    format: str = Form("auto"),
    run_ai: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
):
    """
    Upload a test result file for async parsing and ingestion.

    Supported formats: ``junit`` | ``testng`` | ``allure`` | ``cypress`` |
    ``playwright``. Use ``format=auto`` (default) for content-based detection.
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

    # A ZIP is binary — detect it by magic bytes / extension BEFORE the utf-8
    # decode below (which would corrupt it), and route it to the archive parser
    # regardless of the requested format. The worker safe-extracts + dispatches.
    from app.services.safe_archive import looks_like_zip
    is_archive = looks_like_zip(content) or (file.filename or "").lower().endswith(".zip")

    # Feature-flag gate: cypress/playwright ingestion is admin-gated (flags seeded
    # by migration 0064, default OFF). For single files we reject up-front; for a
    # zip we can't know its contents here, so resolve which gated formats are
    # DISABLED and pass that set to the worker, which skips matching tier-2
    # entries — otherwise zipping a Cypress/Playwright report would bypass the gate.
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

    # Celery's JSON serializer can't carry raw bytes — base64-encode a zip;
    # decode text otherwise. The worker branches on file_format == "archive".
    if is_archive:
        import base64
        file_content_arg = base64.b64encode(content).decode("ascii")
    else:
        file_content_arg = content.decode("utf-8", errors="replace")

    run_id = str(uuid.uuid4())

    # Archive the raw upload (byte-exact) for audit / replay (MRU-9). Best-effort
    # — a storage hiccup must not fail the ingest; the worker links the key onto
    # the run's minio_prefix.
    raw_archive_key = None
    try:
        from app.db.storage import get_storage_provider
        safe_name = (file.filename or "report").replace("/", "_").replace("\\", "_")
        raw_archive_key = f"uploads/{target_project_id}/{run_id}/{safe_name}"
        await get_storage_provider().put_object(
            raw_archive_key, content,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as exc:  # noqa: BLE001 — archival is advisory
        logger.warning("upload_raw_archive_failed", run_id=run_id, error=str(exc))
        raw_archive_key = None

    task = ingest_uploaded_file.delay(
        run_id=run_id,
        file_content=file_content_arg,
        file_name=file.filename or "unknown",
        file_format=detected_format,
        # Canonical UUID string so the worker's status writes match the seeded
        # 'pending' record and the project-scoped-key check in the status poll.
        project_id=str(target_project_id),
        build_number=build_number,
        branch=branch,
        commit_hash=commit_hash,
        release_name=release_name,
        user_id=str(current_user.id),
        disabled_formats=disabled_formats,
        run_ai=run_ai,
        raw_archive_key=raw_archive_key,
    )

    # Seed a 'pending' status so the very first client poll (which may land
    # before the worker starts) gets a record instead of a 404.
    from app.services import upload_status
    await upload_status.set_status(
        task.id, run_id=run_id, project_id=str(target_project_id),
        state=upload_status.STATE_PENDING,
    )

    logger.info(
        "file_ingest_accepted",
        run_id=run_id,
        project_id=project_id,
        file_name=file.filename,
        format=detected_format,
        size_bytes=len(content),
        user=current_user.username,
    )

    return IngestResponse(
        run_id=run_id,
        task_id=task.id,
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
    if looks_like_json and (
        '"stats"' in stripped[:2048]
        and '"passes"' in stripped[:2048]
        and '"results"' in stripped[:2048]
    ):
        return "cypress"

    # Allure single-result JSON: ``uuid``/``name``/``status`` at root.
    if looks_like_json and '"uuid"' in stripped and '"name"' in stripped and '"status"' in stripped:
        return "allure"

    # Extension-based fallback — .json files that don't match any JSON
    # sniffer above are treated as Allure for backwards compatibility.
    if lower.endswith(".json"):
        return "allure"

    # Default to JUnit — most common format
    return "junit"
