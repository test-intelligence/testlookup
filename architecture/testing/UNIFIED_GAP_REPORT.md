# Unified Gap Report

Status reconciled against `34110eac` on 2026-09-16. This is a code/evidence
inventory update, not a new live test run. The next execution programme is
[the Sol exploratory package](EXPLORATORY_EXECUTION_PACKAGE.md).

| Gap | Severity | Evidence and impact | Recommended closure |
|---|---|---|---|
| GAP-01 | High | Most Playwright product journeys require a live deployment and were explicitly excluded from ordinary CI | Grow hermetic browser coverage for review, release and run-to-defect journeys; retain live release gate |
| GAP-02 | High, live verification remaining | PR #119 added exact Investigator review subjects and distribution regressions | Verify the full subject/state/channel matrix against actual delivered content; see M07–M08 |
| GAP-03 | High, partially closed | PR #119 added `test_ingest_intelligence_defect_release_postgres.py` with shared release identity; dependencies remain controlled | Add deployed UI/API → real Redis worker → durable defect/release acceptance evidence with shared IDs; see M04 |
| GAP-04 | High | Historical service baseline was 24.97%; subsequent service tests improved coverage, including Test Management. See dated measurements in `CODE_COVERAGE_EXPANSION.md` | Refresh measurement and prioritize lifecycle, release, RAG, analytics, integration and delivery behavior |
| GAP-05 | Medium | Live browser evidence can vary with deployment data and credentials | Version fixtures, create per-run projects, retain traces/correlation IDs, and publish result manifests |
| GAP-06 | Closed for initial floors | MCP and CLI have independent 41% / 62% CI floors; SDK tests run separately | Expand low-coverage tools/commands and raise floors independently; deployed client parity is M22 |
| GAP-07 | Medium | Provider quality/tier promotion cannot be proven by mocks | Complete inference-backed E9.3 paired evaluation before promoting defaults |
| GAP-08 | Medium | Per-request UI outage/retry recovery is now blocking; combined outages and sustained broker partitions remain outside per-PR testing | Add quarterly chaos game day with explicit recovery and alert-clear assertions |
| GAP-09 | Medium | Accessibility coverage is mainly component/manual | Add axe scan plus keyboard browser cases for login, triage, review and release dialogs |
| GAP-10 | Medium | A hermetic two-project run-list canary now blocks stale cache disclosure during outage/recovery; production list/detail/search/export coverage remains absent | Add production-safe synthetic canary for the remaining routes with alert on disclosure |
| GAP-11 | Low | Historical test plan contains stale counts and failures | Mark it historical and link this evidence-dated suite from the architecture index |
| GAP-12 | Low | Coverage reports were available locally but not enforced in frontend CI | Closed here with four metric floors and CI coverage execution |

## Release interpretation

GAP-01 through GAP-03 remain important live confidence constraints. Existing
service and browser-contract tests do not prove that the current deployed build
delivers the complete business path. GAP-02's original implementation omission
is closed; its remaining work is deployed distribution verification. Do not
reimplement shipped subjects or describe mocked browser routes as live E2E.
