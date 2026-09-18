# EXP-BUG-090 — Custom workflow resume could change execution authority

**Mission / severity:** M13, P0.

Resume and checkpoint paths did not bind the complete definition, plan, tools,
frozen configuration, model and runtime-version authority. Named repeated steps
could lose instance identity or restore pipeline-bound output under an alias.
The runtime now freezes one sanitized authority snapshot and rejects mismatched
resume/checkpoint state. Focused regressions and the mutation harness cover each
authority input and named-step boundary.
