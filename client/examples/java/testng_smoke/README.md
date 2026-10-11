# TestNG sample

Minimal Maven + TestNG project that streams to TestLookup live.

## What's in here

```
testng_smoke/
├── pom.xml                                          ← Maven build
├── src/test/resources/
│   ├── testng.xml                                   ← <listener> registered here
│   └── testlookup.properties                        ← config (classpath-discovered)
└── src/test/java/com/example/
    ├── SmokeTests.java                              ← TestNG tests; no TestLookup imports
    └── AuthTests.java
```

## Run it

1. **Install the reporter** into your local Maven repository (it is not on
   Maven Central). From the repository root:

   ```bash
   mvn -f client/java/pom.xml install -DskipTests
   ```

2. **Generate an API key.** As an ADMIN, TestLookup → *Settings → Streaming
   API keys*. Copy the `testlookup.properties` content from the dialog that
   shows the new key and replace the placeholder file at
   `src/test/resources/testlookup.properties`.

3. **Build and test:**

   ```bash
   mvn test
   ```

   Surefire picks up `testng.xml` (configured in `pom.xml`), TestNG reads the
   `<listener>` entry, and `TestLookupListener` reads
   `/testlookup.properties` from the test classpath.

## What you'll see

- **TestLookup → Runs → Live** — the run appears immediately, with the
  suite name `Smoke suite` (the listener uses `testlookup.launch` from the
  properties file as the run's suite; a launch label comes only from
  `-Dtestlookup.launch` or `TESTLOOKUP_LAUNCH`, see below). Counters tick up
  live as the data-provider rows stream in.
- **Runs / Overview / Coverage / Failures / Trends** — once the suite
  finishes, the listener's `onFinish(ISuite)` closes the session and the
  run lands on every other page within a few seconds.

## CI wiring

Inject CI metadata as JVM system properties — they take precedence over the
properties file without needing a per-build edit:

```bash
mvn test \
  -Dtestlookup.build=$BUILD_NUMBER \
  -Dtestlookup.branch=$GIT_BRANCH \
  -Dtestlookup.commit=$GIT_COMMIT \
  -Dtestlookup.launch="Smoke suite ($GIT_BRANCH)"
```

You can also use environment variables (`TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH`,
`TESTLOOKUP_COMMIT`, `TESTLOOKUP_LAUNCH`) — same effect, lower precedence than
JVM props. The full configuration chain is in
[client/java/README.md](../../../java/README.md#configuration-chain).

## JUnit 5 alternative

If you're on JUnit 5 instead of TestNG, swap the listener for the JUnit
extension and remove `testng.xml`:

```java
@ExtendWith(io.testlookup.junit5.TestLookupExtension.class)
class SmokeTests {
    @Test void exampleTest() { ... }
}
```

The same `testlookup.properties` file works unchanged.
