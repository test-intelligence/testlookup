"""Make ``AI_OFFLINE_MODE`` cover model **weights**, not just inference.

## The defect this closes

``AI_OFFLINE_MODE`` is documented as a hard egress ceiling. It was not one.

Two modules reasoned — in comments, as a stated guarantee — that because they
pass no ``embedding_function``, ChromaDB uses its *"bundled LOCAL"* ONNX
all-MiniLM model, never a cloud API, and therefore need no offline gate at all.
The first half is true: there is no cloud *inference* on that path. The second
half does not follow. The model is not bundled. On first use ChromaDB
**downloads 79.3 MB from** ``chroma-onnx-models.s3.amazonaws.com`` into
``Path.home()/.cache/chroma``.

Observed live on the reference deployment with ``AI_OFFLINE_MODE=true`` set
inside the container::

    HTTP Request: GET https://chroma-onnx-models.s3.amazonaws.com/
      all-MiniLM-L6-v2/onnx.tar.gz "HTTP/1.1 200 OK"

A successful egress, not a blocked attempt. In those pods ``HOME`` is ``/tmp``,
so the cache did not survive a restart and the fetch recurred every time — and
the download plus ONNX load, across several concurrent Celery forks, exceeded
the worker's 1 GiB limit and OOM-killed it in a restart loop (56 restarts in
18 hours).

So: an offline ceiling that covers inference but not **weight acquisition** is
not a ceiling, and a comment asserting otherwise is worse than no comment.

## Why this guards one chokepoint instead of nine call sites

Nine modules create ChromaDB collections, and every one of them would trigger
the same download. Gating each is nine chances to miss the tenth. ChromaDB
funnels all of them through a single method — ``_download_model_if_not_exists``
— so that is where the ceiling belongs.

Every one of those call sites already wraps ChromaDB in ``try/except`` and
degrades (keyword search, structural-only duplicate detection). Raising a clear,
typed error at the chokepoint therefore produces the correct fallback everywhere
at once, rather than a partial download and a truncated model.

## What this does NOT do

It does not disable semantic features. If the model is already present locally
— baked into the image, or side-loaded into ``CHROMA_ONNX_MODEL_DIR`` — nothing
here intervenes and everything works offline, which is the point. It only
refuses to go and **fetch** one while offline mode says the deployment is
sealed.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger("services.local_embedder_guard")

# The extracted directory ChromaDB looks for beside the archive it downloads.
# If this exists the model is usable and no fetch is attempted.
_EXTRACTED_FOLDER_NAME = "onnx"
_MODEL_FILENAME = "model.onnx"

_install_lock = threading.Lock()
_installed = False


class OfflineModelUnavailable(RuntimeError):
    """Offline mode forbids fetching embedding weights, and none are present.

    Deliberately a plain ``RuntimeError`` subclass: the ChromaDB call sites
    catch broad exceptions and fall back, so this degrades them without any
    needing to import this module.
    """


def _model_dir() -> Optional[Path]:
    """Where the local ONNX model should live.

    ``CHROMA_ONNX_MODEL_DIR`` wins when set — that is the side-load hatch for
    air-gapped installs. Otherwise fall back to whatever path ChromaDB itself
    resolved, so a baked-in image at the default location still works.
    """
    from app.core.config import settings

    configured = getattr(settings, "CHROMA_ONNX_MODEL_DIR", None)
    if configured:
        return Path(str(configured))
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )

        return Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH)
    except Exception:  # noqa: BLE001 — chromadb is an optional dependency
        return None


def model_present(model_dir: Optional[Path] = None) -> bool:
    """Is a usable local model already on disk?

    Checks for the extracted weights rather than the archive: a half-downloaded
    or untarred ``onnx.tar.gz`` is not a model, and treating it as one would
    trade a clear failure for a confusing one.
    """
    directory = model_dir if model_dir is not None else _model_dir()
    if directory is None:
        return False
    try:
        return (directory / _EXTRACTED_FOLDER_NAME / _MODEL_FILENAME).is_file()
    except OSError:
        return False


def acquisition_allowed() -> bool:
    """May this deployment fetch model weights over the network?

    This is the whole point of the module: ``AI_OFFLINE_MODE`` governs
    **acquisition**, not merely inference.
    """
    from app.core.config import settings

    return not bool(getattr(settings, "AI_OFFLINE_MODE", True))


def embedder_status() -> dict[str, Any]:
    """Plain description of why embeddings will or will not work here."""
    directory = _model_dir()
    present = model_present(directory)
    allowed = acquisition_allowed()
    return {
        "model_present": present,
        "acquisition_allowed": allowed,
        "model_dir": str(directory) if directory else None,
        "available": present or allowed,
        "reason": (
            "local model present"
            if present
            else (
                "no local model; offline mode forbids fetching one, so semantic "
                "features degrade to their non-embedding fallback"
                if not allowed
                else "no local model; will be fetched on first use"
            )
        ),
    }


def install_offline_embedder_guard() -> bool:
    """Wrap ChromaDB's model download so offline mode actually stops it.

    Idempotent and safe to call from several entry points — the API lifespan
    and each Celery worker process both call it, and a double-install would
    otherwise nest the wrapper.

    Returns True when the guard is in place (or already was). Returns False
    only when ChromaDB is not installed, which is a supported build.
    """
    global _installed

    with _install_lock:
        if _installed:
            return True
        try:
            from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
                ONNXMiniLM_L6_V2,
            )
        except Exception:  # noqa: BLE001 — chromadb is optional
            logger.info("embedder_guard_skipped", reason="chromadb not installed")
            return False

        original = ONNXMiniLM_L6_V2._download_model_if_not_exists

        def _guarded(self: Any) -> None:
            if model_present():
                # Present already — the original call is a no-op, but run it so
                # ChromaDB keeps ownership of its own integrity checks.
                return original(self)
            if not acquisition_allowed():
                logger.warning(
                    "embedder_weights_blocked_offline",
                    model_dir=str(_model_dir()),
                    remedy=(
                        "bake the model into the image or side-load it into "
                        "CHROMA_ONNX_MODEL_DIR"
                    ),
                )
                raise OfflineModelUnavailable(
                    "AI_OFFLINE_MODE is on and no local embedding model is "
                    "present, so the weights will not be downloaded. Side-load "
                    "the model into CHROMA_ONNX_MODEL_DIR to enable semantic "
                    "features offline."
                )
            return original(self)

        # Marked so the guard is detectable — the regression tests assert it is
        # installed rather than trusting that startup called it.
        _guarded._testlookup_offline_guard = True  # type: ignore[attr-defined]
        ONNXMiniLM_L6_V2._download_model_if_not_exists = _guarded  # type: ignore[method-assign]
        _installed = True
        logger.info("embedder_guard_installed", **embedder_status())
        return True


def guard_installed() -> bool:
    """True when the download chokepoint carries the guard."""
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(
        getattr(
            ONNXMiniLM_L6_V2._download_model_if_not_exists,
            "_testlookup_offline_guard",
            False,
        )
    )
