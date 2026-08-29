# QA Run 2026-08-29-01 — Baseline

- Source branch at start: `fix/TL-2026-08-28-01-002-public-base-url`
- Source tree at start matched `origin/main`; no pre-existing working-tree edits.
- Existing homelab image before this run: `build-20260829-015431`.
- Authenticated exploratory testing used the seeded admin account in the local homelab; credentials are intentionally not recorded.
- Primary and secondary route sweeps completed without in-page error text or browser console errors.
- Backend health, readiness, dependency details, and OpenAPI probes returned HTTP 200.

## Scope exercised

- Dashboard and project scope switching (`All Projects`, `Payment Service`)
- Testing, AI Reports, Management, profile, and settings route families
- Test-management tabs, run detail/intelligence, run compare, suite coverage, and documentation
- Read-only filters on runs and project-scoped analytics pages
- Default 1280px browser layout and post-deploy browser console diagnostics

## Initial finding

The dashboard top row was reproduced with `grid-template-columns: 154px 800px`; the release-readiness lede content measured `0px` wide and wrapped one word per line. The same result occurred for both tested project scopes. This was triaged as a confirmed S4/P2 UI defect before the fix.
