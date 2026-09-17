# EXP-BUG-058 — Revoked members retained existing chat sessions

Session ownership was checked without rechecking current project membership.
The session dependency now refuses owners removed from the session's project.
Regression and mutation evidence is in M10.

