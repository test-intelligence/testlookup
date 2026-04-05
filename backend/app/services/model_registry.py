"""
Redis-backed model registry for hot-swappable fine-tuned models.

Keeps track of which model version is active per training track so that
llm_factory.py can pick up a promoted fine-tuned model without a restart.

Redis keys:
  testlookup:model:active:{track}     → model_name string
  testlookup:model:metrics:{track}    → JSON hash: {accuracy, examples, promoted_at}

Tracks:
  classifier  — small fast-path model (single LLM call, no tool use)
  reasoning   — full ReAct agent model (tool-calling fine-tune)
  embedding   — domain embedding model (contrastive fine-tune)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.redis_client import get_redis

logger = logging.getLogger("services.model_registry")

_PREFIX = "testlookup:model"
_TTL = 86_400 * 30  # 30-day TTL — refreshed on every write


class ModelRegistry:
    """Redis-backed registry for active model versions per track."""

    TRACKS = ("classifier", "reasoning", "embedding")

    @classmethod
    async def get_active_model(cls, track: str) -> Optional[str]:
        """Return the active fine-tuned model name for `track`, or None if none promoted."""
        redis = get_redis()
        value = await redis.get(f"{_PREFIX}:active:{track}")
        return value.decode() if value else None

    @classmethod
    async def promote(cls, track: str, model_name: str, metrics: dict) -> None:
        """
        Promote a candidate model to active for `track`.
        Writes model name to Redis and persists metrics for observability.
        """
        redis = get_redis()
        await redis.set(f"{_PREFIX}:active:{track}", model_name, ex=_TTL)
        await redis.set(
            f"{_PREFIX}:metrics:{track}",
            json.dumps({**metrics, "promoted_at": datetime.now(timezone.utc).isoformat()}),
            ex=_TTL,
        )
        logger.info("Promoted model track=%s model=%s metrics=%s", track, model_name, metrics)

    @classmethod
    async def retire(cls, track: str) -> None:
        """Retire the active model for `track` — falls back to settings.LLM_MODEL."""
        redis = get_redis()
        await redis.delete(f"{_PREFIX}:active:{track}")
        logger.info("Retired active model for track=%s", track)

    @classmethod
    async def get_all_status(cls) -> dict:
        """Return active model + metrics for all tracks (for health/debug endpoint)."""
        redis = get_redis()
        result = {}
        for track in cls.TRACKS:
            model = await redis.get(f"{_PREFIX}:active:{track}")
            metrics_raw = await redis.get(f"{_PREFIX}:metrics:{track}")
            result[track] = {
                "active_model": model.decode() if model else None,
                "metrics": json.loads(metrics_raw) if metrics_raw else None,
            }
        return result
