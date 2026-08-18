"""Performance Budgets & Metrics router — exposes budgets and index health (OPS-03)."""
import logging

from fastapi import APIRouter, Depends

from app.core.deps import require_role
from app.models.postgres import User, UserRole

logger = logging.getLogger("routers.performance")

router = APIRouter(prefix="/api/v1/performance", tags=["Performance"])


@router.get("/budgets")
async def get_performance_budgets(
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Return all codified performance budgets and scale scenarios."""
    from app.services.performance_budgets import get_all_budgets

    return get_all_budgets()


@router.get("/search-config")
async def get_search_config(
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Return current search and indexing configuration."""
    from app.core.config import settings
    from app.db.postgres import get_effective_pool_config

    pool = get_effective_pool_config()

    return {
        "index_batch_size": settings.SEARCH_INDEX_BATCH_SIZE,
        "incremental_limit": settings.SEARCH_INDEX_INCREMENTAL_LIMIT,
        "query_timeout_ms": settings.SEARCH_QUERY_TIMEOUT_MS,
        "max_results": settings.SEARCH_MAX_RESULTS,
        "pg_pool_size": pool["pool_size"],
        "pg_max_overflow": pool["max_overflow"],
        "pg_pool_recycle": pool["pool_recycle"],
        "celery_worker_concurrency": settings.CELERY_WORKER_CONCURRENCY,
    }
