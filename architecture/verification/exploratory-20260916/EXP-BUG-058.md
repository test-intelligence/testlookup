# EXP-BUG-058 — Revoked members retained existing chat sessions

Session ownership was checked without rechecking current project membership.
The session dependency now refuses owners removed from the session's project,
and session listing filters out projects the owner can no longer access.
Regression and mutation evidence is in M10.
