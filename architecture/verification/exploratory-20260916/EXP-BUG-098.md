# EXP-BUG-098 — Plans accepted case identifiers from another project

**Mission / severity:** M16, P0.

Plan membership looked up the case identifier without checking the plan's
project. The service now checks identifier and project together and returns the
same 404 for a missing or foreign case.
