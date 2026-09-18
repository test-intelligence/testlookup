# EXP-BUG-085 — Durable idempotency scope disagreed with the API contract

**Mission / severity:** M12, P0.

Redis scoped keys by user, project and agent route, while migration 0178's
unique index used only user and key. Reusing an ordinary client key in another
project or agent route therefore failed despite the documented independent
scope. Migration 0190 replaces the index with the four-part scope and updates
the replay lookup to match.

Model/migration tests verify the partial index and downgrade. A real PostgreSQL
integration test uses one key across projects and agents and proves independent
rows. The live journey proves same-scope replay and changed-body refusal.
