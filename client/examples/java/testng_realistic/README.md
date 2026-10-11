# realistic TestNG client examples

This Maven project uses TestNG as the harness and the Java `TestLookupReporter`
client directly, which lets the examples attach rich metadata to each emitted
test result.

## Run

1. Install the reporter into your local Maven repository once, from the
   repository root: `mvn -f client/java/pom.xml install -DskipTests`.
2. Replace `src/test/resources/testlookup.properties` with the
   `testlookup.properties` block TestLookup shows when an ADMIN creates a key
   under *Settings → Streaming API keys*.
3. Run:

```bash
mvn test
```

- `ParallelSuitesWithOneApiKeyTest` starts multiple suites concurrently with
  one reporter configuration and one project-scoped API key.
- `SingleSuiteHundredCasesTest` submits one suite with 100 cases. Every case
  includes step definitions, assertions, and expected results in metadata.

The client API used here is described in
[client/java/README.md](../../../java/README.md#programmatic-use).
