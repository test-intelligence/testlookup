"""Re-audit M14: a retrain in one pod reaches every other pod.

``ML_MODEL_DIR`` was a directory on each pod's own filesystem with no shared
volume, so a model trained in one worker never reached the API or the AI
worker that classifies. The object store is now the source of truth.

Two "pods" here are two ML_MODEL_DIRs sharing one real LocalStorageProvider
(the file-backed implementation of the same StorageProvider interface MinIO
serves in Kubernetes).
"""
from __future__ import annotations

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
