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

Neither jar contains TestNG, JUnit or RestAssured; your project provides them (TestNG 7.x, JUnit Jupiter 5.x or 6.x, RestAssured 5.x for [HTTP capture](#capturing-http-exchanges-restassured)).

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

The Java reporter takes either an **API key** (sent as `X-API-Key`) or a **JWT access token** (sent as `Authorization: Bearer`); a value that starts with `qai_` is sent as an API key even when given as the token. The two resolve as a pair: one set on the `Builder` (or as a TestNG suite parameter) wins; otherwise the first source holding either one wins, in the order JVM properties, environment variables, configuration files; within one source the API key wins. Use a key whose owner has the QA_ENGINEER role or higher on the project; a project-scoped streaming key from **Settings → Streaming API keys** works, and that page's dialog offers a ready-made `testlookup.properties` block. Keep keys out of committed files: pass them as environment variables or JVM properties in CI.

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

The listener first looks for these names as `<parameter>` values in `testng.xml`, then resolves the rest through the [configuration chain](#configuration-chain): JVM system properties, then environment variables, then `testlookup.properties` / `testlookup.yaml` (the file keys are `testlookup.build`, `testlookup.branch`, `testlookup.commit`, `testlookup.launch`, `testlookup.suite` and so on):

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

The extension works with JUnit 5 (Java 11 or newer) and JUnit 6 (Java 17 or newer, as JUnit 6 itself requires); the SDK's own tests run against both.

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

The extension opens **one session for the whole test run**, however many classes use it, and closes it when JUnit finishes the run. It records each test under its display name with its measured duration and the class as the suite, and maps successful to `PASSED`, failed to `FAILED` (with the exception), aborted to `BROKEN` and disabled to `SKIPPED`. Server, credentials, project, build (default `junit5-<epoch ms>`), branch, commit, launch, suite and release all come from the [configuration chain](#configuration-chain). It does nothing when no configuration is found.

## Capturing HTTP exchanges (RestAssured)

The reporter can send the RestAssured calls each test makes beside its result. The test's page then shows an **HTTP exchanges** tab, and the deep investigation can compare response fields with earlier runs.

1. Add RestAssured 5.x to your own test dependencies (the SDK is built against 5.5.7). The SDK declares it `provided` and optional, so nothing reaches your classpath from the SDK, and the `-all` jar does not contain it. Without RestAssured the reporter works as before.
2. Register the filter, for every request or for one:

```java
import io.restassured.RestAssured;
import io.testlookup.restassured.TestLookupRestAssuredFilter;

RestAssured.filters(new TestLookupRestAssuredFilter());                // every request, e.g. in a @BeforeAll

given().filter(new TestLookupRestAssuredFilter()).get("/api/orders");  // one request
```

Registering the filter turns capture on. `TESTLOOKUP_CAPTURE_HTTP` set to `0`, `false`, `no` or `off` turns it off without a code change (`1`, `true`, `yes` or `on` keeps it on). When that variable is not set, `-Dtestlookup.capture_http=false` turns it off too, as does `testlookup.capture_http=false` in `testlookup.properties` or `reporting.capture_http: false` in a YAML configuration file.

- **What is captured:** the calls a test method makes while the JUnit 5 extension or the TestNG listener reports the run: method, full URL, status, duration, start time, both header sets (a repeated header is one value, joined with `, `) and text bodies (no content type, JSON, `text/*`, XML, form, JavaScript, GraphQL, YAML, CSV, HTML). A binary body keeps its headers but not its body; a file, stream or multipart request body is not read. With JUnit 5 the calls of the test's `@AfterEach` methods are included. Calls in `@BeforeEach`, `@BeforeAll`, `@AfterAll`, TestNG's `@BeforeMethod` / `@AfterMethod`, or another thread are not captured. A call that fails (for example, connection refused) is captured with its exception's class name, and the exception reaches your test unchanged.
- **Caps:** at most 20 exchanges per test; the rest are counted, not sent. A body is read up to 64 KiB; the server stores at most 16 KiB of it.
- **Redacted before it leaves your machine**, and again on the server, with the same rules as the Python SDK (`sdk-vectors/http_exchange.json` is the shared specification): the values of `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`, `X-Auth-Token`, `X-Amz-Security-Token` and other credential headers (and of any header whose name ends in `token`, `secret`, `password`, `api-key`, `signature` or `credential`); query values of `token`, `access_token`, `api_key`, `key`, `code`, `password`, `secret`, `sig`, `signature` and similar; the password in `user:password@`; JSON values under keys like `password`, `client_secret`, `apiKey`, `*_token`; and, anywhere, bearer tokens, JWTs, provider tokens (`ghp_…`, `glpat-…`, `xoxb-…`, `sk-…`, `AKIA…`, any unbroken 40+ character run), e-mail addresses and phone / card / IPv4 shapes become `[REDACTED]`. Review a captured run of your suite before turning capture on in CI.
- **Never a test failure:** the filter never changes a call or its response. Each test that captured anything sends one `POST /api/v1/stream/http-exchanges` after its result, from the reporter's background thread. A refusal or a network error is logged once per session with the status code only, and counted (`LiveSession.getHttpExchangesFailedCount()`); your results are unaffected.
- **Dependency scanning:** this repository's dependency scan (Trivy, in the `security-deps` CI job) reads `client/java/pom.xml`, RestAssured's tree included. A new CVE in RestAssured or one of its dependencies (Groovy, Apache HttpClient, commons-codec and others) can fail that job until `rest-assured.version` moves. Your own project scans the RestAssured version you chose.

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

- `Builder`: `baseUrl`, `apiKey` or `token`, `projectId` (required; `build()` throws `IllegalStateException` when they cannot be resolved), `clientName`, `framework`, `suiteName`, `releaseName`, `batchSize`, `batchIntervalMs`, `caCertPath`, `insecure` (see [TLS](#tls)). `TestLookupReporter.login(baseUrl, username, password)` returns a JWT access token.
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
| `testlookup.ca_cert_path` | `TESTLOOKUP_CA_CERT` | `testlookup.ca_cert_path`, `testlookup.caCertPath` |
| `testlookup.insecure` | `TESTLOOKUP_INSECURE` | `testlookup.insecure` |

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

## TLS

For a server whose certificate a private or self-signed CA issued, point the reporter at that CA's PEM file with `testlookup.ca_cert_path` / `TESTLOOKUP_CA_CERT` / `Builder.caCertPath(...)`: the reporter then trusts only the certificates in that file and still checks the host name. `testlookup.insecure=true` / `TESTLOOKUP_INSECURE=true` / `Builder.insecure(true)` skips certificate and host-name verification altogether and logs a warning; use it only for local experiments. A CA path wins over `insecure`.

## CI context

Sessions carry the CI provider, repository, pull/merge request number, actor and run URL, detected from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`CiContext.java`). `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL` override detection, and the `ciProvider`, `ciRepo`, `prNumber`, `ciActor` and `ciRunUrl` session options override both. The Java reporter does not collect a commit range.

## License

Apache-2.0, like the rest of the repository. The `-all` jar also carries the Apache-2.0 notices of the bundled Jackson and SnakeYAML libraries.
