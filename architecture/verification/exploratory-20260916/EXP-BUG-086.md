# EXP-BUG-086 — Sync waiting and OpenAPI described the wrong bounded contract

**Mission / severity:** M12, P1.

A zero-second sync wait slept before its first read, and the invoke operation
did not publish all of its 200/202/409/422/503 outcomes. The wait now reads
immediately and sleeps only for bounded remaining time; OpenAPI declares each
response and `Retry-After`.

Focused unit regressions cover zero wait and schema responses. Generated API
documentation checks the mounted contract, and the deployed sync journey
completed with HTTP 200 and status `passed`.
