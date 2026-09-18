# EXP-BUG-077 — Web configuration errors discarded structured detail

The frontend flattened object-shaped FastAPI errors, hiding the validation
reason needed to correct a refused configuration. The API error adapter now
retains structured detail for the configuration panel. Regression and mutation
evidence is in M11.
