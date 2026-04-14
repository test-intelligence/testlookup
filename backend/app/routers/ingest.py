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
from app.models.schemas import IngestPayload, IngestResponse

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


@router.post(
    "/file",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload test result file (JUnit/TestNG XML, Allure JSON)",
)
async def ingest_file(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    build_number: str = Form(...),
    branch: str = Form(None),
    commit_hash: str = Form(None),
    release_name: str = Form(None),
    format: str = Form("auto"),
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
):
    """
    Upload a test result file for async parsing and ingestion.

    Supported formats: JUnit XML, TestNG XML, Allure JSON.
    Set format=auto (default) for automatic detection based on content.
    """
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

    detected_format = format
    if format == "auto":
        detected_format = _detect_format(file.filename or "", content)

    run_id = str(uuid.uuid4())
    task = ingest_uploaded_file.delay(
        run_id=run_id,
        file_content=content.decode("utf-8", errors="replace"),
        file_name=file.filename or "unknown",
        file_format=detected_format,
        project_id=project_id,
        build_number=build_number,
        branch=branch,
        commit_hash=commit_hash,
        release_name=release_name,
        user_id=str(current_user.id),
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


def _detect_format(filename: str, content: bytes) -> str:
    """Auto-detect test result file format from filename and first 2 KB of content."""
    lower = filename.lower()

    # Extension-based hints
    if lower.endswith(".json"):
        return "allure"

    # Content-based detection
    text = content[:2048].decode("utf-8", errors="replace")

    # TestNG has distinctive markers
    if "<testng-results" in text or "configurationMethod" in text.lower():
        return "testng"

    # Standard JUnit/Surefire XML
    if "<testsuite" in text or "<testsuites" in text:
        return "junit"

    # Allure JSON markers
    if '"uuid"' in text and '"name"' in text and '"status"' in text:
        return "allure"

    # Default to JUnit — most common format
    return "junit"
