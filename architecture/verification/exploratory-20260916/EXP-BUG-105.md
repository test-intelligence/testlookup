# EXP-BUG-105 — Analytics periods changed with sparse rows and local time

**Mission / severity:** M17, P1.

Trend and comparison boundaries depended on elapsed time or returned row count,
and Billing displayed a half-open period end as if it were inclusive. UTC
calendar boundaries now define the requested periods and displayed final day.
