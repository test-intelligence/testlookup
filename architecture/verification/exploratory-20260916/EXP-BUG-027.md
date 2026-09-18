# EXP-BUG-027 — advisory release values lacked role authorization

**Mission / severity:** M08, P1.

An ordinary authenticated caller could request `allow_advisory=true` and reveal
an unaccepted release recommendation. Only QA Lead and Admin callers may now use
the interactive advisory escape.

**Green evidence:** exact deployed `77fc9bd2`; authorization regression and its
asserted mutation passed. Fix: `f0277b68`.
