# EXP-BUG-023 — JSON draft exports omitted their watermark

**Mission / severity:** M08, P1.

Pending JSON intelligence exports returned model content without the draft
watermark required by the project opt-in. The JSON renderer now carries the
same watermark as PDF/HTML exports.

**Green evidence:** exact deployed `77fc9bd2`; focused distribution suite and
asserted M08 distribution mutations passed. Fix: `80c50d14`.
