# EXP-BUG-104 — Analytics picker offered widgets without renderer contracts

**Mission / severity:** M17, P1.

The widget registry contained identifiers that pages never rendered, while
several rendered panels ignored saved visibility. The offered registry now
matches shipping renderer identifiers and the tested page panels honor saved
selection.
