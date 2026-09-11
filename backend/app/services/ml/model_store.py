"""Trained ML models, shared across pods through the object store (re-audit M14).

``ML_MODEL_DIR`` is a directory on the local filesystem. The nightly retrain
runs in one worker pod and wrote its ``.joblib`` there; every other pod --
the API, the AI worker that actually classifies, the other replicas -- read
its own empty directory and never saw it. A retrain did not propagate, and on
a restart the model was gone. Kubernetes gave the directory no volume at all.

A ReadWriteMany volume is not portable across the supported overlays (EBS on
EKS, a PD on GKE and local-path on K3s are all ReadWriteOnce), but the object
store is present in every deployment. So:

* the object store is the source of truth: a training run PUBLISHES the
  model and its metadata under ``ML_MODEL_STORE_PREFIX`` right after its
  atomic local write;
* ``ML_MODEL_DIR`` is a pod-local cache: :func:`sync_down` copies any version
  it does not have yet, atomically, and is called from the async paths that
  are about to use a model. It is throttled, so a hot path pays one listing
  a minute at most, and the loaders' existing 60 s version check then picks
  the new file up (hot swap, no restart).

Only names that look like a model version or its metadata are accepted from
the store: an object called ``../../x`` or ``evil.pkl`` is never written, let
alone loaded (a ``.joblib`` is a pickle). The bucket is private to the
deployment; anyone who can write to it can already replace every report.

Best-effort in both directions: a publish or sync failure is logged and the
local model keeps serving. ``ML_MODEL_SYNC_ENABLED`` switches it on (the
Kubernetes base config does; a single-host install needs nothing).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import structlog

from app.core.config import settings

logger = structlog.get_logger("services.ml.model_store")

#: What may come down from the store: versioned models and their metadata.
_ALLOWED_NAME = re.compile(
    r"^(?:(?:classifier|flaky_confidence)_v\d{8}_\d{6}\.joblib"
    r"|training_metadata\.json|flaky_confidence_metadata\.json)$"
)
_METADATA_NAMES = ("training_metadata.json", "flaky_confidence_metadata.json")

SYNC_INTERVAL_SECONDS = 60.0
_last_sync: float = 0.0


def _prefix() -> str:
    prefix = (settings.ML_MODEL_STORE_PREFIX or "ml-models/").strip().lstrip("/")
    return prefix if prefix.endswith("/") else prefix + "/"


def _storage():
    from app.db.storage import get_storage_provider

    return get_storage_provider()


async def publish(*paths: Path) -> list[str]:
    """Upload freshly written model files; returns the keys written."""
    if not settings.ML_MODEL_SYNC_ENABLED:
        return []
    written: list[str] = []
    try:
        storage = _storage()
        # Model files first, metadata last: a reader that sees the metadata
        # can then always find the model it describes.
        ordered = sorted(paths, key=lambda p: p.name in _METADATA_NAMES)
        for path in ordered:
            if not _ALLOWED_NAME.match(path.name):
                raise ValueError(f"refusing to publish an unexpected model file name: {path.name}")
            key = _prefix() + path.name
            content_type = "application/json" if path.suffix == ".json" else "application/octet-stream"
            await storage.put_object(key, path.read_bytes(), content_type=content_type)
            written.append(key)
    except Exception as exc:  # noqa: BLE001 -- the local model still serves this pod
        logger.warning("ml_model_publish_failed", error=str(exc)[:300], written=written)
    else:
        logger.info("ml_model_published", keys=written)
    return written


def _write_atomically(target: Path, content: bytes) -> None:
    tmp = target.with_name(target.name + ".download")
    tmp.write_bytes(content)
    os.replace(tmp, target)


async def sync_down(*, force: bool = False) -> list[str]:
    """Copy model versions this pod does not have yet. Returns the names written.

    Throttled to one listing per :data:`SYNC_INTERVAL_SECONDS` unless
    ``force``. Never raises.
    """
    global _last_sync
    if not settings.ML_MODEL_SYNC_ENABLED:
        return []
    now = time.monotonic()
    if not force and _last_sync and (now - _last_sync) < SYNC_INTERVAL_SECONDS:
        return []
    _last_sync = now

    written: list[str] = []
    try:
        storage = _storage()
        prefix = _prefix()
        model_dir = Path(settings.ML_MODEL_DIR)
        model_dir.mkdir(parents=True, exist_ok=True)
        objects = await storage.list_objects(prefix)
        wanted: list[tuple[str, str, int | None]] = []
        for item in objects:
            key = str(item.get("key") or item.get("Key") or "")
            name = key[len(prefix):] if key.startswith(prefix) else ""
            if not _ALLOWED_NAME.match(name):
                if name:
                    logger.warning("ml_model_store_unexpected_object", key=key)
                continue
            size = item.get("size", item.get("Size"))
            wanted.append((key, name, int(size) if isinstance(size, int) else None))
        # Models before metadata, for the same reason as publish().
        wanted.sort(key=lambda entry: entry[1] in _METADATA_NAMES)
        for key, name, size in wanted:
            target = model_dir / name
            is_metadata = name in _METADATA_NAMES
            if target.exists() and not is_metadata and (size is None or target.stat().st_size == size):
                continue  # versioned files are immutable once written
            content = await storage.get_object_content(key)
            if is_metadata and target.exists() and target.read_bytes() == content:
                continue
            _write_atomically(target, content)
            written.append(name)
    except Exception as exc:  # noqa: BLE001 -- keep serving what this pod has
        logger.warning("ml_model_sync_failed", error=str(exc)[:300])
        return written
    if written:
        logger.info("ml_model_synced", files=written)
    return written


def reset_sync_clock() -> None:
    """Tests: forget the throttle."""
    global _last_sync
    _last_sync = 0.0
