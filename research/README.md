# research — test-intelligence research corpus

**Why this exists**: the deep-research workflow's result cache is **session-bound**.
Run `wf_9368f81f-165` cannot be resumed from a new session — reopening it would
re-fetch every source and re-run every verifier from scratch. These files are the
permanent copy so that never has to happen.

The run cost 4 attempts (3 killed by session limits) and ~6.1M subagent tokens
across 105 agents. Everything it produced is here.

## Files

| file | what it holds |
|---|---|
| `TEST_INTELLIGENCE_RESEARCH.md` | **The report.** 11 verified findings, 7 refuted claims, a revised roadmap, and the open questions. |
| `claims-corpus.md` | All **124 claims** from all 25 sources, each with its verbatim supporting quote, source quality, publish date, and verification status. Plus every recovered verifier verdict with its reasoning and counter-sources. **Start here.** |
| `claims-raw.json` | Machine-readable twin of the above: `{sources, verdicts, taskResults}`. |
| `synthesis.json` | The final synthesis verbatim — 11 findings with evidence/vote/confidence notes, 7 refuted claims, caveats, open questions. |

The written-up conclusions live in `TEST_INTELLIGENCE_RESEARCH.md`, alongside this file.

## Status key in `claims-corpus.md`

- `[CONFIRMED]` — survived a 3-vote adversarial panel
- `[CHECKED]` — has ≥1 recovered verifier verdict (see the verdict section)
- unmarked — extracted from source, **never adversarially verified**; the verifier
  agents died on session limits, not on the evidence. Directional only.

Only 25 of the 124 claims were ever put to a panel (the workflow caps verification
breadth). 18 survived, 7 were killed, and the synthesizer merged the 18 into 11
findings.

## If you reopen this research

Do **not** re-run the whole workflow — the extraction phase is already done and is
the expensive part. Target the gaps instead, listed as open questions in
`TEST_INTELLIGENCE_RESEARCH.md` §5: release-gate credibility, LLM-RCA time-to-diagnose evidence,
named-vendor practitioner complaints, and whether hyperscale magnitudes hold for
small self-hosted teams.

The original workflow script is at
`.claude/projects/…/workflows/scripts/deep-research-wf_9368f81f-165.js` and the
byte-exact research question is the `question` field in `synthesis.json`.
