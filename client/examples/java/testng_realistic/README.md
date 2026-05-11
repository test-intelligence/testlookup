# realistic TestNG client examples

This Maven project uses TestNG as the harness and the Java `TestLookupReporter`
client directly, which lets the examples attach rich metadata to each emitted
test result.

## Run

1. Replace `src/test/resources/testlookup.properties` with the streaming key
   block from TestLookup.
2. Run:

```bash
mvn test
```

- `ParallelSuitesWithOneApiKeyTest` starts multiple suites concurrently with
  one reporter configuration and one project-scoped API key.
- `SingleSuiteHundredCasesTest` submits one suite with 100 cases. Every case
  includes step definitions, assertions, and expected results in metadata.
