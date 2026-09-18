# EXP-BUG-071 — Workflow execution reread mutable project configuration

An accepted invocation could plan later workflow steps from changed live
configuration. Every step now receives the sanitized resolved snapshot frozen
at acceptance, with only current safety ceilings reapplied. Regression and
mutation evidence is in M11.
