# EXP-BUG-037 — superseded Investigator subject was reported as pending

**Mission / severity:** M08, P1.

Stored Investigator excerpts used the live pipeline lookup, which excludes
superseded reviews. The content failed closed, but the response lost the true
terminal state and review ID.

Excerpt gating now derives the stored verdict's evidence hash and uses the
historical pipeline-and-evidence lookup. Superseded state remains terminal and
truthful while the narrative stays withheld.

**Green evidence:** exact homelab candidate `843a6565`, focused narrative tests
and the eight-mutation review-subject harness.
