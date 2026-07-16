# The Fixer (AI-2) ships a real, runnable pytest project under
# ``flaky_demo_repo/`` — its ``test_flaky_timing`` is *deliberately* flaky
# (passes ~50% of the time) so the Fixer has something to reproduce and
# stabilise in a sandbox. That project is cloned and executed by the
# DockerEphemeralRunner / e2e test as file content; it must NEVER be
# collected by TestLookup's own suite, or CI goes red at random.
collect_ignore = ["flaky_demo_repo"]
