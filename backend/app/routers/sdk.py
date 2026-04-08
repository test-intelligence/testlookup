"""
SDK download endpoints — serves the client SDK files from ./client/ at
/api/v1/sdk/{lang}.  Registered as a PUBLIC router (no JWT required) so
developers can download SDKs without logging in first.
"""
import io
import os
import zipfile

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/sdk", tags=["SDK Downloads"])

# Path where the client/ directory is mounted inside the container.
# Override via SDK_PATH env var if needed.
SDK_BASE_PATH = os.environ.get("SDK_PATH", "/app/client_sdks")


def _zip_directory(dir_path: str, zip_root: str) -> io.BytesIO:
    """Recursively zip a directory into an in-memory buffer."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(dir_path):
            for fname in files:
                abs_path = os.path.join(root, fname)
                arc_name = os.path.join(
                    zip_root, os.path.relpath(abs_path, dir_path)
                )
                zf.write(abs_path, arc_name)
    buf.seek(0)
    return buf


_SDK_CONFIGS: dict[str, dict] = {
    "python": {
        "label": "Python",
        "type": "file",
        "path": "testlookup_reporter.py",
        "filename": "testlookup_reporter.py",
        # Use octet-stream so browsers don't rename .py → .txt on Windows
        "media_type": "application/octet-stream",
    },
    "go": {
        "label": "Go",
        "type": "dir",
        "path": "go",
        "filename": "testlookup-go-sdk.zip",
    },
    "java": {
        "label": "Java",
        "type": "dir",
        "path": "java",
        "filename": "testlookup-java-sdk.zip",
    },
    "js": {
        "label": "JavaScript / TypeScript",
        "type": "dir",
        "path": "js",
        "filename": "testlookup-js-sdk.zip",
    },
}


@router.get("", summary="List available client SDKs")
async def list_sdks() -> JSONResponse:
    """Return metadata for all available SDK downloads."""
    available = []
    for lang, cfg in _SDK_CONFIGS.items():
        full_path = os.path.join(SDK_BASE_PATH, cfg["path"])
        exists = os.path.exists(full_path)
        available.append(
            {
                "lang": lang,
                "label": cfg["label"],
                "filename": cfg["filename"],
                "download_url": f"/api/v1/sdk/{lang}",
                "available": exists,
            }
        )
    return JSONResponse(content={"sdks": available})


@router.get("/{lang}", summary="Download a client SDK")
async def download_sdk(lang: str):
    """
    Download the SDK for the specified language.

    - **python** — returns `testlookup_reporter.py` directly
    - **go / java / js** — returns a ZIP archive of the SDK directory
    """
    cfg = _SDK_CONFIGS.get(lang)
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown SDK language '{lang}'. Available: {list(_SDK_CONFIGS)}",
        )

    full_path = os.path.join(SDK_BASE_PATH, cfg["path"])
    if not os.path.exists(full_path):
        logger.warning("sdk_file_not_found", lang=lang, path=full_path)
        raise HTTPException(
            status_code=503,
            detail=(
                "SDK files are not mounted in this environment. "
                "Add '- ./client:/app/client_sdks:ro' to the backend volumes in docker-compose.yml."
            ),
        )

    _no_cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }

    if cfg["type"] == "file":
        return FileResponse(
            path=full_path,
            filename=cfg["filename"],
            media_type=cfg["media_type"],
            headers=_no_cache_headers,
        )

    # Directory → buffer as ZIP then return with Content-Length set
    buf = _zip_directory(full_path, zip_root=lang)
    data = buf.read()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{cfg["filename"]}"',
            "Content-Length": str(len(data)),
            **_no_cache_headers,
        },
    )
