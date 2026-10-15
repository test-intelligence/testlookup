"""One ML-classifier training job, and the status the Settings > AI card reads (AIC-05).

The trainer (``services/ml/trainer.train_classifier``: composition policy, CV,
holdout, ``ML_ACCURACY_THRESHOLD``, ``model_store.publish``) existed and had no
caller: its docstring named a nightly beat and a retrain route that did not
exist, and the image did not ship scikit-learn. This module is the missing
wiring (W04 contract §2.9):

* :func:`run_training_job` is what the Celery task ``train_ml_classifier``
  runs, nightly at 03:10 UTC (``triggered_by="beat"``) or on "Train now"
  (``triggered_by="user:<username>"``). It holds the retrain lock while it
  trains, records the outcome in ``ml:retrain:last`` and hands the task the
  audit row to write. It never raises and never commits: the task owns the
  transaction (transaction-boundary ratchet).
* :func:`build_ml_status` is the read behind ``GET /settings/ai/ml/status``.
  It uses ONLY the session it is given: a second ``AsyncSessionLocal()``
  inside a guarded handler stalls on the 2+1 pool, and the card polls every
  3 s while a job runs. That is why the two counts below are not the
  trainer's ``get_training_sample_count`` / ``get_human_label_count`` (each
  opens its own session); the SQL is the same text, and
  ``tests/integration/test_ml_training_postgres.py`` proves the numbers agree.

Redis keys (contract §1.2):

``ml:retrain:lock``
    JSON ``{"task_id", "queued_at", "triggered_by"[, "started_at"]}``, 3600 s
    (above the task's 1800 s hard limit, so a dead worker frees it within an
    hour). Taken with ``SET NX`` by the retrain route (or by a nightly run),
    marked ``started_at`` by the job (``SET XX KEEPTTL``), released by the job
    with a compare-and-delete on ``task_id``: a job never frees a lock it does
    not hold.
``ml:retrain:last``
    JSON :class:`~app.models.ml_training_schemas.MLLastRun`, 30 days.

``ML_RETRAIN_ENABLED`` gates the NIGHTLY run only; "Train now" ignores it.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger("services.ml.training_job")

LOCK_KEY = "ml:retrain:lock"
LAST_RUN_KEY = "ml:retrain:last"
LOCK_TTL_SECONDS = 3600
LAST_RUN_TTL_SECONDS = 30 * 24 * 3600

TRIGGERED_BY_BEAT = "beat"

# MLTrainingState.state
STATE_IDLE = "idle"
STATE_QUEUED = "queued"
STATE_RUNNING = "running"

# MLLastRun.status (the trainer's own statuses plus the job's three)
STATUS_TRAINED = "trained"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
STATUS_INSUFFICIENT_CLASS_DIVERSITY = "insufficient_class_diversity"
STATUS_BELOW_THRESHOLD = "below_threshold"
STATUS_DISABLED = "disabled"
STATUS_SKIPPED_RUNNING = "skipped_running"
STATUS_ERROR = "error"

# MLStatusRead.cannot_train_reason
REASON_INSUFFICIENT_SAMPLES = "insufficient_samples"
REASON_DEPS_MISSING = "deps_missing"
REASON_STATUS_UNAVAILABLE = "status_unavailable"

AUDIT_SETTING_KEY = "ml_classifier"
AUDIT_ACTION_TRAINED = "trained"

DISABLED_MESSAGE = "Nightly training is switched off on this server (ML_RETRAIN_ENABLED)."
SKIPPED_RUNNING_MESSAGE = "Another training run was already in progress."
DEPS_MISSING_MESSAGE = "scikit-learn and joblib are not installed on this server."
STATUS_UNAVAILABLE_MESSAGE = "The training status could not be read; try again shortly."

# Compare-and-delete: free the lock only when it still names this task. The
# value is JSON, so the comparison decodes it (cjson ships with Redis).
_RELEASE_LOCK_LUA = """
local value = redis.call('GET', KEYS[1])
if not value then
  return 0
end
local ok, decoded = pcall(cjson.decode, value)
if ok and type(decoded) == 'table' and decoded['task_id'] == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# The same text as trainer.get_training_sample_count / get_human_label_count.
HUMAN_LABEL_COUNT_SQL = """
    SELECT COUNT(*) FROM ai_feedback
    WHERE rating = 'correct'
       OR (rating = 'incorrect' AND corrected_category IS NOT NULL)
"""
PSEUDO_LABEL_COUNT_SQL = """
    SELECT COUNT(*) FROM ai_analysis ai
    WHERE ai.confidence_score >= 80
      AND ai.failure_category IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM ai_feedback fb WHERE fb.analysis_id = ai.id
      )
"""


@dataclass(frozen=True)
class TrainingJobOutcome:
    """What the Celery task needs: the JSON-safe result, and the audit row to write."""

    result: dict[str, Any]
    audit: Optional[dict[str, Any]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _redis(redis: Any | None) -> Any:
    if redis is not None:
        return redis
    from app.db.redis_client import get_redis

    return get_redis()


def user_trigger(username: str | None) -> str:
    """``triggered_by`` of a "Train now" press."""
    return f"user:{username or 'unknown'}"


def deps_installed() -> bool:
    """scikit-learn and joblib importable (the trainer needs both)."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("sklearn", "joblib"))
    except (ImportError, ValueError):
        return False


def insufficient_samples_message(have: int, need: int) -> str:
    return f"Need {need} labelled samples, have {have}."


# ── Redis: the lock and the last run ──────────────────────────────────────────


def _decode(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def read_lock(redis: Any | None = None) -> Optional[dict[str, Any]]:
    """The lock's JSON, or ``None`` when free (or unreadable as a lock). Raises on a Redis error."""
    lock = _decode(await _redis(redis).get(LOCK_KEY))
    if lock is None or not lock.get("task_id"):
        return None
    return lock


async def acquire_lock(
    redis: Any | None,
    *,
    task_id: str,
    triggered_by: str,
    started: bool = False,
) -> bool:
    """``SET NX EX 3600``. True when this call took the lock."""
    now = _utcnow().isoformat()
    payload: dict[str, Any] = {"task_id": task_id, "queued_at": now, "triggered_by": triggered_by}
    if started:
        payload["started_at"] = now
    return bool(await _redis(redis).set(LOCK_KEY, json.dumps(payload), nx=True, ex=LOCK_TTL_SECONDS))


async def mark_lock_started(redis: Any | None, lock: dict[str, Any]) -> None:
    """Stamp ``started_at`` on a lock this job holds, keeping its TTL (``SET XX KEEPTTL``)."""
    updated = {**lock, "started_at": _utcnow().isoformat()}
    await _redis(redis).set(LOCK_KEY, json.dumps(updated), xx=True, keepttl=True)


async def release_lock(redis: Any | None, task_id: str) -> bool:
    """Compare-and-delete on ``task_id``. True when this call freed the lock."""
    return bool(await _redis(redis).eval(_RELEASE_LOCK_LUA, 1, LOCK_KEY, task_id))


async def read_last_run(redis: Any | None = None) -> Optional[dict[str, Any]]:
    """``ml:retrain:last`` as a dict, or ``None``. Raises on a Redis error."""
    return _decode(await _redis(redis).get(LAST_RUN_KEY))


async def write_last_run(redis: Any | None, last_run: dict[str, Any]) -> None:
    await _redis(redis).set(LAST_RUN_KEY, json.dumps(last_run, default=str), ex=LAST_RUN_TTL_SECONDS)


def training_state(lock: Optional[dict[str, Any]]) -> dict[str, Any]:
    """``MLTrainingState`` from the lock: free = idle, taken = queued, stamped = running."""
    if not lock:
        return {"state": STATE_IDLE, "task_id": None, "queued_at": None, "started_at": None, "triggered_by": None}
    return {
        "state": STATE_RUNNING if lock.get("started_at") else STATE_QUEUED,
        "task_id": str(lock.get("task_id")),
        "queued_at": lock.get("queued_at"),
        "started_at": lock.get("started_at"),
        "triggered_by": lock.get("triggered_by"),
    }


# ── Counts on the caller's session ────────────────────────────────────────────


async def count_training_samples(db: AsyncSession) -> tuple[int, int]:
    """``(available_samples, human_labels)`` on ``db`` (never a second session).

    ``available_samples`` = feedback rows carrying a usable label + high-
    confidence analyses with no feedback (pseudo-labels): the trainer's
    ``get_training_sample_count``. ``human_labels`` = the first term: its
    ``get_human_label_count``. Raises on a database error (the caller decides).
    """
    human = int((await db.execute(text(HUMAN_LABEL_COUNT_SQL))).scalar() or 0)
    pseudo = int((await db.execute(text(PSEUDO_LABEL_COUNT_SQL))).scalar() or 0)
    return human + pseudo, human


async def retrain_refusal(db: AsyncSession) -> Optional[dict[str, Any]]:
    """Why "Train now" cannot run, as the 409 ``detail``; ``None`` when it can.

    ``deps_missing`` wins over ``insufficient_samples`` (no number of labels
    trains without scikit-learn). A database error propagates: the route must
    not queue a job on a count it could not read.
    """
    need = int(settings.ML_MIN_TRAINING_SAMPLES)
    have, _human = await count_training_samples(db)
    if not deps_installed():
        return {"code": REASON_DEPS_MISSING, "message": DEPS_MISSING_MESSAGE, "have": have, "need": need}
    if have < need:
        return {
            "code": REASON_INSUFFICIENT_SAMPLES,
            "message": insufficient_samples_message(have, need),
            "have": have,
            "need": need,
        }
    return None


# ── The job ───────────────────────────────────────────────────────────────────


def _json_safe(value: Any) -> Any:
    """The trainer returns numpy floats; a Celery result must be plain JSON."""
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return {"status": str(value.get("status")) if isinstance(value, dict) else STATUS_ERROR}


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _message_for(result: dict[str, Any]) -> Optional[str]:
    status = result.get("status")
    if status == STATUS_TRAINED:
        accuracy = _finite_float(result.get("accuracy"))
        acc = f"{accuracy:.2f}" if accuracy is not None else "n/a"
        return f"Model {result.get('version')} trained on {result.get('sample_count')} samples (accuracy {acc})."
    if status == STATUS_BELOW_THRESHOLD:
        accuracy = _finite_float(result.get("accuracy"))
        threshold = _finite_float(result.get("threshold"))
        acc = f"{accuracy:.2f}" if accuracy is not None else "n/a"
        thr = f"{threshold:.2f}" if threshold is not None else "n/a"
        return f"Accuracy {acc} is below the deployment threshold {thr}; the previous model is kept."
    message = result.get("message")
    return str(message)[:300] if message else None


def _last_run(result: dict[str, Any], triggered_by: str) -> dict[str, Any]:
    trained = result.get("status") == STATUS_TRAINED
    return {
        "status": str(result.get("status") or STATUS_ERROR),
        "message": _message_for(result),
        "finished_at": _utcnow().isoformat(),
        "triggered_by": triggered_by,
        "accuracy": _finite_float(result.get("accuracy")),
        "version": str(result["version"]) if trained and result.get("version") else None,
        "sample_count": _int_or_none(result.get("sample_count")),
    }


def _audit_for(result: dict[str, Any], triggered_by: str) -> Optional[dict[str, Any]]:
    if result.get("status") != STATUS_TRAINED:
        return None
    accuracy = _finite_float(result.get("accuracy")) or 0.0
    return {
        "setting_key": AUDIT_SETTING_KEY,
        "action": AUDIT_ACTION_TRAINED,
        "changed_fields": [
            f"version:{result.get('version')}",
            f"accuracy:{accuracy:.4f}",
            f"samples:{result.get('sample_count')}",
            f"triggered_by:{triggered_by}",
        ],
    }


async def _record(redis: Any, result: dict[str, Any], triggered_by: str) -> None:
    try:
        await write_last_run(redis, _last_run(result, triggered_by))
    except Exception as exc:  # noqa: BLE001 -- the outcome is still returned and logged
        logger.warning("ml_training_last_run_write_failed: %s", str(exc)[:300])


async def _claim(redis: Any, task_id: str, triggered_by: str) -> Optional[str]:
    """Take or confirm the lock for this job. Returns the holder's task id when another job holds it.

    A manual run finds the lock the route took for it and stamps
    ``started_at``. A nightly run takes it (``NX``) already started. A lock that
    expired or was flushed is re-taken, so the card says "running". A Redis
    outage is not a reason to skip the night's training: the lock is advisory,
    so the job trains without it.
    """
    try:
        held = await read_lock(redis)
        if held is not None:
            if str(held.get("task_id")) != task_id:
                return str(held.get("task_id"))
            await mark_lock_started(redis, held)
            return None
        if await acquire_lock(redis, task_id=task_id, triggered_by=triggered_by, started=True):
            return None
        held = await read_lock(redis)
        if held is not None and str(held.get("task_id")) != task_id:
            return str(held.get("task_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_training_lock_unavailable: %s -- training without the lock", str(exc)[:300])
    return None


async def run_training_job(task_id: str, triggered_by: str, *, redis: Any | None = None) -> TrainingJobOutcome:
    """Train once, record the outcome, release the lock. Never raises, never commits."""
    try:
        client = _redis(redis)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_training_redis_unavailable: %s", str(exc)[:300])
        client = None

    if triggered_by == TRIGGERED_BY_BEAT and not settings.ML_RETRAIN_ENABLED:
        result = {"status": STATUS_DISABLED, "message": DISABLED_MESSAGE}
        if client is not None:
            await _record(client, result, triggered_by)
        return TrainingJobOutcome(result=result, audit=None)

    holder = await _claim(client, task_id, triggered_by) if client is not None else None
    if holder is not None:
        result = {"status": STATUS_SKIPPED_RUNNING, "message": SKIPPED_RUNNING_MESSAGE, "running_task_id": holder}
        await _record(client, result, triggered_by)
        return TrainingJobOutcome(result=result, audit=None)

    try:
        from app.services.ml import trainer

        raw = await trainer.train_classifier()
        result = _json_safe(raw) if isinstance(raw, dict) else {"status": STATUS_ERROR, "message": "trainer returned no result"}
    except Exception as exc:  # noqa: BLE001 -- the job never raises (the task has max_retries=0)
        logger.exception("ml_training_failed")
        result = {"status": STATUS_ERROR, "message": str(exc)[:300] or type(exc).__name__}
    finally:
        if client is not None:
            try:
                await release_lock(client, task_id)
            except Exception as exc:  # noqa: BLE001 -- the lock expires by itself (3600 s)
                logger.warning("ml_training_lock_release_failed: %s", str(exc)[:300])

    if client is not None:
        await _record(client, result, triggered_by)
    logger.info(
        "ml_training_finished status=%s version=%s accuracy=%s samples=%s triggered_by=%s",
        result.get("status"), result.get("version"), result.get("accuracy"), result.get("sample_count"), triggered_by,
    )
    return TrainingJobOutcome(result=result, audit=_audit_for(result, triggered_by))


# ── The status read ───────────────────────────────────────────────────────────


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _model_snapshot() -> dict[str, Any]:
    """What this pod has on disk (after a sync): availability + the metadata fields the card shows."""
    from app.services.ml.classifier import MLClassifier
    from app.services.ml.label_provenance import model_maturity_from_metadata

    available = MLClassifier.is_available()
    info = MLClassifier.get_model_info() if available else {}
    return {
        "model_available": available,
        "version": str(info["version"]) if info.get("version") else None,
        "accuracy": _finite_float(info.get("accuracy")),
        "trained_at": _parse_datetime(info.get("trained_at")),
        "model_sample_count": _int_or_none(info.get("sample_count")) if available else None,
        "maturity": model_maturity_from_metadata(info if available else None, settings.ML_HUMAN_LABEL_FLOOR),
    }


def _valid_last_run(raw: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not raw or not raw.get("status") or not raw.get("triggered_by"):
        return None
    finished = _parse_datetime(raw.get("finished_at"))
    if finished is None:
        return None
    return {
        "status": str(raw["status"]),
        "message": raw.get("message"),
        "finished_at": finished,
        "triggered_by": str(raw["triggered_by"]),
        "accuracy": _finite_float(raw.get("accuracy")),
        "version": raw.get("version"),
        "sample_count": _int_or_none(raw.get("sample_count")),
    }


async def build_ml_status(db: AsyncSession, *, redis: Any | None = None) -> dict[str, Any]:
    """The ``MLStatusRead`` payload. Reads only; degrades instead of raising.

    A Redis or database outage turns the unreadable parts into ``null``/``0``
    and ``cannot_train_reason="status_unavailable"``: the page renders, and
    "Train now" is not offered on numbers nobody could read.
    """
    from app.services.ml import model_store

    unavailable = False

    lock: Optional[dict[str, Any]] = None
    last_run: Optional[dict[str, Any]] = None
    try:
        client = _redis(redis)
        lock = await read_lock(client)
        last_run = _valid_last_run(await read_last_run(client))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_status_redis_unavailable: %s", str(exc)[:300])
        unavailable = True

    # A model another pod trained reaches this one through the store. The sync
    # is throttled to once a minute; a finished run whose version this pod does
    # not have yet forces it, so "Training…" turns into the model line at once.
    try:
        local_version = _model_snapshot()["version"]
        fresh_version = last_run.get("version") if last_run and last_run["status"] == STATUS_TRAINED else None
        await model_store.sync_down(force=bool(fresh_version) and fresh_version != local_version)
        model = _model_snapshot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_status_model_unreadable: %s", str(exc)[:300])
        unavailable = True
        model = {"model_available": False, "version": None, "accuracy": None, "trained_at": None,
                 "model_sample_count": None, "maturity": "not_trained"}

    available_samples = 0
    human_labels = 0
    try:
        available_samples, human_labels = await count_training_samples(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_status_counts_unreadable: %s", str(exc)[:300])
        unavailable = True
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass

    need = int(settings.ML_MIN_TRAINING_SAMPLES)
    installed = deps_installed()
    reason: Optional[str] = None
    message: Optional[str] = None
    if not installed:
        reason, message = REASON_DEPS_MISSING, DEPS_MISSING_MESSAGE
    elif unavailable:
        reason, message = REASON_STATUS_UNAVAILABLE, STATUS_UNAVAILABLE_MESSAGE
    elif available_samples < need:
        reason, message = REASON_INSUFFICIENT_SAMPLES, insufficient_samples_message(available_samples, need)

    return {
        **model,
        "available_samples": available_samples,
        "min_training_samples": need,
        "human_labels": human_labels,
        "human_label_floor": int(settings.ML_HUMAN_LABEL_FLOOR),
        "deps_installed": installed,
        "retrain_enabled": bool(settings.ML_RETRAIN_ENABLED),
        "can_train": reason is None,
        "cannot_train_reason": reason,
        "cannot_train_message": message,
        "training": training_state(lock),
        "last_run": last_run,
    }
