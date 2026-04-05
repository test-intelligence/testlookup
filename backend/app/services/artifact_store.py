"""
Intermediate artifact persistence for pipeline stages.

Stores large intermediate data (agent scratchpad, analysis payloads,
evidence bundles) in object storage (MinIO/S3/local) and returns URI
references. Pipeline stages pass only these lightweight references
instead of full payloads, keeping memory footprints low.

Usage:
    uri = await store_artifact(pipeline_run_id, "analysis", "tc-123", data)
    data = await load_artifact(uri)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

logger = logging.getLogger("services.artifact_store")

_ARTIFACT_BUCKET = "pipeline-artifacts"
_ARTIFACT_PREFIX = "pipeline"


def _artifact_key(pipeline_run_id: str, stage: str, artifact_id: str) -> str:
    """Build a deterministic storage key for a pipeline artifact."""
    date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    return f"{_ARTIFACT_PREFIX}/{date_prefix}/{pipeline_run_id}/{stage}/{artifact_id}.json"


async def store_artifact(
    pipeline_run_id: str,
    stage_name: str,
    artifact_id: str,
    data: Any,
) -> str:
    """
    Persist an intermediate artifact to object storage.

    Args:
        pipeline_run_id: The pipeline run this artifact belongs to.
        stage_name: The pipeline stage that produced this artifact.
        artifact_id: A unique identifier for this artifact within the stage.
        data: The data to persist (must be JSON-serializable).

    Returns:
        A URI reference string (storage_key) that can be used to retrieve the artifact.
    """
    from app.db.storage import get_storage_provider

    key = _artifact_key(pipeline_run_id, stage_name, artifact_id)
    content = json.dumps(data, default=str).encode("utf-8")

    try:
        storage = get_storage_provider()
        await storage.put_object(
            key=key,
            content=content,
            content_type="application/json",
            bucket=_ARTIFACT_BUCKET,
        )
        logger.debug(
            "Stored artifact: bucket=%s key=%s size=%d",
            _ARTIFACT_BUCKET, key, len(content),
        )
        return f"{_ARTIFACT_BUCKET}/{key}"
    except Exception as exc:
        logger.warning("Artifact store failed for %s/%s — falling back to inline: %s", stage_name, artifact_id, exc)
        # Return a sentinel so caller knows storage failed
        return ""


async def load_artifact(uri: str) -> Optional[dict]:
    """
    Load a previously stored artifact by its URI reference.

    Args:
        uri: The URI returned by store_artifact (format: "bucket/key").

    Returns:
        The deserialized data dict, or None if the artifact cannot be loaded.
    """
    if not uri:
        return None

    from app.db.storage import get_storage_provider

    try:
        # Parse bucket and key from URI
        parts = uri.split("/", 1)
        if len(parts) != 2:
            logger.warning("Invalid artifact URI: %s", uri)
            return None

        bucket, key = parts[0], parts[1]
        storage = get_storage_provider()
        content = await storage.get_object_content(key=key, bucket=bucket)
        return cast(dict[Any, Any], json.loads(content))
    except Exception as exc:
        logger.warning("Artifact load failed for %s: %s", uri, exc)
        return None


async def store_evidence_bundle(
    pipeline_run_id: str,
    bundle_bytes: bytes,
) -> str:
    """
    Store an evidence bundle ZIP in object storage.

    Returns:
        A URI reference string for the stored bundle.
    """
    from app.db.storage import get_storage_provider

    date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    key = f"{_ARTIFACT_PREFIX}/{date_prefix}/{pipeline_run_id}/evidence-bundle.zip"

    try:
        storage = get_storage_provider()
        await storage.put_object(
            key=key,
            content=bundle_bytes,
            content_type="application/zip",
            bucket=_ARTIFACT_BUCKET,
        )
        logger.info(
            "Stored evidence bundle: bucket=%s key=%s size=%d",
            _ARTIFACT_BUCKET, key, len(bundle_bytes),
        )
        return f"{_ARTIFACT_BUCKET}/{key}"
    except Exception as exc:
        logger.warning("Evidence bundle store failed: %s", exc)
        return ""


async def get_artifact_presigned_url(uri: str, expiry: int = 3600) -> Optional[str]:
    """Generate a presigned URL for downloading an artifact."""
    if not uri:
        return None

    from app.db.storage import get_storage_provider

    try:
        parts = uri.split("/", 1)
        if len(parts) != 2:
            return None
        bucket, key = parts[0], parts[1]
        storage = get_storage_provider()
        return await storage.get_presigned_url(key=key, expiry=expiry, bucket=bucket)
    except Exception as exc:
        logger.warning("Presigned URL generation failed for %s: %s", uri, exc)
        return None
