# EXP-BUG-119 — Stale previews could delete newly protected evidence

**Mission / severity:** M21, P0.

Deletion preview checked in-progress status and citations, but workers trusted
that earlier answer. A run linked to a release, cited by a compliance pack or
decision report, or resumed after preview would still be deleted. Search-index
purging happened before any current protection check.

Single-run and criteria workers now lock the current run and recheck every
mutable status and citation blocker before the first search or cross-store
side effect. Decision-report publication takes a compatible Postgres share
lock on that run through its Mongo write, so publication and deletion cannot
cross between the final blocker check and the destructive boundary. A live
race showed an update-lock contender waiting 0.154 seconds for publication to
finish. Critic failure attempts and summary projections take the same subject
lock; when deletion wins and removes the run first, those writes are
suppressed. The inverse live race waited 0.167 seconds and produced zero Mongo
writes. A blocked criteria item is retained and contributes to an honest
failed or partial job result.
