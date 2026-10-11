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

The build emits CommonJS files under `dist/` (`testlookup-reporter.js`, `jest-reporter.js`, `mocha-reporter.js`, each with type declarations). The package's `exports` map only declares `import` conditions, so `require('testlookup-reporter/...')` is refused by Node; the examples below therefore point at the files under `node_modules/testlookup-reporter/dist/` by path, which works for both test runners.

## Credentials

The JavaScript reporters authenticate with a **JWT access token** only, sent as `Authorization: Bearer <token>`; there is no API-key option. Get a token from `POST /api/v1/auth/login` (or `TestLookupReporter.login()`, below) at the start of the CI job. Access tokens last 12 hours by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` on the server). Use a dedicated user with the QA_ENGINEER role or higher on the target project.

## Jest

```js
// jest.config.js
module.exports = {
  reporters: [
    'default',
    ['<rootDir>/node_modules/testlookup-reporter/dist/jest-reporter.js', {
      baseUrl:     process.env.TESTLOOKUP_URL,
      token:       process.env.TESTLOOKUP_TOKEN,
      projectId:   process.env.TESTLOOKUP_PROJECT_ID,
      buildNumber: process.env.TESTLOOKUP_BUILD,
      branch:      process.env.TESTLOOKUP_BRANCH,
    }],
  ],
}
```

Options (each falls back to the environment variable shown): `baseUrl` (`TESTLOOKUP_URL`), `token` (`TESTLOOKUP_TOKEN`), `projectId` (`TESTLOOKUP_PROJECT_ID`), `buildNumber` (`TESTLOOKUP_BUILD`, default `jest-<epoch ms>`), `branch` (`TESTLOOKUP_BRANCH`), `commitHash` (`TESTLOOKUP_COMMIT`), `framework` (default `jest`), `batchSize`, `batchIntervalMs`. If the URL, token or project is missing, the reporter logs a warning and stays inactive; your tests still run.

Each test is recorded under its full name, with the `describe` path (`Outer > Inner`) as the suite. `passed` maps to `PASSED`, `failed` to `FAILED` (with the failure message and stack), and `skipped`, `pending`, `todo` and `disabled` to `SKIPPED`. The session is closed when the run completes.

## Mocha

Mocha calls a reporter module as a constructor, and the compiled reporter is the module's `default` export, so register it through a one-line shim:

```js
// testlookup-mocha-reporter.cjs (in your project)
module.exports = require('./node_modules/testlookup-reporter/dist/mocha-reporter.js').default
```

```js
// .mocharc.cjs
module.exports = {
  reporter: './testlookup-mocha-reporter.cjs',
  reporterOptions: {
    testlookupUrl:     process.env.TESTLOOKUP_URL,
    testlookupToken:   process.env.TESTLOOKUP_TOKEN,
    testlookupProject: process.env.TESTLOOKUP_PROJECT_ID,
  },
}
```

or on the command line: `mocha --reporter ./testlookup-mocha-reporter.cjs --reporter-option testlookupProject=<project UUID>`.

Reporter options (each falls back to the environment variable shown): `testlookupUrl` (`TESTLOOKUP_URL`), `testlookupToken` (`TESTLOOKUP_TOKEN`), `testlookupProject` (`TESTLOOKUP_PROJECT_ID`), `testlookupBuild` (`TESTLOOKUP_BUILD`, default `mocha-<epoch ms>`), `testlookupBranch` (`TESTLOOKUP_BRANCH`), `testlookupCommit` (`TESTLOOKUP_COMMIT`). Passing, failing and pending tests are recorded as `PASSED`, `FAILED` and `SKIPPED`, with the parent suite's full title as the suite. Mocha runs one reporter at a time, so this reporter replaces the console output for that run.

## Programmatic client

```ts
import { TestLookupReporter } from 'testlookup-reporter'   // ESM / TypeScript

const token = await TestLookupReporter.login('https://testlookup.example.com', 'ci-bot', process.env.TESTLOOKUP_PASSWORD!)
const reporter = new TestLookupReporter({
  baseUrl: 'https://testlookup.example.com',
  token,
  projectId: '<project UUID or name>',
  framework: 'vitest',
})

const session = await reporter.startSession({ buildNumber: 'build-42', branch: 'main', launchName: 'Smoke' })
await session.record('login works', 'PASSED', 120)
await session.record('checkout works', 'FAILED', 340, { error: 'expected 200, got 500', suiteName: 'checkout' })
await session.close()                              // flush buffered events
await reporter.closeSession(session.sessionId)     // finalise the run on the server
```

- `ReporterConfig`: `baseUrl`, `token`, `projectId` (required); `clientName` (default the host name), `framework` (default `javascript`), `suiteName`, `releaseName` (blank: the project's default release), `batchSize` (default 50), `batchIntervalMs` (default 100), `timeoutMs` (default 30000). `verifySsl` is declared but has no effect; to trust a private CA, start Node with `NODE_EXTRA_CA_CERTS=/path/to/ca.pem`.
- `startSession(options)`: `buildNumber`, `runId`, `branch`, `commitHash`, `totalTests`, `machineId`, `launchName`, `suiteName`, `releaseName`, `metadata`, and the CI-context fields below. Calls `POST /api/v1/stream/sessions`.
- `LiveSession`: `record(testName, status, durationMs, { suiteName, className, error, stackTrace, tags, metadata })` with status `PASSED`, `FAILED`, `SKIPPED` or `BROKEN`; `log(message, level)`; `metric(name, value, unit)`; `close()`; `stats`.
- Events are batched (50 events or 100 ms; at most 1,000 per request) and retried up to 5 times with back-off; each batch keeps its client-generated `batch_id` across retries (see [Replay-safe live identity](../README.md#replay-safe-live-identity)).

## Configuration

The Jest and Mocha reporters read only their options and the environment variables listed above (`TESTLOOKUP_URL`, `TESTLOOKUP_TOKEN`, `TESTLOOKUP_PROJECT_ID`, `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT`). Unlike the Python and Java reporters, they do not read `testlookup.properties`, `testlookup.yaml`, `TESTLOOKUP_ENDPOINT` or `TESTLOOKUP_API_KEY`. (`src/config-loader.ts` contains a file loader, but nothing in the package uses it.)

## CI context

`startSession` detects the CI provider, repository, pull/merge request number, actor and run URL from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`src/ci-context.ts`). `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL` override detection, and the `ciProvider`, `ciRepo`, `prNumber`, `ciActor` and `ciRunUrl` session options override both. The JavaScript reporter does not collect a commit range.

## Known limitations

Today's behaviour, to be removed from this list as each is fixed:

- Authentication is a **JWT access token** only; there is no API-key option.
- `package.json` `exports` lists only `import` conditions while the build
  emits CommonJS, so `require('testlookup-reporter/jest-reporter')` and the
  Mocha equivalent can fail with `ERR_PACKAGE_PATH_NOT_EXPORTED`. Reference the
  reporters **by file path** (`.../dist/jest-reporter.js`,
  `.../dist/mocha-reporter.js`) as shown above.
- The Mocha reporter is the module's `default` export, hence the one-line
  shim.
- The Jest reporter implements both `onTestCaseResult` and `onTestResult`;
  on Jest 27.4 and later a test can be recorded twice.
- `verifySsl` is accepted but not used, and `src/config-loader.ts` is not
  wired in: configuration comes only from options and environment variables.

## License

Apache-2.0, like the rest of the repository.
