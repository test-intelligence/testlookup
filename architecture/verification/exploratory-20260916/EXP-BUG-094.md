# EXP-BUG-094 — G4 replay evidence did not prove the behavior it scored

**Mission / severity:** M13, P0.

Replay identity omitted model, tool, config, prompt, runtime, topology and corpus
inputs; cached outputs could be presented as measured control flow. G4 now
requires a tamper-evident input/output receipt, deterministically selects the
latest terminal attempt, binds the evidence corpus into the manifest, and marks
topology unmeasured. Any executable-authority mismatch is publish-blocking.
