# EXP-BUG-095 — G4 snapshots could mix concurrent configuration generations

**Mission / severity:** M13, P0.

Evaluation and execution could combine cached or concurrently changed global AI
settings, project configs and feature flags. A shared global-then-project lock
order now protects authority snapshots and every corresponding writer. Fresh
reads bypass caches, endpoint identity is hashed rather than disclosed, and
finalization preserves the original prompt, endpoint and behavior-plan
authority.
