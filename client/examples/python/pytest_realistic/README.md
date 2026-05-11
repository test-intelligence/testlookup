# realistic pytest client examples

These examples generate production-shaped test run data through the Python
client instead of relying on pytest's automatic plugin.

## Run

```bash
pip install -r requirements.txt
pytest -m integration
```

Both tests use the same project-scoped API key from `testlookup.properties` or
`TESTLOOKUP_API_KEY`.

- `test_parallel_suites.py` starts several suites concurrently, each with its
  own run id, to model parallel CI workers sharing one API key.
- `test_single_suite_100_cases.py` submits one suite with 100 cases. Every case
  includes step definitions, assertions, and expected results in event metadata.
