# TestLookup reporter for Java

Streams Java test results to a TestLookup server while the tests run, through a **TestNG listener** or a **JUnit 5 extension**, or programmatically. Results appear on **Runs → Live** (`/live`) during the run and on Runs, Failures and Trends when the session closes.

Other reporters and the shared concepts (credentials, live identity) are in [client/README.md](../README.md). Runnable Maven projects are in [client/examples/java](../examples/README.md).

## Build and install (from source)

The artifact is not published to Maven Central. Build it from a clone (Java 11 or newer, Maven 3):

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
mvn -f client/java/pom.xml install      # add -DskipTests to skip the SDK's own tests
```

This installs `io.testlookup:testlookup-reporter:1.0.0` into your local Maven repository (`~/.m2`) and also leaves two jars in `client/java/target/`:

| Jar | Contents | Use it when |
|---|---|---|
| `testlookup-reporter-1.0.0.jar` | The reporter only; Jackson comes in as normal dependencies | You build with Maven or Gradle |
| `testlookup-reporter-1.0.0-all.jar` | The reporter with Jackson and SnakeYAML bundled and relocated to `ai.testlookup.shaded.jackson` | You add a jar to a classpath by hand, or must avoid Jackson version clashes |

Neither jar contains TestNG or JUnit; your project provides them (TestNG 7.x, JUnit Jupiter 5.x).

**Maven:**

```xml
<dependency>
  <groupId>io.testlookup</groupId>
  <artifactId>testlookup-reporter</artifactId>
  <version>1.0.0</version>
  <scope>test</scope>
</dependency>
```

**Gradle:** add `mavenLocal()` to `repositories` and `testImplementation 'io.testlookup:testlookup-reporter:1.0.0'`.

A running TestLookup server can also hand out the SDK at `GET /api/v1/sdk/java`: the `-all` jar when the server image contains a built one, otherwise a ZIP of these sources (images built by the project's CI contain the sources only).

## Credentials

The Java reporter takes either an **API key** (sent as `X-API-Key`) or a **JWT access token** (sent as `Authorization: Bearer`). When both are configured the API key is used. Use a key whose owner has the QA_ENGINEER role or higher on the project; a project-scoped streaming key from **Settings → Streaming API keys** works, and that page's dialog offers a ready-made `testlookup.properties` block. Keep keys out of committed files: pass them as environment variables or JVM properties in CI.

## TestNG

The jar registers `io.testlookup.testng.TestLookupListener` through `META-INF/services/org.testng.ITestNGListener`, so TestNG picks it up automatically once the jar is on the test classpath. You can also declare it explicitly:

```xml
<suite name="Checkout">
  <listeners>
    <listener class-name="io.testlookup.testng.TestLookupListener"/>
  </listeners>
  <test name="API">
    <classes><class name="com.example.CheckoutTest"/></classes>
  </test>
</suite>
```

The listener opens one session per TestNG suite, records `PASSED`, `FAILED` and `SKIPPED` results (a failure within the success percentage, or a timeout, is recorded as `BROKEN`), and closes the session when the suite finishes. It does nothing when no configuration is found, so the jar is safe to leave on the classpath.

The listener first looks for these names as `<parameter>` values in `testng.xml`, then as JVM system properties, then as environment variables, and finally lets the configuration chain below fill the gaps:

| Suite parameter / `-D` property | Environment variable | Meaning |
|---|---|---|
| `testlookup.url` | `TESTLOOKUP_URL` | Server URL |
| `testlookup.apiKey` | `TESTLOOKUP_API_KEY` | API key |
| `testlookup.token` | `TESTLOOKUP_TOKEN` | JWT access token |
| `testlookup.projectId` | `TESTLOOKUP_PROJECT_ID` | Project UUID or name |
| `testlookup.build` | `TESTLOOKUP_BUILD` | Build number (default `testng-<epoch ms>`) |
| `testlookup.branch`, `testlookup.commit` | `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT` | Git metadata |
| `testlookup.launch` | `TESTLOOKUP_LAUNCH` | Launch label |
| `testlookup.suite` | `TESTLOOKUP_SUITE` | Run-level suite (else `testlookup.launch`, else the TestNG `<suite name>`) |

## JUnit 5

Annotate a test class:

```java
@ExtendWith(io.testlookup.junit5.TestLookupExtension.class)
class CheckoutTest {
    @Test void paysWithCard() { /* ... */ }
}
```

or register the extension for every class. The jar ships `META-INF/services/org.junit.jupiter.api.extension.Extension`, which JUnit loads only when automatic extension registration is on; add this to `src/test/resources/junit-platform.properties`:

```properties
junit.jupiter.extensions.autodetection.enabled=true
```

The extension opens **one session per top-level test class** (so each class becomes its own run), records each test under its display name with the class as the suite, and maps successful to `PASSED`, failed to `FAILED` (with the exception), aborted to `BROKEN` and disabled to `SKIPPED`. It does nothing when no configuration is found.

## Programmatic use

```java
import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.LiveSession;
import io.testlookup.TestLookupReporter.SessionOptions;
import io.testlookup.TestLookupReporter.TestStatus;

TestLookupReporter reporter = new TestLookupReporter.Builder()
    .baseUrl("https://testlookup.example.com")
    .apiKey(System.getenv("TESTLOOKUP_API_KEY"))
    .projectId("<project UUID or name>")
    .framework("custom")
    .build();                                   // unset values come from the configuration chain

try (LiveSession session = reporter.startSession(
        SessionOptions.builder().buildNumber("build-42").branch("main").build())) {
    session.record("login works", TestStatus.PASSED, 120);
}                                               // close() flushes and finalises the run
```

- `Builder`: `baseUrl`, `apiKey` or `token`, `projectId` (required; `build()` throws `IllegalStateException` when they cannot be resolved), `clientName`, `framework`, `suiteName`, `releaseName`, `batchSize`, `batchIntervalMs`. `TestLookupReporter.login(baseUrl, username, password)` returns a JWT access token.
- `SessionOptions.builder()`: `buildNumber`, `branch`, `commitHash`, `machineId`, `totalTests`, `launchName`, `suiteName`, `releaseName`, and the CI-context fields below.
- `LiveSession`: `record(testName, status, durationMs[, RecordOptions])` with `RecordOptions.builder()` (`suiteName`, `className`, `error`, `stackTrace`, `tags`, `metadata`); `log(message, level)`; `metric(name, value, unit)`; `close()`. `TestStatus` is `PASSED`, `FAILED`, `SKIPPED` or `BROKEN`.
- Events are batched and retried with back-off; each batch keeps its client-generated `batch_id` across retries (see [Replay-safe live identity](../README.md#replay-safe-live-identity)).

## Configuration chain

Lowest precedence first; a later source overrides an earlier one:

1. A `testlookup.properties` on the **test classpath** (for example `src/test/resources/testlookup.properties`).
2. The first file found on disk: `./testlookup.properties`, `./.testlookup/testlookup.properties`, `./testlookup.yaml`, `./.testlookup/config.yaml`, `~/.testlookup/testlookup.properties`, `~/.testlookup/config.yaml`. YAML files use the legacy nested schema (see [client/testlookup.yaml.example](../testlookup.yaml.example)).
3. Environment variables.
4. JVM system properties (`-Dtestlookup.*`).
5. Values set on the `Builder` (or, for the TestNG listener, its suite parameters).

| Properties key | Environment variable | `-D` property |
|---|---|---|
| `testlookup.endpoint` (alias `testlookup.url`) | `TESTLOOKUP_ENDPOINT` or `TESTLOOKUP_URL` (`TESTLOOKUP_ENDPOINT` wins) | `testlookup.endpoint`, `testlookup.url` |
| `testlookup.api.key` (aliases `testlookup.api_key`, `testlookup.apiKey`) | `TESTLOOKUP_API_KEY` | `testlookup.api.key`, `testlookup.apiKey`, `testlookup.api-key` |
| `testlookup.token` | `TESTLOOKUP_TOKEN` | `testlookup.token` |
| `testlookup.project` | `TESTLOOKUP_PROJECT` or `TESTLOOKUP_PROJECT_ID` | `testlookup.project`, `testlookup.projectId` |
| `testlookup.launch`, `testlookup.suite`, `testlookup.release` | `TESTLOOKUP_LAUNCH`, `TESTLOOKUP_SUITE`, `TESTLOOKUP_RELEASE` | same names |
| `testlookup.build`, `testlookup.branch`, `testlookup.commit` | `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT` | same names |
| `testlookup.framework` | `TESTLOOKUP_FRAMEWORK` | `testlookup.framework` |

```properties
# src/test/resources/testlookup.properties
testlookup.endpoint=https://testlookup.example.com
testlookup.project=<project UUID or name>
testlookup.launch=Smoke suite
# testlookup.api.key=  (set TESTLOOKUP_API_KEY in CI instead of committing a key)
```

In CI, override per-build values without editing the file:

```bash
mvn test -Dtestlookup.build="$BUILD_NUMBER" -Dtestlookup.branch="$GIT_BRANCH" -Dtestlookup.commit="$GIT_COMMIT"
```

## CI context

Sessions carry the CI provider, repository, pull/merge request number, actor and run URL, detected from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`CiContext.java`). `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL` override detection, and the `ciProvider`, `ciRepo`, `prNumber`, `ciActor` and `ciRunUrl` session options override both. The Java reporter does not collect a commit range.

## Known limitations

Today's behaviour, to be removed from this list as each is fixed:

- The JUnit 5 extension records every test with a duration of 0.
- The TestNG listener and the JUnit 5 extension take the build, branch,
  commit and launch label only from JVM properties, environment variables
  (and, for TestNG, suite parameters); those keys in `testlookup.properties`
  are not used for the session.
- `TESTLOOKUP_UPLOAD_MODE` is read but changes nothing, and there is no
  CA-certificate or insecure-TLS option: import a private CA into the JVM
  trust store instead.

## License

Apache-2.0, like the rest of the repository. The `-all` jar also carries the Apache-2.0 notices of the bundled Jackson and SnakeYAML libraries.
