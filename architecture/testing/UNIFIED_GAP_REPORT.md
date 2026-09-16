# Unified Gap Report

| Gap | Severity | Evidence and impact | Recommended closure |
|---|---|---|---|
| GAP-01 | High | Most Playwright product journeys require a live deployment and were explicitly excluded from ordinary CI | Grow hermetic browser coverage for review, release and run-to-defect journeys; retain live release gate |
| GAP-02 | High | Investigator narrative excerpts lack the owner-selected review subject | Add canonical subject, gate every distribution/export, and test pending/accept/reject end to end |
| GAP-03 | High | Complete ingest → intelligence → defect → release flow is split across suites | Add one PostgreSQL/Redis worker integration fixture and one live acceptance journey with shared IDs |
| GAP-04 | High | Frontend service layer measured 24.97% statements; safety-critical services had 0% | Continue contract/error tests, beginning with lifecycle, release, review and integration services |
| GAP-05 | Medium | Live browser evidence can vary with deployment data and credentials | Version fixtures, create per-run projects, retain traces/correlation IDs, and publish result manifests |
| GAP-06 | Medium | MCP/CLI are tested but have no numeric coverage ratchet | Measure each package independently and introduce conservative non-decreasing floors |
| GAP-07 | Medium | Provider quality/tier promotion cannot be proven by mocks | Complete inference-backed E9.3 paired evaluation before promoting defaults |
| GAP-08 | Medium | Combined outages and sustained broker partitions are outside per-PR testing | Add quarterly chaos game day with explicit recovery and alert-clear assertions |
| GAP-09 | Medium | Accessibility coverage is mainly component/manual | Add axe scan plus keyboard browser cases for login, triage, review and release dialogs |
| GAP-10 | Medium | Cross-project protection lacks a production canary | Add synthetic two-project canary for list/detail/search/export with alert on disclosure |
| GAP-11 | Low | Historical test plan contains stale counts and failures | Mark it historical and link this evidence-dated suite from the architecture index |
| GAP-12 | Low | Coverage reports were available locally but not enforced in frontend CI | Closed here with four metric floors and CI coverage execution |

## Release interpretation

GAP-01 through GAP-03 are the main confidence constraints. Existing service and
live tests provide substantial evidence, but a release manager still has to
join results from separate runs to prove the whole business path. GAP-02 is a
known functional backlog item, not a test-only omission.
