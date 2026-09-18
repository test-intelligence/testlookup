# EXP-BUG-062 — Citations were fabricated for every generated case

Every case received the first five retrieved chunks. The model must now name
valid `EVIDENCE-n` identifiers and only those resolved chunks are returned,
evaluated and persisted. Regression and mutation evidence is in M10.

