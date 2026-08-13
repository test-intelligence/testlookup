# Action Register

| ID | Severity | Status | Owner area | Finding | Recommended action | Validation expected |
|---|---|---|---|---|---|---|
| PR-001 | P1 | Open | Release | Shared `CHANGELOG.md` insertions will conflict as PRs merge | Merge #569 first; resolve each later changelog hunk without dropping entries | Final main diff contains all four dated entries; protected CI green |
| PR-002 | P2 | Open | Backend tests | #570 tests call the handler directly, not the mounted route | Add TestClient/router registration regression | `/health/version` returns 200 through the actual app route |
| PR-003 | P2 | Open | CLI security | #571 echoes complete configured `base_url` in transport errors | Redact URL userinfo/query/fragment before formatting | Canary credentials never appear in CLI output/log capture |
| PR-004 | P2 | Open | Frontend | #568 emits an untrimmed ID after a trimmed non-empty check | Use normalized ID or UUID validation | Whitespace-padded fixture produces a valid command |
| PR-005 | P2 | Deferred | CLI UX | Upload HTTP status errors remain generic while shared requests are mapped | Normalize in a follow-up or document scope | 4xx upload output matches shared client UX |
| PR-006 | P2 | Open | Release | #335 is stale and conflicts with the authoritative #569 update | Close #335 as superseded after #569 merges | #335 closed with supersession note; no duplicate dependency change |

