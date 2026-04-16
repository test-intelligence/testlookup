# Sample Test Results

This directory contains example test result files for every format TestLookup can ingest. Use them to:

- Verify your TestLookup installation ingests correctly
- Learn the expected file structure before building your own reporter
- Feed the `make demo` target (which uploads these automatically)

## Files

| Directory | Format | Tests | Failures | Description |
|-----------|--------|-------|----------|-------------|
| `junit/auth-suite.xml` | JUnit / Surefire XML | 6 | 2 + 1 skipped | Auth flow: login, register, password reset, OAuth |
| `junit/payment-suite.xml` | JUnit / Surefire XML | 8 | 1 + 1 error | Payment processing: charges, refunds, gateway timeout, race condition |
| `allure/allure-results.json` | Allure JSON | 4 | 1 + 1 broken | Search feature: keyword, empty query, pagination bug, special chars crash |
| `cypress/cypress-results.json` | Cypress JSON | 5 | 1 + 1 pending | E2E checkout flow: add to cart, discount, purchase, gateway error |
| `playwright/playwright-results.json` | Playwright JSON | 4 | 1 + 1 flaky | User profile: display name, avatar, password complexity, concurrent edit |

## Uploading manually

```bash
# Via the CLI (after make dev):
testlookup upload samples/junit/auth-suite.xml --project <project-uuid> --build demo-1
testlookup upload samples/allure/allure-results.json --project <project-uuid> --build demo-2 --format allure

# Via curl:
curl -X POST http://localhost:8000/api/v1/ingest/file \
    -H "Authorization: Bearer <token>" \
    -F "file=@samples/junit/auth-suite.xml" \
    -F "project_id=<project-uuid>" \
    -F "build_number=demo-1" \
    -F "format=auto"
```

## Licensing

All sample data in this directory is original, synthetic, and released into the public domain (CC0 1.0). No real test results, stack traces, or proprietary information is included.
