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

    detected_format = format
    if format == "auto":
        detected_format = _detect_format(file.filename or "", content)

    # Feature-flag gate: refuse parser invocations for flags that aren't
    # enabled for this project. The flags are seeded by migration 0064 and
    # default to OFF, so existing deployments are unaffected until an ADMIN
    # toggles them from Settings > Feature Flags.
    if detected_format in ("cypress", "playwright"):
        from app.services.feature_flags import is_enabled
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
