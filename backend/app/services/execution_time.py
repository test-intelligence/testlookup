"""Resolve when a test run actually executed, from an untrusted client value.

The problem
-----------
``TestRun.start_time`` is read by ~43 call sites for ordering, time-window
filtering, and duration. Every one of them assumes it means *when the run
executed*. On the upload paths it does not: both ``ingestion`` and
``ingestion_pipeline`` stamp ``datetime.now()`` at INGEST, so for a JUnit
archive uploaded an hour after the job finished, the column is an hour wrong —
and for one uploaded the next morning, a day.

That was tolerable while nothing depended on it. It stopped being tolerable
with the release attribution ladder: rungs 4 (cutoff window) and 5 (the release
active when the run executed) both predicate on execution time, so with ingest
time in the column both silently degrade to "whatever release is current" —
which is precisely the mis-attribution the as-of design exists to prevent.

Why overwrite rather than add a column
--------------------------------------
A second "when did this run" column would force all 43 readers to choose
between them, and every one that chose wrong would be wrong silently. One
column that means what its name says is the smaller change: existing readers
get *more* accurate without knowing anything happened.

Why the value needs bounding
----------------------------
It arrives from a client, and a bad one is not a validation error — it is a
run that vanishes. ``start_time`` drives the 30-day window every dashboard
defaults to, so a timestamp far in the past silently removes the run from every
default view, and one in the future pins it to the top of every ordering
forever. Neither raises anything.

So the value is bounded rather than trusted, and the boundary is deliberately
asymmetric:

* **Future** is always wrong and always clock skew, so it is clamped to now.
  A small skew is normal and needs no noise; a large one is worth saying.
* **Past** is legitimate — uploading yesterday's archive is the case this
  feature exists for — so it is accepted far back. Only implausible values
  (older than ``MAX_BACKDATE``) fall back to ingest time, because at that point
  the likeliest explanation is a unit mix-up (seconds parsed as milliseconds,
  or a zero epoch) rather than a genuinely old run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

#: How far back a supplied execution time may sit before it is treated as
#: nonsense rather than history. A year is generous on purpose: re-ingesting an
#: old archive is a real use case, and rejecting it would be worse than
#: accepting it. Beyond this the value is almost certainly a unit error.
MAX_BACKDATE = timedelta(days=365)

#: Future skew tolerated without comment. Runner clocks drift by seconds;
#: anything beyond a few minutes is worth a log line even though it is clamped
#: either way.
QUIET_FUTURE_SKEW = timedelta(minutes=5)


def resolve_execution_time(
    supplied: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    context: str = "ingest",
) -> datetime:
    """Return the execution time to store, bounding an untrusted value.

    ``supplied`` is whatever the client sent (or None). ``now`` is injectable
    so callers and tests share one clock rather than each reading their own.

    Never raises. A rejected value degrades to ``now`` — the pre-existing
    behaviour — because failing an ingest over a bad timestamp would lose the
    test results, which are the thing of value.
    """
    now = now or datetime.now(timezone.utc)

    if supplied is None:
        return now

    # A naive datetime is ambiguous, and guessing its zone would be a silent
    # error of up to a day. Treat it as UTC — which is what every client that
    # sends one means — but say so, so a client sending local time can be
    # found and fixed.
    if supplied.tzinfo is None:
        logger.info(
            "execution_time_naive_assumed_utc", context=context, supplied=str(supplied)
        )
        supplied = supplied.replace(tzinfo=timezone.utc)

    if supplied > now:
        if supplied - now > QUIET_FUTURE_SKEW:
            logger.warning(
                "execution_time_in_future_clamped",
                context=context,
                supplied=supplied.isoformat(),
                clamped_to=now.isoformat(),
                skew_seconds=int((supplied - now).total_seconds()),
            )
        return now

    if now - supplied > MAX_BACKDATE:
        logger.warning(
            "execution_time_implausibly_old_ignored",
            context=context,
            supplied=supplied.isoformat(),
            using=now.isoformat(),
            age_days=int((now - supplied).days),
        )
        return now

    return supplied
