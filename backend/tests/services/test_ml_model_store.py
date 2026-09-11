"""Re-audit M14: a retrain in one pod reaches every other pod.

``ML_MODEL_DIR`` was a directory on each pod's own filesystem with no shared
volume, so a model trained in one worker never reached the API or the AI
worker that classifies. The object store is now the source of truth.

Two "pods" here are two ML_MODEL_DIRs sharing one real LocalStorageProvider
(the file-backed implementation of the same StorageProvider interface MinIO
serves in Kubernetes).
"""
from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.storage import LocalStorageProvider
from app.services.ml import model_store

VERSION = "classifier_v20260911_010203.joblib"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path / "object-store"))
    provider = LocalStorageProvider()
    monkeypatch.setattr(model_store, "_storage", lambda: provider)
    monkeypatch.setattr(settings, "ML_MODEL_SYNC_ENABLED", True)
    model_store.reset_sync_clock()
    yield provider
    model_store.reset_sync_clock()


def _pod(tmp_path: Path, name: str, monkeypatch) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(directory))
    model_store.reset_sync_clock()
    return directory


@pytest.mark.asyncio
async def test_a_version_published_by_one_pod_reaches_another(store, tmp_path, monkeypatch):
    from app.services.ml.classifier import _find_latest_model

    pod_a = _pod(tmp_path, "pod-a", monkeypatch)
    (pod_a / VERSION).write_bytes(b"model-bytes-a")
    (pod_a / "training_metadata.json").write_text('{"version": "20260911_010203"}')
    keys = await model_store.publish(pod_a / VERSION, pod_a / "training_metadata.json")
    assert keys == [f"ml-models/{VERSION}", "ml-models/training_metadata.json"]

    pod_b = _pod(tmp_path, "pod-b", monkeypatch)
    assert _find_latest_model() is None  # before the sync, pod B has nothing
    written = await model_store.sync_down()
    assert sorted(written) == sorted([VERSION, "training_metadata.json"])
    assert (pod_b / VERSION).read_bytes() == b"model-bytes-a"
    assert _find_latest_model() == pod_b / VERSION
    assert not list(pod_b.glob("*.download"))  # atomic: no partial files left


@pytest.mark.asyncio
async def test_unexpected_objects_are_never_written(store, tmp_path, monkeypatch):
    for key in (
        "ml-models/evil.pkl",
        "ml-models/classifier_v1.joblib",
        "ml-models/sub/classifier_v20260911_010203.joblib",
        "ml-models/classifier_v20260911_010203.joblib.exe",
    ):
        await store.put_object(key, b"x", content_type="application/octet-stream")
    pod = _pod(tmp_path, "pod", monkeypatch)
    assert await model_store.sync_down() == []
    assert list(pod.iterdir()) == []


@pytest.mark.asyncio
async def test_publish_refuses_a_file_that_is_not_a_model_version(store, tmp_path, monkeypatch):
    pod = _pod(tmp_path, "pod", monkeypatch)
    (pod / "notes.txt").write_text("x")
    assert await model_store.publish(pod / "notes.txt") == []
    assert await store.list_objects("ml-models/") == []


@pytest.mark.asyncio
async def test_sync_is_throttled_and_force_bypasses_it(store, tmp_path, monkeypatch):
    _pod(tmp_path, "pod", monkeypatch)
    assert await model_store.sync_down() == []
    await store.put_object(f"ml-models/{VERSION}", b"m", content_type="application/octet-stream")
    assert await model_store.sync_down() == []  # within the interval
    assert await model_store.sync_down(force=True) == [VERSION]


@pytest.mark.asyncio
async def test_changed_metadata_is_refreshed_and_versions_are_not_refetched(store, tmp_path, monkeypatch):
    await store.put_object(f"ml-models/{VERSION}", b"m", content_type="application/octet-stream")
    await store.put_object("ml-models/training_metadata.json", b'{"v": 1}')
    pod = _pod(tmp_path, "pod", monkeypatch)
    await model_store.sync_down()
    fetched: list[str] = []
    original = store.get_object_content

    async def spy(key, bucket=None):
        fetched.append(key)
        return await original(key, bucket)

    monkeypatch.setattr(store, "get_object_content", spy)
    await store.put_object("ml-models/training_metadata.json", b'{"v": 2}')
    assert await model_store.sync_down(force=True) == ["training_metadata.json"]
    assert (pod / "training_metadata.json").read_bytes() == b'{"v": 2}'
    assert f"ml-models/{VERSION}" not in fetched


@pytest.mark.asyncio
async def test_disabled_is_a_no_op(store, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ML_MODEL_SYNC_ENABLED", False)
    pod = _pod(tmp_path, "pod", monkeypatch)
    (pod / VERSION).write_bytes(b"m")
    assert await model_store.publish(pod / VERSION) == []
    assert await store.list_objects("ml-models/") == []
    # A version is in the store; a disabled pod still does not pull it.
    other = "flaky_confidence_v20260911_010203.joblib"
    await store.put_object(f"ml-models/{other}", b"x", content_type="application/octet-stream")
    assert await model_store.sync_down(force=True) == []
    assert not (pod / other).exists()


@pytest.mark.asyncio
async def test_a_store_outage_never_raises(tmp_path, monkeypatch):
    class Down:
        async def list_objects(self, prefix, bucket=None):
            raise ConnectionError("minio down")

        async def put_object(self, *args, **kwargs):
            raise ConnectionError("minio down")

    monkeypatch.setattr(settings, "ML_MODEL_SYNC_ENABLED", True)
    monkeypatch.setattr(model_store, "_storage", lambda: Down())
    pod = _pod(tmp_path, "pod", monkeypatch)
    (pod / VERSION).write_bytes(b"m")
    assert await model_store.publish(pod / VERSION) == []
    assert await model_store.sync_down(force=True) == []


@pytest.mark.asyncio
async def test_the_flaky_trainer_publishes_a_model_another_pod_can_use(store, tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    from app.services.ml import flaky_confidence as flaky

    rng = random.Random(7)

    def sample(label: int) -> dict:
        base = 0.85 if label else 0.15
        return {name: base + rng.uniform(-0.05, 0.05) for name in flaky.FLAKY_FEATURE_NAMES}

    labels = [i % 2 for i in range(60)]
    samples = [sample(label) for label in labels]

    async def gathered():
        return samples, labels

    monkeypatch.setattr(flaky, "_gather_flaky_training_data", gathered)
    monkeypatch.setattr(settings, "ML_MIN_TRAINING_SAMPLES", 40)
    monkeypatch.setattr(settings, "ML_ACCURACY_THRESHOLD", 0.5)
    _pod(tmp_path, "trainer-pod", monkeypatch)
    result = await flaky.train_flaky_confidence_model()
    assert result["status"] == "trained", result

    names = sorted(Path(o["Key"]).name for o in await store.list_objects("ml-models/"))
    assert names[-1] == "flaky_confidence_v" + result["version"] + ".joblib"
    assert "flaky_confidence_metadata.json" in names

    serving = _pod(tmp_path, "serving-pod", monkeypatch)
    monkeypatch.setattr(flaky, "_model", None)
    monkeypatch.setattr(flaky, "_model_path", None)
    await model_store.sync_down()
    assert flaky._find_latest_model() == serving / f"flaky_confidence_v{result['version']}.joblib"
    confidence = flaky.FlakyConfidenceModel.predict(sample(1))
    assert confidence is not None and confidence > 0.5


@pytest.mark.asyncio
async def test_the_classifier_trainer_publishes_a_model_another_pod_can_use(store, tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    from app.services.ml import classifier, trainer
    from app.services.ml.feature_extractor import FEATURE_NAMES

    rng = random.Random(11)

    def sample(label: str) -> dict:
        base = 0.9 if label == "INFRASTRUCTURE" else 0.1
        return {name: base + rng.uniform(-0.05, 0.05) for name in FEATURE_NAMES}

    labels = ["INFRASTRUCTURE" if i % 2 else "PRODUCT_BUG" for i in range(60)]
    samples = [sample(label) for label in labels]

    async def gathered():
        return samples, labels, ["human_direct"] * len(labels)

    monkeypatch.setattr(trainer, "_gather_training_data", gathered)
    monkeypatch.setattr(settings, "ML_MIN_TRAINING_SAMPLES", 40)
    monkeypatch.setattr(settings, "ML_HUMAN_LABEL_FLOOR", 10)
    monkeypatch.setattr(settings, "ML_ACCURACY_THRESHOLD", 0.5)
    _pod(tmp_path, "trainer-pod", monkeypatch)
    result = await trainer.train_classifier()
    assert result["status"] == "trained", result

    serving = _pod(tmp_path, "serving-pod", monkeypatch)
    monkeypatch.setattr(classifier, "_model", None)
    monkeypatch.setattr(classifier, "_model_path", None)
    await model_store.sync_down()
    latest = classifier._find_latest_model()
    assert latest == serving / f"classifier_v{result['version']}.joblib"
    assert (serving / "training_metadata.json").exists()
    assert classifier.MLClassifier.classify(sample("INFRASTRUCTURE"))["failure_category"] == "INFRASTRUCTURE"


@pytest.mark.asyncio
async def test_pipeline_start_syncs_models_before_resolving_the_mode(monkeypatch):
    from app.services import analysis_router

    calls: list[str] = []

    async def fake_sync(*, force=False):
        calls.append("sync")
        return []

    async def no_probe():
        return None

    monkeypatch.setattr(model_store, "sync_down", fake_sync)
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", no_probe)
    await analysis_router.refresh_analysis_mode_from_cache()
    assert calls == ["sync"]


# ── R-B45-3: concurrent writers in one pod; IO off the event loop ────────────


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows refuses concurrent renames onto one file (and onto an open one); "
    "the pods run Linux, where rename is atomic. The interleave test below covers Windows.",
)
def test_concurrent_downloads_never_publish_a_partial_model(tmp_path):
    """Every worker in a pod syncs the same version at once on startup.

    With one fixed temp name, two writers wrote the same file and one
    ``os.replace`` published it while the other was still writing (or, on
    Windows, the replace failed outright). Each reader must only ever see
    one writer's complete bytes, and no temp file may be left behind.
    """
    import threading

    target = tmp_path / VERSION
    size = 2 * 1024 * 1024
    payloads = [bytes([i]) * size for i in range(8)]
    errors: list[BaseException] = []
    seen_bad: list[int] = []
    stop = threading.Event()

    def writer(payload: bytes) -> None:
        try:
            for _ in range(6):
                model_store._write_atomically(target, payload)
        except BaseException as exc:  # noqa: BLE001 -- surfaced by the assert
            errors.append(exc)

    def reader() -> None:
        while not stop.is_set():
            try:
                data = target.read_bytes()
            except (FileNotFoundError, PermissionError):
                continue
            if data and (len(data) != size or data.count(data[:1]) != size):
                seen_bad.append(len(data))

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    watcher = threading.Thread(target=reader)
    watcher.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    watcher.join()
    assert errors == []
    assert seen_bad == []
    assert target.read_bytes() in payloads
    assert sorted(p.name for p in tmp_path.iterdir()) == [VERSION]


def test_a_writer_that_finishes_first_does_not_break_one_still_writing(tmp_path, monkeypatch):
    """Deterministic interleave: B writes and publishes while A is between
    its write and its replace. A's own bytes must still publish, whole."""
    target = tmp_path / VERSION
    real_replace = os.replace
    nested: list[bool] = []

    def replace_after_another_writer(src, dst):
        if not nested:
            nested.append(True)
            model_store._write_atomically(target, b"B" * 1000)
            assert target.read_bytes() == b"B" * 1000
        real_replace(src, dst)

    monkeypatch.setattr(model_store.os, "replace", replace_after_another_writer)
    model_store._write_atomically(target, b"A" * 1000)
    assert nested == [True]
    assert target.read_bytes() == b"A" * 1000
    assert sorted(p.name for p in tmp_path.iterdir()) == [VERSION]


def test_a_failed_write_leaves_no_temp_file_and_keeps_the_old_model(tmp_path, monkeypatch):
    target = tmp_path / VERSION
    target.write_bytes(b"old")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(model_store.os, "replace", broken_replace)
    with pytest.raises(OSError):
        model_store._write_atomically(target, b"new")
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == [VERSION]


@pytest.mark.asyncio
async def test_sync_does_its_file_io_off_the_event_loop(store, tmp_path, monkeypatch):
    import threading

    await store.put_object(f"ml-models/{VERSION}", b"m", content_type="application/octet-stream")
    _pod(tmp_path, "pod", monkeypatch)
    loop_thread = threading.get_ident()
    threads: list[int] = []
    original = model_store._write_atomically

    def spy(target, content):
        threads.append(threading.get_ident())
        original(target, content)

    monkeypatch.setattr(model_store, "_write_atomically", spy)
    assert await model_store.sync_down(force=True) == [VERSION]
    assert threads and loop_thread not in threads


# ── QA-B45-A6: exact names only; one bad object does not stop the sync ──────


@pytest.mark.asyncio
async def test_a_name_with_a_trailing_newline_is_refused(store, tmp_path, monkeypatch):
    pod = _pod(tmp_path, "pod", monkeypatch)

    class Listing:
        async def list_objects(self, prefix, bucket=None):
            return [
                {"Key": "ml-models/classifier_v20200101_000000.joblib\n", "Size": 1},
                {"Key": f"ml-models/{VERSION}", "Size": 1},
            ]

        async def get_object_content(self, key, bucket=None):
            fetched.append(key)
            return b"m"

    fetched: list[str] = []
    monkeypatch.setattr(model_store, "_storage", lambda: Listing())
    assert await model_store.sync_down(force=True) == [VERSION]
    assert fetched == [f"ml-models/{VERSION}"]
    assert sorted(p.name for p in pod.iterdir()) == [VERSION]
    assert model_store._is_allowed_name(VERSION + "\n") is False


@pytest.mark.asyncio
async def test_one_object_that_fails_does_not_cost_the_others(store, tmp_path, monkeypatch):
    other = "flaky_confidence_v20260911_010203.joblib"
    await store.put_object(f"ml-models/{VERSION}", b"m", content_type="application/octet-stream")
    await store.put_object(f"ml-models/{other}", b"f", content_type="application/octet-stream")
    pod = _pod(tmp_path, "pod", monkeypatch)
    original = store.get_object_content

    async def flaky_get(key, bucket=None):
        if key.endswith(VERSION):
            raise ConnectionError("reset")
        return await original(key, bucket)

    monkeypatch.setattr(store, "get_object_content", flaky_get)
    assert await model_store.sync_down(force=True) == [other]
    assert (pod / other).read_bytes() == b"f"


# ── R-B45-R2-7: the published mode; orphaned temp files are swept ────────────


def test_a_published_model_gets_the_intended_mode_not_mkstemps_0600(tmp_path, monkeypatch):
    modes: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def spy(path, mode):
        modes.append((Path(path).name, mode))
        real_chmod(path, mode)

    monkeypatch.setattr(model_store.os, "chmod", spy)
    target = tmp_path / VERSION
    model_store._write_atomically(target, b"m")
    # the TEMP file is chmodded, before the rename publishes it
    assert [m for _, m in modes] == [0o644]
    assert modes[0][0].endswith(".download")
    if os.name != "nt":  # Windows has only a read-only bit
        assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.asyncio
async def test_sync_sweeps_stale_temp_files_but_not_a_write_in_progress(store, tmp_path, monkeypatch):
    import time

    pod = _pod(tmp_path, "pod", monkeypatch)
    (pod / VERSION).write_bytes(b"kept")
    stale = pod / f"{VERSION}.abc123.download"
    fresh = pod / f"{VERSION}.def456.download"
    stale.write_bytes(b"half")
    fresh.write_bytes(b"half")
    old = time.time() - model_store.STALE_DOWNLOAD_SECONDS - 60
    os.utime(stale, (old, old))

    await model_store.sync_down(force=True)
    assert not stale.exists()
    assert fresh.exists()  # another process may still be writing it
    assert (pod / VERSION).read_bytes() == b"kept"


def test_the_sweep_only_touches_temp_files(tmp_path):
    import time

    old = time.time() - model_store.STALE_DOWNLOAD_SECONDS - 60
    names = [VERSION, "training_metadata.json", "notes.txt", f"{VERSION}.x.download"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (old, old))
    assert model_store._sweep_stale_downloads(tmp_path) == [f"{VERSION}.x.download"]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names[:3])
