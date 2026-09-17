# EXP-BUG-051 — bounded semantic and hybrid pages looked exact

**Mission / severity:** M09, P1.

Hybrid retrieval always fetched the first `size * 2` candidates, making later
pages empty despite more matches. Semantic and hybrid totals also described a
bounded candidate window as the complete corpus. Candidate sizing now covers
the requested hybrid page, date-filtered semantic search uses the full bounded
window, and responses disclose lower-bound totals.

**Green evidence:** exact candidate `5e43ba14`; page-three regression and
mutation passed after deployment.
