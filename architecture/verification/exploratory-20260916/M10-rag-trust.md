# M10 — RAG, knowledge and AI explanation trust

## Result

**PARTIAL — local and deployed fail-closed variants passed; positive provider and
full ingestion journeys remain blocked.** Exact executable candidate
`805f7714398ac495a656bf7c415e70dee2d0cbbb` was deployed first as
`build-20260917-125909`, built `2026-09-17T12:59:10Z`. `/health/version`
reported that revision, `/health/ready` reported PostgreSQL, MongoDB and Redis
ready, and every application Deployment was at its desired replica count on the
same tag.

## What was proved

- Non-admin chat requires a project, existing sessions and session listings
  recheck current project membership, and both Mongo and PostgreSQL run-summary
  fallbacks use the caller's allowed project set.
- Prompt text is redacted before persistence. Generation provenance records
  sanitized prompt/output SHA-256 hashes, selected sources, retrieved vectors,
  resolved citations and the evidence policy.
- Retrieved instructions are sanitized and JSON-serialized as untrusted data,
  so quotes, newlines and forged section/tag delimiters remain inside a parsed
  string rather than escaping into prompt authority.
  Model citations resolve only explicit `EVIDENCE-n` identifiers; unknown or
  absent identifiers produce no verified citation.
- PostgreSQL project/archive state and the exact active
  `(KnowledgeChunk.source_id, KnowledgeChunk.vector_id)` pair are authoritative
  over vector results. Partial Chroma upserts, cross-source metadata corruption
  and failed vector retirement therefore cannot ground a generation. More than ten
  selected sources stay individually filtered. Archive/delete retires vectors,
  marks generated cases stale and preserves citation lineage.
- Empty, malformed, missing, timed-out or failed provider output is an explicit
  failure. MinIO and Chroma failures cannot make a source look synchronized.
- Citation links permit only HTTP(S), untrusted evidence renders as text,
  notification HTML escapes stored values, and changing projects remounts the
  generation workspace and invalidates late responses.

The exact deployment returned 422 for `javascript:` source creation and 409 for
a grounded request whose random selected source had no authoritative evidence.
Both probes left no source or generated case behind.

## Verification

- Backend: **406 passed** across RAG, knowledge, chat, authorization and
  lifecycle suites; the final focused trust set was 19 passed.
- Frontend: **4 passed** across citation safety, unsupported-evidence labeling
  and project-switch state isolation; TypeScript and changed-file ESLint passed.
- Mutation: **16 backend** and **3 consumer** wrong-behavior mutations killed;
  every replacement asserted it applied exactly once and restored bytes.
- Ruff passed; mypy held at **369 errors in 114 files** (baseline 369); all
  **43** quality guards and **238** guard self-tests passed.
- Independent final review approved evidence head `ff2c5dfa` after the exact
  source/vector-pair regression and mutation closed the last review finding.

## Remaining gaps

Homelab has zero knowledge sources and no installed local model. A controlled
source ingest/sync, an answerable generation, provider timeout against a real
model, citation opening against real stored content, and quality comparison
across conflicting evidence therefore remain unexecuted. CLI and MCP expose no
knowledge/RAG workflow. Chat source chips and MCP search output still lack the
full source-detail and inert-data treatment required for a complete M10 pass.
