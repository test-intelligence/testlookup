# EXP-BUG-115 — Decision review dialogs leaked keyboard focus

**Mission / severity:** M19, P1.

The claim-correction dialog and evidence drawer did not contain keyboard focus,
close consistently with Escape, or restore focus to their trigger. Both now use
the shared modal keyboard contract.
