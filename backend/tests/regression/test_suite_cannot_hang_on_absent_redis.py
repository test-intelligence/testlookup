"""Regression guard: a Celery dispatch must not block the suite forever.

The defect
----------
``tests/regression/test_release_link_canonical_uuid.py`` called
``stream_service.close_session`` without patching the two Celery dispatch
points, on the reasoning written in its own comment: those blocks are each
wrapped in ``try/except``, so "any failure (e.g. no Celery broker) is
swallowed".

That reasoning is wrong, and on a machine with no local Redis it **hung the
entire backend suite at ~18%** — silently, at zero CPU, with no output to
diagnose. ``apply_async`` never raises there. It calls
``ResultConsumer.on_task_call`` -> ``consume_from``, which subscribes to the
result pubsub channel and reconnects through
``kombu.utils.functional.retry_over_time``. Nothing is raised, so the
``except`` never runs.

Captured with ``faulthandler.dump_traceback_later``::

    redis/connection.py:718 in _connect
    celery/backends/redis.py:106 in _reconnect_pubsub
    kombu/utils/functional.py:318 in retry_over_time
    celery/backends/redis.py:373 in on_task_call
    celery/app/task.py:594 in apply_async
    app/services/stream_service.py:474 in close_session

``backend/CLAUDE.md`` states the local suite does not need live services, and
every dispatch call site is written on that assumption. ``tests/conftest.py``
now makes it true by neutralising the result-consumer subscription, so the
dispatch falls through to the broker publish, which *does* raise promptly.

Measured, not assumed
---------------------
With the conftest net removed and the test's own patches removed, the run
still hangs (>90s, killed). With the net alone it completes in ~9.5s. With the
test's explicit patches as well, ~1.6s.

This file pins the net: deleting it from ``conftest.py`` fails here rather
than reintroducing a silent, undiagnosable hang.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.regression


def test_result_consumer_subscription_is_neutralised():
    """The exact frame the captured stack blocked in must be a no-op."""
    celery_redis = pytest.importorskip("celery.backends.redis")

    consumer = celery_redis.ResultConsumer
    # Called unbound with a dummy self: the replacement ignores both args and
    # returns None. The real implementation would try to reach Redis.
    assert consumer.consume_from(object(), "some-task-id") is None, (
        "ResultConsumer.consume_from is live again. tests/conftest.py's "
        "_make_celery_fail_fast_without_a_broker() is what keeps a Celery "
        "dispatch from blocking forever when no Redis is running -- see this "
        "file's docstring for the captured stack."
    )


def test_the_conftest_hook_still_exists():
    """Fail-open: the assertion above could pass for the wrong reason.

    If someone replaced the hook with something else that happens to make
    ``consume_from`` return None, the guard above would still pass while the
    documented protection was gone.
    """
    import tests.conftest as conftest

    assert hasattr(conftest, "_make_celery_fail_fast_without_a_broker"), (
        "the conftest hook that neutralises the Redis result consumer is "
        "gone; the suite can hang again on a machine without Redis."
    )
