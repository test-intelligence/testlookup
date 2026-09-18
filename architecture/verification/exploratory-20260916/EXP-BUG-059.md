# EXP-BUG-059 — SQL run-summary fallback ignored allowed projects

Mongo summaries were scoped while PostgreSQL stubs fell through to a fleet
query. The SQL statement now applies the same allowed project set, including an
empty set. Regression and mutation evidence is in M10.

