# EXP-BUG-053 — suite results lost project identity

**Mission / severity:** M09, P1.

Global suite search grouped identical suite names across projects and emitted a
name-only URL. Results now group by project, include project identity, encode
the name and project query parameters, and select the matching authorized
project before web navigation.

**Green evidence:** final candidate `0da8f248`; scope/link and web navigation
regressions plus three mutations passed after deployment.
