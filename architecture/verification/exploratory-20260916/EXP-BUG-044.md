# EXP-BUG-044 — critic release event could bind to a concurrent pipeline

**Mission / severity:** M08, P1.

The report critic passed its evidence hash to the emitter without its pipeline
ID. A concurrent decision replacement could make the emitter stage the older
report under the newer pipeline and deduplication scope.

The critic now passes both immutable identifiers. The emitter refuses when the
persisted decision pipeline no longer matches and does not load or publish the
older report.

**Green evidence:** exact candidate `21f1e98f` deployed as
`build-20260917-075932`; concurrent-pipeline regression and the webhook replay
mutation harness.
