# TestLookup reporter for JavaScript and TypeScript

Streams test results to a TestLookup server while the tests run, with ready-made reporters for **Jest** and **Mocha** and a small client for anything else. Results appear on **Runs → Live** (`/live`) during the run and on Runs, Failures and Trends when it finishes.

Other reporters and the shared concepts (credentials, live identity) are in [client/README.md](../README.md).

## Install (from source)

The package (`testlookup-reporter`, version 1.0.0, Node.js 18 or newer, no runtime dependencies) is not published to npm. Build it from a clone and install the packed tarball into your project:

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup/client/js
npm ci
npm run build          # tsc: compiles src/ to dist/
npm pack               # writes testlookup-reporter-1.0.0.tgz

cd /path/to/your/project
npm install --save-dev /path/to/testlookup/client/js/testlookup-reporter-1.0.0.tgz
```

`npm test` in `client/js` builds and runs the package's own tests. `jest` (27 or newer) and `mocha` (9 or newer) are optional peer dependencies; install the one you use.

The build emits CommonJS files under `dist/` (`testlookup-reporter.js`, `jest-reporter.js`, `mocha-reporter.js`, each with type declarations). Load them by package name, with `require` or `import`: `testlookup-reporter`, `testlookup-reporter/jest-reporter`, `testlookup-reporter/mocha-reporter`.

## Credentials

The reporters take an **API key** (`apiKey`, sent as `X-API-Key`) or a **JWT access token** (`token`, sent as `Authorization: Bearer`); a value that starts with `qai_` is sent as an API key even when given as the token. The two resolve as a pair: one passed as an option wins; otherwise the first source holding either one wins, environment variables (`TESTLOOKUP_API_KEY`, `TESTLOOKUP_TOKEN`) before the configuration file; within one source the API key wins.

For CI, use a project-scoped streaming key from **Settings → Streaming API keys**, whose owner has the QA_ENGINEER role or higher on the project. A token comes from `POST /api/v1/auth/login` (or `TestLookupReporter.login()`, below) and lasts 12 hours by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` on the server).

## Jest

```js
// jest.config.js
module.exports = {
  reporters: [
    'default',
    ['testlookup-reporter/jest-reporter', {
      // Every option is optional: unset ones come from TESTLOOKUP_* variables,
      // then testlookup.properties / testlookup.yaml (see Configuration).
      projectId:   '<project UUID or name>',
      buildNumber: process.env.CI_PIPELINE_ID,
    }],
  ],
}
```

Options: `baseUrl`, `apiKey`, `token`, `projectId`, `buildNumber` (default `jest-<epoch ms>`), `branch`, `commitHash`, `launchName`, `suiteName` (default the launch name), `releaseName`, `framework` (default `jest`), `batchSize`, `batchIntervalMs`. Each falls back to the variable and file key listed under [Configuration](#configuration). If the URL, credential or project is missing, the reporter logs a warning and stays inactive; your tests still run.

Each test is recorded once, under its full name, with the `describe` path (`Outer > Inner`) as the suite. `passed` maps to `PASSED`, `failed` to `FAILED` (with the failure message and stack), and `skipped`, `pending`, `todo` and `disabled` to `SKIPPED`. The session is closed when the run completes.

## Mocha

```js
// .mocharc.cjs
module.exports = {
  reporter: 'testlookup-reporter/mocha-reporter',
  reporterOptions: {
    testlookupProject: '<project UUID or name>',
  },
}
```

or on the command line: `mocha --reporter testlookup-reporter/mocha-reporter --reporter-option testlookupProject=<project UUID or name>`.

Reporter options: `testlookupUrl`, `testlookupApiKey`, `testlookupToken`, `testlookupProject`, `testlookupBuild` (default `mocha-<epoch ms>`), `testlookupBranch`, `testlookupCommit`, `testlookupLaunch`, `testlookupSuite` (default the launch name), `testlookupRelease`; each falls back as described under [Configuration](#configuration). Passing, failing and pending tests are recorded as `PASSED`, `FAILED` and `SKIPPED`, with the parent suite's full title as the suite. A result that finishes before the session is open waits for it, and Mocha waits for the final flush before exiting. Mocha runs one reporter at a time, so this reporter replaces the console output for that run.

## Playwright, Vitest, Cypress and Cucumber

Each adapter takes the same options (`testlookupUrl`, `testlookupProject`, `testlookupApiKey` or
`testlookupToken`, and optionally `testlookupBuild`, `testlookupBranch`, `testlookupCommit`,
`testlookupLaunch`, `testlookupSuite`, `testlookupRelease`), falling back to `TESTLOOKUP_*` and
`testlookup.properties` like the Jest and Mocha reporters. A missing URL, credential or project turns
the adapter off with one warning; a network error is logged and never fails your run.

| Runner | Entry point | What streams | Supported |
|---|---|---|---|
| Playwright Test | `testlookup-reporter/playwright-reporter` (in `reporter:`) | each attempt as it ends; a retry is `metadata.attempt` 2; one suite per Playwright project | `@playwright/test` >= 1.30 |
| Vitest | `testlookup-reporter/vitest-reporter` (in `test.reporters`) | each test as it ends (Vitest 3 `onTestCaseResult`, Vitest 1/2 `onTaskUpdate`); skipped tests at the end | `vitest` >= 1 |
| Cypress | `testlookup-reporter/cypress-plugin` (call it in `setupNodeEvents`) | per **spec**: Cypress hands results to Node at `after:spec`; the last attempt is the result | `cypress` >= 10, `cypress run` |
| Cucumber JS | `testlookup-reporter/cucumber-formatter` (`--format`) | each scenario attempt; the worst step decides the status; the feature is the suite | `@cucumber/cucumber` >= 8 |

```ts
// playwright.config.ts
reporter: [['list'], ['testlookup-reporter/playwright-reporter', { testlookupProject: '<project-id>' }]]

// vitest.config.ts
test: { reporters: ['default', ['testlookup-reporter/vitest-reporter', { testlookupProject: '<project-id>' }]] }

// cypress.config.ts
import testlookup from 'testlookup-reporter/cypress-plugin'
e2e: { setupNodeEvents(on, config) { return testlookup(on, config, { testlookupProject: '<project-id>' }) } }
```

```bash
npx cucumber-js --format testlookup-reporter/cucumber-formatter   --format-options '{"testlookupProject":"<project-id>"}'
```

## Programmatic client

```ts
import { TestLookupReporter } from 'testlookup-reporter'

const reporter = new TestLookupReporter({
  baseUrl: 'https://testlookup.example.com',
  apiKey: process.env.TESTLOOKUP_API_KEY,          // or token: '<JWT access token>'
  projectId: '<project UUID or name>',
  framework: 'vitest',
})

const session = await reporter.startSession({ buildNumber: 'build-42', branch: 'main', launchName: 'Smoke' })
await session.record('login works', 'PASSED', 120)
await session.record('checkout works', 'FAILED', 340, { error: 'expected 200, got 500', suiteName: 'checkout' })
await session.close()                              // flush buffered events
await reporter.closeSession(session.sessionId)     // finalise the run on the server
```

- `ReporterConfig`: `baseUrl`, `projectId` and `apiKey` or `token` (required; the constructor throws without a credential); `clientName` (default the host name), `framework` (default `javascript`), `suiteName`, `releaseName` (blank: the project's default release), `batchSize` (default 50), `batchIntervalMs` (default 100), `timeoutMs` (default 30000). The client takes its values only from this object; the Jest and Mocha reporters are the ones that read configuration.
- `startSession(options)`: `buildNumber`, `runId`, `branch`, `commitHash`, `totalTests`, `machineId`, `launchName`, `suiteName`, `releaseName`, `metadata`, and the CI-context fields below. Calls `POST /api/v1/stream/sessions`.
- `LiveSession`: `record(testName, status, durationMs, { suiteName, className, error, stackTrace, tags, metadata })` with status `PASSED`, `FAILED`, `SKIPPED` or `BROKEN`; `log(message, level)`; `metric(name, value, unit)`; `close()`; `stats`.
- Events are batched (50 events or 100 ms; at most 1,000 per request) and retried up to 5 times with back-off; each batch keeps its client-generated `batch_id` across retries (see [Replay-safe live identity](../README.md#replay-safe-live-identity)).
- `verifySsl` is deprecated and has no effect (Node's built-in fetch has no per-request TLS switch); `verifySsl: false` logs a warning. To trust a private CA, start Node with `NODE_EXTRA_CA_CERTS=/path/to/ca.pem`; `NODE_TLS_REJECT_UNAUTHORIZED=0` skips verification for the whole process.

## Configuration

The Jest and Mocha reporters resolve every setting not given as an option from environment variables, then from **one** configuration file: the first found of `./testlookup.properties`, `./.testlookup/testlookup.properties`, `./testlookup.yaml`, `./.testlookup/config.yaml`, `~/.testlookup/testlookup.properties`, `~/.testlookup/config.yaml` (the same order as the Python and Java reporters; YAML needs the `yaml` package installed).

| Properties key | Environment variable | Jest option / Mocha option |
|---|---|---|
| `testlookup.endpoint` (alias `testlookup.url`) | `TESTLOOKUP_ENDPOINT` or `TESTLOOKUP_URL` (`TESTLOOKUP_ENDPOINT` wins) | `baseUrl` / `testlookupUrl` |
| `testlookup.api.key` (aliases `testlookup.api_key`, `testlookup.apiKey`) | `TESTLOOKUP_API_KEY` | `apiKey` / `testlookupApiKey` |
| `testlookup.token` | `TESTLOOKUP_TOKEN` | `token` / `testlookupToken` |
| `testlookup.project` | `TESTLOOKUP_PROJECT` or `TESTLOOKUP_PROJECT_ID` | `projectId` / `testlookupProject` |
| `testlookup.build`, `testlookup.branch`, `testlookup.commit` | `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT` | `buildNumber`, `branch`, `commitHash` / `testlookupBuild`, `testlookupBranch`, `testlookupCommit` |
| `testlookup.launch`, `testlookup.suite`, `testlookup.release` | `TESTLOOKUP_LAUNCH`, `TESTLOOKUP_SUITE`, `TESTLOOKUP_RELEASE` | `launchName`, `suiteName`, `releaseName` / `testlookupLaunch`, `testlookupSuite`, `testlookupRelease` |
| `testlookup.framework` | `TESTLOOKUP_FRAMEWORK` | `framework` / none |

## CI context

`startSession` detects the CI provider, repository, pull/merge request number, actor and run URL from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`src/ci-context.ts`). `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL` override detection, and the `ciProvider`, `ciRepo`, `prNumber`, `ciActor` and `ciRunUrl` session options override both. The JavaScript reporter does not collect a commit range.

## License

Apache-2.0, like the rest of the repository.
