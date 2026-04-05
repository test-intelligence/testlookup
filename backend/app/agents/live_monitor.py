"""
Live Test Execution Monitor Agent.

Thin facade over RedisLiveRunState — delegates all state management to Redis
so that state survives restarts and is shared across multiple FastAPI workers.
The in-memory dict has been removed; all reads/writes are Redis operations.
"""
import structlog
from typing import Optional

from app.streams.live_run_state import RedisLiveRunState

logger = structlog.get_logger("agents.live_monitor")


class LiveMonitorAgent:
    """
    Stateless facade for querying live run state.
    State is owned by RedisLiveRunState; this class is kept for backwards
    compatibility with routers that import LiveMonitorAgent.
    """

    @staticmethod
    async def get_active_runs() -> list[dict]:
        """Return state dicts for all currently active runs (reads from Redis)."""
        return await RedisLiveRunState.get_all_active()

    @staticmethod
    async def get_run_state(run_id: str) -> Optional[dict]:
        """Return the current state for a single run, or None if not found."""
        return await RedisLiveRunState.get(run_id)
