# EXP-BUG-052 — global search hid adapter failures

**Mission / severity:** M09, P1.

A failed entity adapter was logged and discarded while the response looked
complete. Global search now reports partial status, failed entity types and
inexact counts; web, CLI and MCP surface the lower-bound warning.

**Green evidence:** final candidate `0da8f248`; adapter-failure regression,
frontend consumer regression and mutation passed after deployment.
