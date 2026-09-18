# EXP-BUG-032 — immutable report review was not evidence-bound

**Mission / severity:** M08, P0.

Historical report lookup selected the newest review for a pipeline without
matching the report's `evidence_bundle_sha256`. A changed evidence bundle on the
same pipeline could authorize different immutable bytes.

Version metadata and selected-report internals now carry the evidence hash, and
the historical lookup matches pipeline plus hash while retaining terminal
superseded rows. The review-subject mutation harness covers dropped metadata,
query predicates, and router arguments.

**Green evidence:** exact homelab candidate `843a6565`, focused M08 suite and
eight-mutation review-subject harness.
