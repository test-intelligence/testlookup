# EXP-BUG-050 — hybrid provider outage was labeled as hybrid success

**Mission / severity:** M09, P1.

Semantic search swallowed a Chroma failure into an empty tuple, so hybrid mode
could return keyword rows while claiming hybrid retrieval. Provider failures
now propagate to the router fallback, which labels the response keyword.

**Green evidence:** exact candidate `5e43ba14`; fallback regression and mutation
passed after deployment.
