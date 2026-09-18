# EXP-BUG-114 — Release dialogs leaked keyboard focus

**Mission / severity:** M19, P1.

The release editor and link-run dialogs did not take or contain focus, restore
the invoking control, or expose an accessible name for their icon-only close
buttons. Both dialogs now use the shared modal keyboard contract and name their
close controls.
