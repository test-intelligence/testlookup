"""Process-local SQLAlchemy pool measurements, safe for async and prefork use.

Each application process owns one lazy PostgreSQL engine. Absolute pool values
sum across live processes, while utilization uses ``livemax``: an exhausted
child must remain visible even when its siblings have idle pools. Only the
bounded process role is a label; neither connection strings nor process IDs are
exported. The existing Gunicorn/Celery child-exit hooks reap these live gauges.

Capacity includes configured overflow, rather than only persistent pool size.
Negative cold-pool overflow is reported as zero. For an unbounded pool (not a
supported application configuration), capacity and utilization are zero and
``capacity_limited`` is zero; zero does not claim that pool is exhausted.

Acquisition duration includes queue wait, connection creation and pre-ping. It
is not a query timer or a pure queue-wait measurement. A public ``connect`` call
is one attempt even when SQLAlchemy internally retries checkout. Only its
documented QueuePool timeout (error code 3o7r) increments the timeout counter.
"""
from __future__ import annotations

import logging
import os
from time import perf_counter
from typing import Any
from weakref import WeakValueDictionary

from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.config import settings
from app.core.metrics import (
    sqlalchemy_pool_acquire_seconds,
    sqlalchemy_pool_acquiring,
    sqlalchemy_pool_capacity,
    sqlalchemy_pool_capacity_limited,
    sqlalchemy_pool_checked_out,
    sqlalchemy_pool_max_overflow,
    sqlalchemy_pool_overflow,
    sqlalchemy_pool_size,
    sqlalchemy_pool_timeouts_total,
    sqlalchemy_pool_utilization_ratio,
)

logger = logging.getLogger(__name__)

# Internal object identity is never a metric label. Engine.dispose() recreates
# its pool; late checkins from the detached old pool must not overwrite the new
# pool's pressure. Weak references do not retain disposed engines/connections.
_current_pools: WeakValueDictionary[tuple[int, str], InstrumentedAsyncAdaptedQueuePool] = (
    WeakValueDictionary()
)


class InstrumentedAsyncAdaptedQueuePool(AsyncAdaptedQueuePool):
    """Keep SQLAlchemy's async queue semantics and add passive measurements.

    ``_do_return_conn`` is the one small internal hook: the public checkin event
    fires BEFORE the connection has returned to the queue, so sampling there
    leaves the final checked-out gauge one too high. No queue/lock behavior is
    replaced. The pinned SQLAlchemy contract is covered by real async tests.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._metric_pid = os.getpid()
        self._metric_role = settings.PG_PROCESS_ROLE
        self._metric_warning_logged = False
        _current_pools[(self._metric_pid, self._metric_role)] = self
        self._safe_metric_update(self._initialize_series)
        self._refresh_metrics()

    def _safe_metric_update(self, operation: Any) -> bool:
        try:
            operation()
            return True
        except Exception:
            # A failed exporter file/write must not fail a database operation.
            # No exception text, URL or statement is included in this warning.
            if not self._metric_warning_logged:
                self._metric_warning_logged = True
                logger.warning("db_pool_metric_update_failed")
            return False

    def _initialize_series(self) -> None:
        sqlalchemy_pool_acquiring.labels(role=self._metric_role)
        sqlalchemy_pool_timeouts_total.labels(role=self._metric_role)
        for outcome in ("success", "timeout", "error"):
            sqlalchemy_pool_acquire_seconds.labels(role=self._metric_role, outcome=outcome)

    def _refresh_metrics(self) -> None:
        def update() -> None:
            key = (self._metric_pid, self._metric_role)
            if os.getpid() != self._metric_pid or _current_pools.get(key) is not self:
                return
            size = self.size()
            limited = size > 0 and self._max_overflow >= 0
            capacity = size + self._max_overflow if limited else 0
            checked_out = max(0, self.checkedout())
            role = self._metric_role
            sqlalchemy_pool_checked_out.labels(role=role).set(checked_out)
            sqlalchemy_pool_size.labels(role=role).set(size)
            sqlalchemy_pool_max_overflow.labels(role=role).set(max(0, self._max_overflow))
            sqlalchemy_pool_capacity.labels(role=role).set(capacity)
            sqlalchemy_pool_capacity_limited.labels(role=role).set(int(limited))
            sqlalchemy_pool_overflow.labels(role=role).set(max(0, self.overflow()))
            sqlalchemy_pool_utilization_ratio.labels(role=role).set(
                checked_out / capacity if capacity else 0
            )

        self._safe_metric_update(update)

    def connect(self) -> Any:
        started = perf_counter()
        outcome = "error"
        counted = self._safe_metric_update(
            lambda: sqlalchemy_pool_acquiring.labels(role=self._metric_role).inc()
        )
        try:
            connection = super().connect()
            outcome = "success"
            return connection
        except SQLAlchemyTimeoutError as exc:
            if exc.code == "3o7r":
                outcome = "timeout"
                self._safe_metric_update(
                    lambda: sqlalchemy_pool_timeouts_total.labels(role=self._metric_role).inc()
                )
            raise
        finally:
            if counted:
                self._safe_metric_update(
                    lambda: sqlalchemy_pool_acquiring.labels(role=self._metric_role).dec()
                )
            self._safe_metric_update(
                lambda: sqlalchemy_pool_acquire_seconds.labels(
                    role=self._metric_role, outcome=outcome
                ).observe(perf_counter() - started)
            )
            self._refresh_metrics()

    def _do_return_conn(self, record: Any) -> None:
        try:
            super()._do_return_conn(record)
        finally:
            self._refresh_metrics()

    def dispose(self) -> None:
        try:
            super().dispose()
        finally:
            self._refresh_metrics()
