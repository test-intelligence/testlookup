"""Optional controlled saturation on an explicitly configured disposable DB.

Only SELECT 1 is executed. The tests never use the application's DATABASE_URL;
TESTLOOKUP_POSTGRES_TEST_DSN must name the isolated integration database.
"""
from __future__ import annotations

import os

from prometheus_client import REGISTRY
import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.pool_metrics import InstrumentedAsyncAdaptedQueuePool

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["api", "worker"])
async def test_postgres_checkout_pressure_timeout_and_recovery(monkeypatch, role):
    dsn = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", role)
    db = create_async_engine(
        dsn, poolclass=InstrumentedAsyncAdaptedQueuePool,
        pool_size=1, max_overflow=1, pool_timeout=0.05,
    )

    def sample(name):
        return REGISTRY.get_sample_value(f"sqlalchemy_pool_{name}", {"role": role})

    try:
        before_timeouts = sample("timeouts_total")
        async with db.connect() as first, db.connect() as second:
            assert (await first.execute(text("SELECT 1"))).scalar_one() == 1
            assert (await second.execute(text("SELECT 1"))).scalar_one() == 1
            assert sample("checked_out") == db.sync_engine.pool.checkedout() == 2
            assert sample("capacity") == 2
            assert sample("overflow") == 1
            assert sample("utilization_ratio") == 1
            with pytest.raises(SQLAlchemyTimeoutError) as failure:
                await db.connect()
            assert failure.value.code == "3o7r"
            assert sample("timeouts_total") == before_timeouts + 1
        assert sample("checked_out") == 0
        assert sample("overflow") == 0
        assert sample("utilization_ratio") == 0
        async with db.connect() as recovered:
            assert (await recovered.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await db.dispose()
