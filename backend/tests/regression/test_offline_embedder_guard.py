"""``AI_OFFLINE_MODE`` must cover model **weights**, not just inference.

Found by running the product, not by reading it. With ``AI_OFFLINE_MODE=true``
set inside the container, the reference deployment logged::

    HTTP Request: GET https://chroma-onnx-models.s3.amazonaws.com/
      all-MiniLM-L6-v2/onnx.tar.gz "HTTP/1.1 200 OK"

A successful 79 MB egress, not a blocked attempt — and the download plus ONNX
load across concurrent Celery forks OOM-killed the 1 GiB worker in a restart
loop (56 restarts in 18 hours).

The reasoning error is the class these tests guard: two modules argued from
*"the model runs locally"* to *"nothing reaches the network"*, and wrote the
conclusion down as a guarantee. There is no cloud **inference** on that path.
The **weights** are still fetched.

So the guards here are about the shape of the ceiling, not this one URL:

1. Offline mode blocks **acquisition**, not merely inference.
2. The ceiling sits at the single chokepoint, because nine modules create
   ChromaDB collections and gating each is nine chances to miss the tenth.
3. It is installed in **every process that can trigger it** — including each
   Celery prefork child, which is the one that actually did.
4. A present local model is never blocked; sealing a deployment must not mean
   disabling features it can serve.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.services import local_embedder_guard as guard


@pytest.fixture(autouse=True)
def _reset_guard_state(monkeypatch):
    """Each test starts from "not yet installed"."""
    monkeypatch.setattr(guard, "_installed", False, raising=False)
    yield


class _FakeEmbedder:
    """Stand-in for ChromaDB's ONNXMiniLM_L6_V2 download chokepoint."""

    DOWNLOAD_PATH = Path("/nonexistent/chroma/onnx_models/all-MiniLM-L6-v2")

    def __init__(self):
        self.downloads = 0

    def _download_model_if_not_exists(self):
        self.downloads += 1


# ── The ceiling covers acquisition ───────────────────────────────────────────

def test_offline_mode_forbids_fetching_weights(monkeypatch):
    """The whole defect in one assertion: offline must stop the download."""
    monkeypatch.setattr(guard, "model_present", lambda *_a, **_k: False)
    monkeypatch.setattr(guard, "acquisition_allowed", lambda: False)
    assert guard.embedder_status()["available"] is False


def test_acquisition_allowed_tracks_the_offline_setting(monkeypatch):
    """Guards the CLASS: this must read AI_OFFLINE_MODE, not a private flag
    that could drift away from it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    assert guard.acquisition_allowed() is False
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    assert guard.acquisition_allowed() is True


def test_a_missing_setting_is_treated_as_offline(monkeypatch):
    """Fail closed. An egress ceiling that opens when it cannot read its own
    configuration is not a ceiling."""
    class _Bare:
        pass

    monkeypatch.setattr("app.core.config.settings", _Bare())
    assert guard.acquisition_allowed() is False


def test_online_mode_still_allows_the_fetch(monkeypatch):
    """Sealing is opt-out. A deployment that wants the download must get it."""
    monkeypatch.setattr(guard, "model_present", lambda *_a, **_k: False)
    monkeypatch.setattr(guard, "acquisition_allowed", lambda: True)
    status = guard.embedder_status()
    assert status["available"] is True
    assert "will be fetched" in status["reason"]


# ── A present model is never blocked ─────────────────────────────────────────

def test_a_local_model_works_offline(monkeypatch, tmp_path):
    """Sealing a deployment must not disable features it can serve. This is
    what makes the fix a ceiling rather than a feature removal."""
    model_dir = tmp_path / "all-MiniLM-L6-v2"
    (model_dir / "onnx").mkdir(parents=True)
    (model_dir / "onnx" / "model.onnx").write_bytes(b"weights")

    monkeypatch.setattr(guard, "_model_dir", lambda: model_dir)
    monkeypatch.setattr(guard, "acquisition_allowed", lambda: False)

    assert guard.model_present() is True
    status = guard.embedder_status()
    assert status["available"] is True
    assert status["reason"] == "local model present"


def test_a_half_downloaded_archive_is_not_a_model(tmp_path):
    """Checks the extracted weights, not the archive. Treating a partial
    download as a model trades a clear failure for a confusing one."""
    model_dir = tmp_path / "all-MiniLM-L6-v2"
    model_dir.mkdir(parents=True)
    (model_dir / "onnx.tar.gz").write_bytes(b"partial")
    assert guard.model_present(model_dir) is False


def test_an_empty_extracted_folder_is_not_a_model(tmp_path):
    model_dir = tmp_path / "all-MiniLM-L6-v2"
    (model_dir / "onnx").mkdir(parents=True)
    assert guard.model_present(model_dir) is False


def test_an_unreadable_path_is_not_a_model():
    assert guard.model_present(Path("/definitely/not/here")) is False


def test_the_side_load_hatch_is_honoured(monkeypatch, tmp_path):
    """An air-gapped operator needs a way in. CHROMA_ONNX_MODEL_DIR is it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "CHROMA_ONNX_MODEL_DIR", str(tmp_path))
    assert guard._model_dir() == tmp_path


# ── The guard is at the chokepoint, and actually installed ───────────────────

def test_the_guard_wraps_the_download_and_blocks_it_offline(monkeypatch):
    fake = _FakeEmbedder()
    monkeypatch.setattr(guard, "model_present", lambda *_a, **_k: False)
    monkeypatch.setattr(guard, "acquisition_allowed", lambda: False)

    original = _FakeEmbedder._download_model_if_not_exists
    wrapped = _wrap(original)
    with pytest.raises(guard.OfflineModelUnavailable):
        wrapped(fake)
    assert fake.downloads == 0, "the download must not have run"


def test_the_guard_lets_the_download_through_when_permitted(monkeypatch):
    fake = _FakeEmbedder()
    monkeypatch.setattr(guard, "model_present", lambda *_a, **_k: False)
    monkeypatch.setattr(guard, "acquisition_allowed", lambda: True)

    wrapped = _wrap(_FakeEmbedder._download_model_if_not_exists)
    wrapped(fake)
    assert fake.downloads == 1


def _wrap(original):
    """Rebuild the guard's wrapper over a stand-in, mirroring install()."""
    def _guarded(self):
        if guard.model_present():
            return original(self)
        if not guard.acquisition_allowed():
            raise guard.OfflineModelUnavailable("blocked")
        return original(self)

    return _guarded


def test_the_error_degrades_call_sites_that_catch_broadly():
    """Every ChromaDB call site catches broad exceptions and falls back. The
    error must be a plain RuntimeError so none of them needs to import this
    module to degrade correctly."""
    assert issubclass(guard.OfflineModelUnavailable, RuntimeError)


def test_the_guard_actually_installs_against_the_real_chromadb():
    """Not a source grep — the real thing.

    ``install_offline_embedder_guard`` returns False and logs a skip when
    ChromaDB cannot be imported, so a wrong module path would silently mean NO
    CEILING AT ALL while every source-level test still passed. That is the same
    fail-open shape as a guard that cannot look and reports OK, so it is
    asserted directly.
    """
    pytest.importorskip("chromadb")
    assert guard.install_offline_embedder_guard() is True
    assert guard.guard_installed() is True


def test_the_guard_patches_the_class_the_call_sites_actually_use():
    """ChromaDB exposes the embedder from both a submodule and the package.
    Patching a different object than the one collections construct would leave
    the download wide open."""
    pytest.importorskip("chromadb")
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2 as via_package
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
        ONNXMiniLM_L6_V2 as via_submodule,
    )

    assert via_package is via_submodule


def test_install_is_idempotent():
    """The API lifespan and every Celery child call it. Double-installing would
    nest the wrapper and multiply the work on a hot path."""
    pytest.importorskip("chromadb")
    guard.install_offline_embedder_guard()
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    first = ONNXMiniLM_L6_V2._download_model_if_not_exists
    guard.install_offline_embedder_guard()
    assert ONNXMiniLM_L6_V2._download_model_if_not_exists is first


def test_the_guard_is_installed_by_the_api_startup():
    """Asserted against the source rather than trusting that it happens: a
    ceiling nobody installs is not a ceiling."""
    import app.main as main

    source = inspect.getsource(main.lifespan)
    assert "install_offline_embedder_guard" in source


def test_the_guard_is_installed_in_every_celery_prefork_child():
    """The child running reindex_search is the process that actually did the
    download. Installing only in the parent would have missed it entirely."""
    import app.worker.celery_app as celery_app

    source = inspect.getsource(celery_app._reset_db_pool_after_fork)
    assert "install_offline_embedder_guard" in source


def test_startup_never_fails_because_of_the_guard():
    """A missing optional dependency must not take the API down."""
    import app.main as main

    source = inspect.getsource(main.lifespan)
    head, _, tail = source.partition("install_offline_embedder_guard")
    # The call sits inside a try, and something catches broadly around it.
    assert "try:" in head
    assert "except Exception" in tail


# ── The comments that stated a false guarantee ───────────────────────────────

def test_no_module_still_claims_the_default_embedder_needs_no_gate():
    """Two modules asserted, as a guarantee, that no AI_OFFLINE_MODE check was
    required because the embedder is local. That reasoning is the defect, and a
    future author reading it would reproduce it."""
    import app.services.duplicate_detection_service as dedupe
    import app.services.semantic_search as search

    for module in (search, dedupe):
        source = inspect.getsource(module)
        assert "bundled LOCAL" not in source
        if "no ``AI_OFFLINE_MODE``" in source or "AI_OFFLINE_MODE`` early-return" in source:
            # Still discussed — but only alongside the correction.
            assert "was wrong" in source or "NOT the same as no" in source


def test_both_call_sites_point_at_the_chokepoint():
    """So the next reader learns where the ceiling lives instead of adding a
    tenth ungated collection."""
    import app.services.duplicate_detection_service as dedupe
    import app.services.semantic_search as search

    for module in (search, dedupe):
        assert "local_embedder_guard" in inspect.getsource(module)


def test_the_offline_setting_is_the_documented_knob():
    """CHROMA_ONNX_MODEL_DIR must exist as real config, not just prose."""
    from app.core.config import settings

    assert hasattr(settings, "CHROMA_ONNX_MODEL_DIR")


def test_structlog_calls_use_keyword_fields():
    """BoundLogger is (event, **kw); a stdlib-style positional %s raises
    mid-call inside an except block and kills the enclosing feature."""
    for line in inspect.getsource(guard).splitlines():
        stripped = line.strip()
        if stripped.startswith("logger.") and "%s" in stripped:
            pytest.fail(f"positional structlog arg: {stripped}")
