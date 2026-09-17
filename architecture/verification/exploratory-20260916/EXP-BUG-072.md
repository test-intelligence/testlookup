# EXP-BUG-072 — Invocation retry could change frozen authority

Retry did not bind its decision to the accepted configuration fingerprint. It
now compares the frozen snapshot to the current resolved version and returns a
conflict when authority changed. Regression and mutation evidence is in M11.
