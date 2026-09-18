# EXP-BUG-047 — concurrent override events collapsed to the newest value

**Mission / severity:** M08, P1.

Override emitters reread the mutable singleton and derived deduplication scope
from the current audit length. If override two committed before override one's
emitter read, the first emitter published value two as ordinal two and the second
deduplicated, losing override one's event.

The route now carries the committed audit ordinal, timestamp and council snapshot.
The emitter validates that audit entry, uses its ordinal for idempotency and uses
its frozen values for the payload even when later overrides already exist.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; interleaved override regression and the webhook replay
mutation harness.
