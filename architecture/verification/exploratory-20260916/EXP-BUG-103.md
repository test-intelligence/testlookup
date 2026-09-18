# EXP-BUG-103 — Saved layout hydration could persist stale scope and values

**Mission / severity:** M17, P0.

The analytics hook could use a shared row as a PATCH target, retain persistence
authority across project changes, mark a failed request hydrated, or save the
previous render's widgets. Project/page scoped hydration and write fences now
make the owned row and the submitted layout explicit.
