# EXP-BUG-091 — Reviewer continuation could bypass blocking findings

**Mission / severity:** M13, P0.

A blocking deterministic failure could be represented as `pass_with_flags`, and
the final reviewer rejection did not reliably terminate the custom graph. The
verdict model now rejects contradictory continuation, low independent-model
agreement forces flags and human review, and final rejection stops execution
after the one declared retry.
