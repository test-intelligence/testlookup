# EXP-BUG-060 — Raw RAG prompts were persisted

Generation batches and accepted cases stored the unredacted user prompt. The
service now stores the redacted prompt and records sanitized prompt/output
hashes and provenance. Regression and mutation evidence is in M10.

