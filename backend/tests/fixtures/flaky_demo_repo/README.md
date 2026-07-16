# flaky_demo_repo

A minimal one-test project used by the Fixer (AI-2) tests:

- `tests/test_flaky_timing.py` — a deliberately time-flaky test used to
  exercise the `ValidationRunner` pipeline end-to-end (FakeRunner in CI; a
  live `DockerEphemeralRunner` attempt when Docker + a local python image are
  present).

The Fixer's test-code-only patch stabilises the assertion; validation then
reruns the test M times and expects all passes.
