# EXP-BUG-055 — capped global samples were reported as exact totals

**Mission / severity:** M09, P1.

Global totals, pages and entity counts came from small bounded adapter samples
without disclosing the cap. A cap hit now marks the response partial and counts
inexact. Consumers render a `+` suffix and lower-bound explanation.

**Green evidence:** final candidate `0da8f248`; cap-hit regression, frontend
consumer regression and mutation passed after deployment.
