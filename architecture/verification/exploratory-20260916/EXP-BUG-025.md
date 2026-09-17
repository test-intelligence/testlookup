# EXP-BUG-025 — report versions borrowed another pipeline review

**Mission / severity:** M08, P0.

An immutable decision report could resolve authority from the newest deep run
for its parent test run. Versions now bind to the historical review envelope of
their exact pipeline subject, including superseded terminal state.

**Green evidence:** exact deployed `77fc9bd2`; focused envelope/report tests and
four asserted subject mutations passed. Fix: `971e730d`.
