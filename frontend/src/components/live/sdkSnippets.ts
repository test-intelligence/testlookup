/**
 * Code snippets for the /live "Connect a test runner" panel.
 *
 * Every Go and JavaScript identifier shown here must exist in the SDK the
 * same panel downloads (client/go, client/js). The JS and Go snippets used to
 * call `RunWithReporter`, `NewReporter`, `CreateSession`, `createSession` and
 * `WithRelease`, none of which the SDKs export, told users to `npm install` /
 * `go get` packages that are not published, and set `TESTLOOKUP_PROJECT`,
 * which the Go client does not read (it reads `TESTLOOKUP_PROJECT_ID`).
 *
 * `sdkSnippets.test.ts` checks each identifier against `sdkSymbols.json`,
 * which `scripts/gen_sdk_symbols.py` generates from the SDK sources; the
 * quality gate `frontend.sdk-symbols-fresh` fails when that file is stale.
 * After changing a snippet, run the test; after changing an SDK, rerun the
 * generator.
 */

export type SDKLang = 'python' | 'java' | 'javascript' | 'go'

export const SDK_TABS: { id: SDKLang; label: string; icon: string }[] = [
  { id: 'python', label: 'Python', icon: 'py' },
  { id: 'java', label: 'Java', icon: 'java' },
  { id: 'javascript', label: 'JavaScript', icon: 'js' },
  { id: 'go', label: 'Go', icon: 'go' },
]

/** File name `npm pack` writes for client/js (its package.json name + version). */
export const JS_SDK_TARBALL = 'testlookup-reporter-1.0.0.tgz'

export function getInstallSnippet(lang: SDKLang): string {
  switch (lang) {
    case 'python':
      return `# Download and extract the Python ZIP from SDK Downloads above
cd python
pip install ".[yaml]"
# Then add testlookup.yaml to your project root (see Step 2)`
    case 'java':
      return `# Option 1: Download the fat JAR from the SDK Downloads button above
# Add to classpath — auto-discovery registers JUnit 5 / TestNG listeners

# Option 2: Install to local Maven repo, then add dependency:
mvn install:install-file -Dfile=testlookup-reporter-1.0.0-all.jar \\
  -DgroupId=io.testlookup -DartifactId=testlookup-reporter \\
  -Dversion=1.0.0 -Dclassifier=all -Dpackaging=jar

# Maven dependency (after local install)
<dependency>
  <groupId>io.testlookup</groupId>
  <artifactId>testlookup-reporter</artifactId>
  <version>1.0.0</version>
  <classifier>all</classifier>
  <scope>test</scope>
</dependency>

# Gradle (after local install)
testImplementation 'io.testlookup:testlookup-reporter:1.0.0:all'`
    case 'javascript':
      return `# Download the JavaScript/TS ZIP from SDK Downloads above and
# extract it (it unpacks to js/). The package is not on npm:
# build it once and pack it,
cd js
npm ci
npm run build
npm pack            # writes ${JS_SDK_TARBALL}
cd ..

# then add the packed file to your project:
npm install --save-dev ./js/${JS_SDK_TARBALL}`
    case 'go':
      return `# Download the Go ZIP from SDK Downloads above and extract it
# into your module (it unpacks to go/). The module is not published,
# so point go.mod at the extracted folder:
go mod edit \\
  -require=github.com/testlookup/testlookup-go@v0.0.0 \\
  -replace=github.com/testlookup/testlookup-go=./go

# The same, as go.mod lines:
# require github.com/testlookup/testlookup-go v0.0.0
# replace github.com/testlookup/testlookup-go => ./go`
  }
}

/**
 * W18 DEV-03: the live adapters per test runner. JavaScript and Python offer a runner picker; the
 * other languages have one runner snippet. Every option key below is a ``LiveAdapterOptions`` field
 * (client/js/src/live-adapter.ts) or the listener's argument (client/testlookup_robot_listener.py).
 */
export interface RunnerOption {
  id: string
  label: string
}

export const RUNNERS: Partial<Record<SDKLang, RunnerOption[]>> = {
  python: [
    { id: 'pytest', label: 'pytest' },
    { id: 'robot', label: 'Robot Framework' },
  ],
  javascript: [
    { id: 'jest-mocha', label: 'Jest / Mocha' },
    { id: 'playwright', label: 'Playwright' },
    { id: 'vitest', label: 'Vitest' },
    { id: 'cypress', label: 'Cypress' },
    { id: 'cucumber', label: 'Cucumber' },
  ],
}

export function runnerStepLabel(lang: SDKLang, runner?: string): string {
  switch (runner) {
    case 'robot': return 'Run robot with the listener'
    case 'playwright': return 'Add the Playwright reporter'
    case 'vitest': return 'Add the Vitest reporter'
    case 'cypress': return 'Register the Cypress plugin (results stream per spec)'
    case 'cucumber': return 'Run cucumber-js with the formatter'
  }
  return lang === 'python' ? 'Run pytest with the plugin'
    : lang === 'java' ? 'Configure TestNG XML or env vars (zero-code)'
      : lang === 'javascript' ? 'Use the Jest or Mocha reporter' : 'Use the testing helper'
}

function adapterRunnerSnippet(runner: string, projectId: string, serverUrl: string): string | null {
  const opts = [
    `testlookupUrl: '${serverUrl}',`,
    `testlookupProject: '${projectId}',`,
    'testlookupApiKey: process.env.TESTLOOKUP_API_KEY,',
  ].join('
')
  switch (runner) {
    case 'playwright':
      return `// playwright.config.ts — one result per attempt (retries show as attempt 2),
// one suite per Playwright project
export default defineConfig({
  retries: 1,
  reporter: [
    ['list'],
    ['testlookup-reporter/playwright-reporter', {
      ${opts.replace(/\n/g, '\n      ')}
    }],
  ],
})`
    case 'vitest':
      return `// vitest.config.ts — each test streams as it finishes
export default defineConfig({
  test: {
    reporters: ['default', ['testlookup-reporter/vitest-reporter', {
      ${opts.replace(/\n/g, '\n      ')}
    }]],
  },
})`
    case 'cypress':
      return `// cypress.config.ts — Cypress reports per spec, so a spec's
// tests appear together when it finishes (needs \`cypress run\`)
import testlookup from 'testlookup-reporter/cypress-plugin'

export default defineConfig({
  e2e: {
    setupNodeEvents(on, config) {
      return testlookup(on, config, {
        ${opts.replace(/\n/g, '\n        ')}
      })
    },
  },
})`
    case 'cucumber':
      return `# Cucumber JS — one result per scenario, the feature as its suite
export TESTLOOKUP_API_KEY=<api-key>
npx cucumber-js \\
  --format testlookup-reporter/cucumber-formatter \\
  --format-options '{"testlookupUrl":"${serverUrl}","testlookupProject":"${projectId}"}'`
    case 'robot':
      return `# Robot Framework — the listener ships in the Python SDK ZIP
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
robot --listener testlookup_robot_listener.TestLookupListener tests/

# or pass the settings to the listener
robot --listener "testlookup_robot_listener.TestLookupListener:project=${projectId}:url=${serverUrl}" tests/`
  }
  return null
}

export function getRunnerSnippet(lang: SDKLang, projectId: string, serverUrl: string, runner?: string): string {
  const adapter = runner ? adapterRunnerSnippet(runner, projectId, serverUrl) : null
  if (adapter) return adapter
  switch (lang) {
    case 'python':
      return `# Option 1: Zero-config — add testlookup.yaml to project root:
# server:
#   url: "${serverUrl}"
# auth:
#   api_key: "<api-key>"       # or token: "<jwt>"
# project:
#   id: "${projectId}"
pytest

# Option 2: CLI flags
pytest --testlookup-url ${serverUrl} \\
       --testlookup-token <jwt-or-api-key> \\
       --testlookup-project ${projectId} \\
       --testlookup-build build-42

# Option 3: Environment variables
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
pytest`
    case 'java':
      return `# Option 1: TestNG XML — zero-code setup (recommended)
# Just add suite parameters to your testng.xml:

# <suite name="My Suite">
#   <parameter name="testlookup.url" value="${serverUrl}"/>
#   <parameter name="testlookup.apiKey" value="qai_..."/>
#   <parameter name="testlookup.projectId" value="${projectId}"/>
#   <listeners>
#     <listener class-name="io.testlookup.testng.TestLookupListener"/>
#   </listeners>
#   <test name="Regression">
#     <classes><class name="com.example.MyTest"/></classes>
#   </test>
# </suite>
mvn test -DsuiteXmlFiles=testng.xml

# Option 2: Environment variables (works with JUnit 5 too)
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
mvn test

# Option 3: JVM system properties
mvn test \\
  -Dtestlookup.url=${serverUrl} \\
  -Dtestlookup.apiKey=<api-key> \\
  -Dtestlookup.projectId=${projectId} \\
  -Dtestlookup.build=build-42

# Option 4: testlookup.yaml in project root (same format as Python)`
    case 'javascript':
      return `// Jest — add to jest.config.js
module.exports = {
  reporters: [
    'default',
    ['testlookup-reporter/jest-reporter', {
      baseUrl: '${serverUrl}',
      apiKey: process.env.TESTLOOKUP_API_KEY, // or token: '<jwt>'
      projectId: '${projectId}',
    }],
  ],
}

// Mocha — run with the reporter and its options
mocha --reporter testlookup-reporter/mocha-reporter \\
  --reporter-option testlookupUrl=${serverUrl} \\
  --reporter-option testlookupProject=${projectId} \\
  --reporter-option testlookupApiKey=<api-key>`
    case 'go':
      return `# Read by testlookup.NewFromEnv (the Go client takes a JWT,
# not an API key)
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_TOKEN=<jwt>
export TESTLOOKUP_PROJECT_ID=${projectId}

// main_test.go — one live session per go test run
package mypkg_test

import (
    "context"
    "os"
    "testing"
    "time"

    "github.com/testlookup/testlookup-go/testlookup"
)

var tr *testlookup.TestReporter

func TestMain(m *testing.M) {
    reporter, err := testlookup.NewFromEnv()
    if err != nil {
        os.Exit(m.Run()) // not configured: run without reporting
    }
    session, err := reporter.StartSession(context.Background(), testlookup.SessionOptionsFromEnv())
    if err != nil {
        os.Exit(m.Run())
    }
    tr = testlookup.NewTestReporter(session, "my/pkg")
    code := m.Run()
    tr.Flush(context.Background()) // flush and close the session
    os.Exit(code)
}

func TestLogin(t *testing.T) {
    if tr != nil {
        defer tr.RecordTest(t, t.Name(), time.Now())
    }
    // ... test body ...
}`
  }
}

export function getAPISnippet(lang: SDKLang, projectId: string, serverUrl: string): string {
  switch (lang) {
    case 'python':
      return `from testlookup_reporter import TestLookupReporter

# Config auto-resolved from testlookup.yaml / env vars:
reporter = TestLookupReporter()

# Or pass explicitly with API key (recommended for CI/CD):
# reporter = TestLookupReporter(
#     base_url="${serverUrl}",
#     api_key="qai_...",               # project-scoped API key
#     project_id="${projectId}",
# )

async with reporter.session(build_number="build-42", branch="main") as s:
    await s.record("test_login", "PASSED", 120)
    await s.record("test_cart",  "FAILED", 340,
                   error="AssertionError: expected 200",
                   suite_name="checkout_tests",
                   tags=["smoke"])
    await s.log("Environment: staging")
    await s.metric("memory_mb", 512.3, unit="MB")
await reporter.aclose()`
    case 'java':
      return `import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;
import java.util.List;

// Config auto-resolved from testlookup.yaml / env vars / system props:
TestLookupReporter reporter = new TestLookupReporter.Builder().build();

// Or pass explicitly with API key (recommended for CI/CD):
// TestLookupReporter reporter = new TestLookupReporter.Builder()
//     .baseUrl("${serverUrl}")
//     .apiKey("qai_...")               // project-scoped API key
//     .projectId("${projectId}")
//     .build();

try (LiveSession session = reporter.startSession(
        SessionOptions.builder()
            .buildNumber("build-42")
            .branch("main")
            .build())) {

    session.record("test_login", TestStatus.PASSED, 120);
    session.record("test_cart",  TestStatus.FAILED, 340,
        RecordOptions.builder()
            .error("AssertionError: expected 200")
            .suiteName("CheckoutTests")
            .tags(List.of("smoke"))
            .build());
    session.log("Environment: staging", "INFO");
    session.metric("memory_mb", 512.3, "MB");
}
// session.close() called automatically — triggers AI analysis`
    case 'javascript':
      return `import { TestLookupReporter } from 'testlookup-reporter'

const reporter = new TestLookupReporter({
  baseUrl: '${serverUrl}',
  apiKey: process.env.TESTLOOKUP_API_KEY, // or token: '<jwt>'
  projectId: '${projectId}',
})

const session = await reporter.startSession({
  buildNumber: 'build-42',
  releaseName: 'v2.5.0',
})

await session.record('test_login', 'PASSED', 120)
await session.record('test_cart', 'FAILED', 340, {
  error: 'AssertionError: expected 200',
})

await session.close() // flush buffered events
await reporter.closeSession(session.sessionId) // finish the run on the server`
    case 'go':
      return `package main

import (
    "context"
    "log"

    "github.com/testlookup/testlookup-go/testlookup"
)

func main() {
    reporter, err := testlookup.New(testlookup.Config{
        BaseURL:   "${serverUrl}",
        Token:     "<jwt>",
        ProjectID: "${projectId}",
    })
    if err != nil {
        log.Fatal(err)
    }

    ctx := context.Background()
    session, err := reporter.StartSession(ctx, testlookup.SessionOptions{
        BuildNumber: "build-42",
        ReleaseName: "v2.5.0",
    })
    if err != nil {
        log.Fatal(err)
    }
    defer session.Close(ctx) // flush, then close the session on the server

    session.Record(ctx, "test_login", testlookup.Passed, 120, testlookup.RecordOptions{})
    session.Record(ctx, "test_cart", testlookup.Failed, 340, testlookup.RecordOptions{
        Error: "AssertionError: expected 200",
    })
}`
  }
}
