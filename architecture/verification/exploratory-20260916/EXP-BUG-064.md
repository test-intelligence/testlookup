# EXP-BUG-064 — Archived sources remained authoritative and deletion erased lineage

Archived vectors could still ground answers, while hard delete cascaded citation
history. Retrieval now requires the exact returned vector to have an active,
same-project relational chunk row with the exact source/vector pair, so orphaned
partial writes, cross-source metadata corruption and failed vector retirement
cannot remain authoritative. Archive retires vectors, marks cases stale and
preserves lineage. Regression and mutation evidence is in M10.
