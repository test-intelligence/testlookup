"""Performance Budgets & Metrics router — exposes budgets and index health (OPS-03)."""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
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


@router.get("/observed")
async def get_observed_performance(
    window: str = Query("1h", pattern="^(15m|1h|24h)$", description="15m, 1h or 24h"),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """W17b ADM-06: observed p50/p95/p99 and request count per budgeted route, beside its budget.

    ``source`` is ``prometheus`` when ``PROMETHEUS_URL`` answers, else ``in_app`` (measured by
    TestLookup on every API pod). A route with no requests in the window reads ``measured: false``.
    """
    from app.services.observed_performance import observed

    return await observed(window)


@router.get("/slow-queries")
async def get_slow_queries(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """W17b ADM-06: the slowest statement shapes (no bound values). ADMIN: SQL text reveals the schema."""
    from app.services.observed_performance import slow_queries

    return await slow_queries(db, limit=limit)


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
        "pg_process_role": settings.PG_PROCESS_ROLE,
        "pg_processes_per_pod": settings.PG_PROCESSES_PER_POD,
        "pg_fleet_max_connections": settings.PG_FLEET_MAX_CONNECTIONS,
        "pg_fleet_operational_reserve": settings.PG_FLEET_OPERATIONAL_RESERVE,
        "pg_fleet_superuser_reserved_connections": settings.PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS,
        "pg_fleet_reserved_connections": settings.PG_FLEET_RESERVED_CONNECTIONS,
        "pg_fleet_migration_connections": settings.PG_FLEET_MIGRATION_CONNECTIONS,
        "pg_fleet_required_connections": settings.PG_FLEET_REQUIRED_CONNECTIONS,
        "celery_worker_concurrency": settings.CELERY_WORKER_CONCURRENCY,
    }
