# EXP-BUG-101 — Plan membership and execution changes left no audit trail

**Mission / severity:** M16, P1.

Adding, removing or executing a plan item changed release evidence without
writing a Test Management audit row. Each operation now stages an append-only
audit record in the same transaction, including the affected case or execution
status transition.
