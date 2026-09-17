# EXP-BUG-054 — similar search hid database failure as no evidence

**Mission / severity:** M09, P1.

The similar-evidence route caught database failures and returned a plausible
empty response. It now treats only malformed UUID input as missing; database
failures propagate to the normal error path.

**Green evidence:** exact candidate `5e43ba14`; provider-failure regression and
mutation passed after deployment.
