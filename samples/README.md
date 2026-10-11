# Sample Test Results

Example test result files in four of the formats TestLookup ingests. Use them to:

- Verify your TestLookup installation ingests correctly
- Learn the expected file structure before building your own reporter
- Try the upload flow (`make demo` uploads the JUnit files automatically on a development stack)

## Files

| Directory | Format | Tests | Failures | Description |
|-----------|--------|-------|----------|-------------|
| `junit/auth-suite.xml` | JUnit / Surefire XML | 6 | 2 + 1 skipped | Auth flow: login, register, password reset, OAuth |
| `junit/payment-suite.xml` | JUnit / Surefire XML | 8 | 1 + 1 error | Payment processing: charges, refunds, gateway timeout, race condition |
| `allure/allure-results.json` | Allure JSON | 4 | 1 + 1 broken | Search feature: keyword, empty query, pagination bug, special chars crash |
| `cypress/cypress-results.json` | Cypress (Mochawesome) JSON | 5 | 1 + 1 pending | E2E checkout flow: add to cart, discount, purchase, gateway error |
| `playwright/playwright-results.json` | Playwright JSON | 4 | 1 + 1 flaky | User profile: display name, avatar, password complexity, concurrent edit |

TestLookup also ingests TestNG XML, Robot Framework `output.xml`, NUnit 3 XML, Visual Studio TRX, xUnit.net v2 XML, pytest-json-report, Cucumber JSON and a zip archive of result files (for example an Allure results directory). There are no samples for those formats here; the upload API detects every format from the file content. Cypress and Playwright parsing are behind the `cypress_ingest` and `playwright_ingest` feature flags, which are on by default (see [docs/operations/feature-flags.md](../docs/operations/feature-flags.md)).

## Uploading manually

```bash
# Via the CLI (install it with `pip install ./cli`; --project is the project UUID):
testlookup upload file samples/junit/auth-suite.xml --project <project-uuid> --build demo-1 --wait
testlookup upload file samples/allure/allure-results.json --project <project-uuid> --build demo-2 --format allure

# Or every .xml, .json and .trx file in one directory:
testlookup upload dir samples/junit --project <project-uuid> --build demo-3

# Via curl, with an API key (or -H "Authorization: Bearer <access token>"):
curl -X POST http://localhost:8000/api/v1/ingest/file \
    -H "X-API-Key: qai_your_api_key_here" \
    -F "file=@samples/junit/auth-suite.xml" \
    -F "project_id=<project-uuid>" \
    -F "build_number=demo-1" \
    -F "format=auto"
```

The CLI reads the server URL and credentials from `testlookup auth login` or from `TESTLOOKUP_URL` and `TESTLOOKUP_API_KEY`; see [cli/README.md](../cli/README.md). Uploading needs the QA_ENGINEER role or higher on the project.

## Licensing

All sample data in this directory is original, synthetic, and released into the public domain (CC0 1.0). No real test results, stack traces, or proprietary information is included.
