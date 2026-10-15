"""Response models of the ML-training routes (AIC-05, W04 contract §3.3).

``GET /api/v1/settings/ai/ml/status`` and ``POST /api/v1/settings/ai/ml/retrain``
(``app/routers/ml_training.py``). Every enum-like field is a plain ``str``: a
strict ``Literal`` 422s the whole response the day a new status is added, and
the vocabularies below are constants in ``app/services/ml/training_job.py``.

``None`` means "not measured / not known", never a stand-in for 0: a status
page that cannot read the model metadata says so with ``null``, it does not
claim "accuracy 0".
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MLTrainingState(BaseModel):
    """Whether a training job holds the retrain lock right now."""

    state: str                            # "idle" | "queued" | "running"
    task_id: Optional[str] = None
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    triggered_by: Optional[str] = None    # "user:<username>" | "beat"


class MLLastRun(BaseModel):
    """The outcome of the most recent training attempt (``ml:retrain:last``)."""

    # trained | insufficient_data | insufficient_class_diversity |
    # below_threshold | disabled | skipped_running | error
    status: str
    message: Optional[str] = None
    finished_at: datetime
    triggered_by: str
    accuracy: Optional[float] = None      # 0..1
    version: Optional[str] = None
    sample_count: Optional[int] = None


class MLStatusRead(BaseModel):
    """What the Settings > AI ML card shows (QA lead and above)."""

    model_available: bool
    version: Optional[str] = None         # "YYYYMMDD_HHMMSS" (training_metadata.version)
    accuracy: Optional[float] = None      # 0..1 holdout accuracy
    trained_at: Optional[datetime] = None
    model_sample_count: Optional[int] = None   # samples the CURRENT model was trained on
    available_samples: int                # usable labels (feedback rows + pseudo-labels)
    min_training_samples: int             # settings.ML_MIN_TRAINING_SAMPLES
    human_labels: int                     # human-provenance labels
    human_label_floor: int                # settings.ML_HUMAN_LABEL_FLOOR
    maturity: str                         # not_trained | bootstrap_llm_imitating | human_calibrated
    deps_installed: bool                  # scikit-learn and joblib importable
    retrain_enabled: bool                 # settings.ML_RETRAIN_ENABLED (the NIGHTLY run only)
    can_train: bool
    # insufficient_samples | deps_missing | status_unavailable | None.
    # A running job is NOT a reason: the button reads "Training…" instead.
    cannot_train_reason: Optional[str] = None
    cannot_train_message: Optional[str] = None  # e.g. "Need 200 labelled samples, have 142."
    training: MLTrainingState
    last_run: Optional[MLLastRun] = None


class MLRetrainAccepted(BaseModel):
    """``202`` of ``POST /settings/ai/ml/retrain``."""

    task_id: str
    state: str                            # "queued" | "running"
    already_running: bool
