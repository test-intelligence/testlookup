# Native producer reports

The release browser tests upload these reports to a new project. Each producer ran an intentionally passing, failing and skipped test. The Allure run also raised a `RuntimeError` outside an assertion to generate a broken test. Independent expected names and outcomes live in `frontend/tests/release/report-upload.spec.ts`.

| Upload format | Producer that generated the input | Report output |
| --- | --- | --- |
| JUnit XML | pytest with `--junitxml`, running the adjacent `producer.py` | `../pytest-junit.xml` |
| pytest JSON | pytest-json-report with `--json-report --json-report-file=pytest-report.json`, running the same tests | `../pytest-report.json` |
| Robot Framework | Robot running the adjacent `cases.robot` with `--output robot.xml` | `../robot.xml` |
| TestNG XML | TestNG 7.10.2 with Maven Surefire 3.5.2, `mvn test` | Surefire's `testng-results.xml`, stored as `testng.xml` |
| NUnit 3 XML | NUnit 3.14.0, NUnit3TestAdapter 4.6.0 and Microsoft.NET.Test.Sdk 17.11.1, `dotnet test -- NUnit.TestOutputXml=output` | The adapter's `Producer.xml`, stored as `nunit.xml` |
| TRX | The same NUnit tests with the native VSTest logger, `dotnet test --logger "trx;LogFileName=release.trx"` | `report.trx` |
| xUnit.net v2 XML | xunit.runner.console 2.9.3 running the .NET test assembly with `-xml output/xunit.xml` | `xunit.xml` |
| Allure | allure-pytest 2.13.5 with pytest, `--alluredir=allure-results` | Four individual `allure-results/*-result.json` records |
| Cypress Mochawesome | Cypress 13.17.0 running headless Electron with mochawesome 7.1.3, JSON reporting enabled | `cypress.json` |
| Playwright JSON | Playwright Test 1.64.1's native JSON reporter, `playwright test --workers=1 --reporter=json` | `playwright.json` |
| Cucumber JSON | Cucumber.js 11.3.0, `cucumber-js --require features/steps.cjs --format json:release.json` | `cucumber.json` |
| ZIP | The browser test packs the original Allure records under `allure-results/` and uploads the archive. A second upload selects JUnit and Robot together and exercises the form's own ZIP creation. | Generated when the test runs |

The original test programs and reporter configurations are in `producers/`. For Allure, run pytest against both `../producer.py` and `producers/allure_error_case.py`. For Playwright, make the frontend's Playwright Test dependency available to the producer. For xUnit.net, build `Producer.csproj` and pass the compiled `Producer.dll` to the framework's console runner. Cypress uses its headless Electron browser.

The .NET tests use `Passes`, `Fails` and `Skips`. Cucumber uses the same three scenario names. The TestNG, Cypress and Playwright cases use lowercase names. Generated host names, host-specific paths and Allure thread identifiers were normalized before committing the report files.
