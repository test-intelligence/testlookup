# EXP-BUG-089 — Workflow validation admitted behavior the runtime could not execute

**Mission / severity:** M13, P0.

Registered capabilities without workflow executors, ignored per-step model
metadata, extra reviewer loops and parallel reviewers could pass validation.
Missing relational facts could also raise instead of selecting the false branch.
Compiler regressions now reject unsupported authority and bound reviewer shape;
typed-condition tests prove missing facts fail closed.
