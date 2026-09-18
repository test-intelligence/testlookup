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


def _zip_files(base_path: str, filenames: list[str], zip_root: str) -> io.BytesIO:
    """Zip an explicit SDK manifest without pulling unrelated clients in."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in filenames:
            zf.write(
                os.path.join(base_path, filename),
                os.path.join(zip_root, filename),
            )
    buf.seek(0)
    return buf


_SDK_CONFIGS: dict[str, dict] = {
    "python": {
        "label": "Python",
        "type": "files",
        "path": "testlookup_reporter.py",
        "files": [
            "testlookup_reporter.py",
            "ci_context.py",
            "commit_range.py",
            "pyproject.toml",
            "README.md",
            "testlookup.yaml.example",
        ],
        "filename": "testlookup-python-sdk.zip",
    },
    "go": {
        "label": "Go",
        "type": "dir",
        "path": "go",
        "filename": "testlookup-go-sdk.zip",
    },
    "java": {
        "label": "Java",
        "type": "jar",
        "path": "java",
        "jar_path": "java/target/testlookup-reporter-1.0.0-all.jar",
        "filename": "testlookup-reporter-1.0.0-all.jar",
        "media_type": "application/java-archive",
        # Fallback: serve as ZIP of source if JAR not built
        "fallback_filename": "testlookup-java-sdk.zip",
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
        if cfg["type"] == "files":
            exists = all(
                os.path.isfile(os.path.join(SDK_BASE_PATH, filename))
                for filename in cfg["files"]
            )

        # For JAR-type SDKs, report whether the built JAR is available
        filename = cfg["filename"]
        if cfg["type"] == "jar":
            jar_full = os.path.join(SDK_BASE_PATH, cfg["jar_path"])
            jar_built = os.path.exists(jar_full)
            if not jar_built:
                filename = cfg["fallback_filename"]

        available.append(
            {
                "lang": lang,
                "label": cfg["label"],
                "filename": filename,
                "download_url": f"/api/v1/sdk/{lang}",
                "available": exists,
            }
        )
    return JSONResponse(content={"sdks": available})


@router.get("/{lang}", summary="Download a client SDK")
async def download_sdk(lang: str):
    """
    Download the SDK for the specified language.

    - **python** — returns a ZIP containing the reporter and its required sibling modules
    - **java** — returns `testlookup-reporter-1.0.0-all.jar` (fat JAR) if built,
      otherwise falls back to a ZIP archive of the source directory
    - **go / js** — returns a ZIP archive of the SDK directory
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

    if cfg["type"] == "files":
        missing = [
            filename
            for filename in cfg["files"]
            if not os.path.isfile(os.path.join(SDK_BASE_PATH, filename))
        ]
        if missing:
            logger.warning("sdk_file_not_found", lang=lang, files=missing)
            raise HTTPException(
                status_code=503,
                detail="Python SDK files are incomplete in this environment.",
            )
        buf = _zip_files(SDK_BASE_PATH, cfg["files"], zip_root=lang)
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

    if cfg["type"] == "jar":
        # Serve the pre-built fat JAR if available, otherwise fall back to
        # zipping the source directory so developers can build locally.
        jar_full = os.path.join(SDK_BASE_PATH, cfg["jar_path"])
        if os.path.exists(jar_full):
            return FileResponse(
                path=jar_full,
                filename=cfg["filename"],
                media_type=cfg["media_type"],
                headers=_no_cache_headers,
            )
        logger.info(
            "jar_not_built_serving_source_zip",
            lang=lang,
            hint="Run 'make build-java-sdk' to build the JAR",
        )
        buf = _zip_directory(full_path, zip_root=lang)
        data = buf.read()
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{cfg["fallback_filename"]}"',
                "Content-Length": str(len(data)),
                **_no_cache_headers,
            },
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
