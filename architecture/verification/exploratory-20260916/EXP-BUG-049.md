# EXP-BUG-049 — entity counts overlapped one AsyncSession

**Mission / severity:** M09, P1.

Six count queries were scheduled with `asyncio.gather` on one request-scoped
SQLAlchemy `AsyncSession`, which can reject overlapping driver operations.
They now execute sequentially. A tracking-session regression fails if more than
one operation is active.

**Green evidence:** exact candidate `5e43ba14` deployed as
`build-20260917-095930`; focused M09 regression and mutation passed.
