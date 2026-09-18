# EXP-BUG-117 — One-time API-key secret survived a project switch

**Mission / severity:** M20, P1.

The generated-key dialog retained a project A secret when the active project
changed and relabelled its setup snippet with project B's name. Project changes
now remove the one-time secret and cancel an in-progress form. The dialog also
uses the shared focus, Escape, and focus-return contract.
