# EXP-BUG-075 — Retired writes parsed request bodies before returning 405

The legacy Investigator and Fixer PUT aliases still invoked body validation, so
empty or malformed JSON returned 422 instead of the retirement contract. Their
routes now return canonical 405 responses before body parsing. Regression and
mutation evidence is in M11.
