# TestLookup Roadmap

**Last updated:** 2026-04-15

This roadmap describes what's in flight, what's planned, and what's on hold. Priorities change as we learn from users and contributors. The canonical status for each item is the label on GitHub issues or the phase column below.

## Legend

- **In progress** -- someone is actively working on this
- **Planned** -- on the near-term list, not yet started
- **Idea** -- on our radar but not committed to
- **On hold** -- previously planned, now paused

## v0.1.0 -- OSS launch (current milestone)

The first public release. Everything here is a launch blocker.

| Item | Status | Phase |
|------|--------|-------|
| Repo hygiene and trust signals (SECURITY, CONTRIBUTING, CoC) | In progress | OS-1 |
| Narrative narrowing (README rewrite, stability labels) | Planned | OS-2 |
| First-run delight (demo mode, sample data, scripted walkthrough) | Planned | OS-3 |
| Evidence and benchmarks (reproducible benchmark pack) | Planned | OS-4 |
| MCP hardening and sample client flows | Planned | OS-5 |
| Scope enforcement (experimental features flag-off by default) | Planned | OS-6 |
| v0.1.0 tag, release, and launch posts | Planned | OS-7 |

## v0.2.0 -- Stabilise the core (next milestone)

Once v0.1.0 is out and we've absorbed early feedback.

| Item | Status |
|------|--------|
| Backfill e2e coverage for every core feature | Planned |
| Wire `make test-e2e` into CI as a blocking check | Planned |
| Audit-outbox pattern to retire the `COMMIT_ALLOWLIST` ratchet | Planned |
| Grafana dashboard "Tier 0-2 operations" JSON | Planned |
| Live `alembic downgrade` round-trip test against a fresh Postgres | Planned |
| Per-feature user guides (one page per core feature) | Planned |

## Experimental capabilities (in-repo, flag-off)

These are in the codebase today but not part of the v0.1.0 headline. Each has its own feature flag and is off by default. Graduation to core requires meeting the criteria in `docs/features/FEATURE_FLAG_INVENTORY.md`.

- RAG knowledge generation (Jira / Confluence / URL connectors)
- Deep multi-agent investigation pipeline
- Flaky auto-quarantine state machine
- LLM cost budget and billing primitive
- GitHub Checks integration (PAT-based)
- Outbound webhooks with HMAC signing
- Release compliance pack export (SOX/HIPAA/SOC 2 audit artefact)
- RAG faithfulness guardrails
- Perf regression detection (Welford baselines)
- Weekly retro digest
- Team value metrics split via service ownership rules

## On hold

These were previously considered but are not currently prioritised.

- Desktop app (macOS / Windows) -- planning documents only, no code
- SDK distribution center -- superseded by direct SDK downloads
- Full SSO/SAML and SCIM provisioning -- enterprise path, no OSS timeline
- Managed cloud deployment -- not an OSS project concern
- Ragas hosted evaluator -- blocked on API credentials
- GitHub App path (installation tokens) -- blocked on a registered App

## How to influence the roadmap

1. **Open a discussion issue** for the item you want to see prioritised.
2. **React with a thumbs-up** on existing roadmap issues to signal interest.
3. **Contribute a PR** for items labelled `good first issue` or `help wanted`.
4. **Back your case with a benchmark** -- claims about why X matters land faster with data.

## What TestLookup is not trying to be

We intentionally do not chase every adjacent category. TestLookup's wedge is **local-first test failure intelligence** -- ingest, cluster, triage, and release-gate. We say no to:

- Test authoring and test management (Allure, TestRail, Xray cover this)
- Test orchestration (Testkube, k6, Playwright's own orchestrator cover this)
- CI system replacement (Jenkins, GitHub Actions, Buildkite cover this)
- Cloud-only AI testing services (several incumbents; different category)

If your idea fits one of the above buckets, it's probably not a good fit for TestLookup -- but it might be a great idea for a different project. File an issue anyway and we can discuss.
