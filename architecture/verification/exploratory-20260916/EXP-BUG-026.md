# EXP-BUG-026 — terminal review narratives remained distributable

**Mission / severity:** M08, P0.

Rejected and superseded release projections could retain blocker, condition or
summary prose. Terminal projections now expose only the review state and remove
the model narrative.

**Green evidence:** exact deployed `77fc9bd2`; focused distribution,
notification and webhook tests plus asserted terminal mutations passed. Fix:
`0be9b5cd`.
