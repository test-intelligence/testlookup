# Flaky tests & quarantine

A flaky test changes verdict without a code change — and it poisons everything downstream: trust in red builds, release-gate accuracy, triage time. TestLookup treats flakiness as a **verdict backed by evidence**, not a failure-rate threshold: pass/fail alternation, error-signature variety, a statistical confidence interval on the failure ratio, and an ML score are combined into `is_flaky` + a likely cause. A test that fails *consistently with the same error* is called a **regression** and will never be recommended for quarantine — that one needs a fix, not a mute.

(Internals: [architecture/FLAKY_INTELLIGENCE.md](../architecture/FLAKY_INTELLIGENCE.md).)

## Where flakiness shows up

| Surface | What you get |
|---|---|
| **Flaky Coach** (`/flaky-coach`) | Per-test coaching: cross-run history, flaky confidence, likely cause. Needs a *specific* project selected — it's disabled in the admin All-Projects view. |
| **Test detail → "Cross-Run Step Flakiness"** | For step-based tests: *which step* flips verdict across runs, with flip counts and current status — so you fix the flaky step, not the whole test. Shows "Not enough run history" until enough runs exist, and says so when the test is stable. |
| **Run detail → step-flip roll-up** | The same signal aggregated across a run's tests. |
| **Failure Analysis / triage verdicts** | The regression-vs-flaky call on each failure (see [Triaging failures](triaging-failures.md)). |
| **MCP** | `get_test_step_flips` exposes the step-flip report to AI/agent consumers. |

## The quarantine workflow

Quarantine is a reviewed, audited holding state — not a mute button. **Flaky Quarantine** (`/quarantine`) organizes it into four buckets: **Awaiting review**, **Active quarantines**, **Released**, and **Rejected / expired**.

1. **Propose** — the flaky sentinel proposes quarantine when a test's *verdict* says flaky (recommendations are derived from the verdict, so a consistently-failing regression never gets proposed). Humans can propose too, from the flaky surfaces.
2. **Review** — a human approves or rejects each proposal in **Awaiting review**. Proposals nobody acts on **expire** on their own rather than piling up.
3. **Active** — while quarantined, the test still *runs* and still *records results*; it's excluded from the **release-gate failure signal** only. You keep the data, you lose the noise.
4. **Release** — when the fix lands (watch the test go stable in the coach), release it from quarantine and it counts again.

Every transition is audit-logged.

## Gating CI on real failures only (`testlookup ci-verdict`)

Quarantine only pays off if it actually unblocks merges. `testlookup ci-verdict` closes the loop with **zero test-code changes**: after uploading results, ask TestLookup whether the run's failures are all quarantined/known-flaky — and let the exit code gate the pipeline.

```bash
testlookup ci-verdict --run "$RUN_ID" --project "$PROJECT_ID"
```

- **Exit `0`** — no *real* failures. Quarantined failures don't block (that's the point).
- **Exit `1`** — at least one non-quarantined failure. With `--strict`, ANY failure blocks, quarantined included.
- **Exit `2`** — TestLookup unreachable / API error / the run didn't finish inside `--timeout-seconds`. **Fails closed by default** — a broken TestLookup must never silently green a build. Teams that prefer availability can pass `--fail-open` (exit 0 with a loud warning on infrastructure errors).

How it decides: the CLI fetches the run's FAILED/BROKEN tests and the project's **quarantine manifest** (`GET /api/v1/projects/{project_id}/quarantine/manifest` — currently-active quarantines only, ETag-cached; also an MCP resource, `testlookup://projects/{id}/quarantine-manifest`), then matches primarily by test fingerprint (`sha256(class::name)`, recomputed client-side), falling back to an exact `(test_name, suite_name)` match. `--output json` emits the full partition (`{verdict, counts, real_failures, quarantined_failures}`) for dashboards; `--timeout-seconds N` polls a still-running run before judging.

### GitHub Actions

```yaml
- name: Run tests (don't stop on red — the verdict decides)
  run: mvn test || true

- name: Upload results to TestLookup
  id: upload
  run: |
    RUN_ID=$(testlookup upload file target/surefire-reports/results.xml \
      --project "$PROJECT_ID" --build "$GITHUB_RUN_NUMBER" --output json | jq -r '.run_id')
    echo "run_id=$RUN_ID" >> "$GITHUB_OUTPUT"
  env:
    TESTLOOKUP_URL: ${{ vars.TESTLOOKUP_URL }}
    TESTLOOKUP_API_KEY: ${{ secrets.TESTLOOKUP_API_KEY }}

- name: Gate on real failures only
  run: testlookup ci-verdict --run "${{ steps.upload.outputs.run_id }}" --project "$PROJECT_ID" --timeout-seconds 120
  env:
    TESTLOOKUP_URL: ${{ vars.TESTLOOKUP_URL }}
    TESTLOOKUP_API_KEY: ${{ secrets.TESTLOOKUP_API_KEY }}
```

### Jenkins (declarative)

```groovy
stage('Quarantine-aware gate') {
  environment { TESTLOOKUP_API_KEY = credentials('testlookup-api-key') }
  steps {
    sh '''
      mvn test || true
      RUN_ID=$(testlookup upload file target/surefire-reports/results.xml \
        --project "$PROJECT_ID" --build "$BUILD_NUMBER" --output json | jq -r '.run_id')
      testlookup ci-verdict --run "$RUN_ID" --project "$PROJECT_ID" --timeout-seconds 120
    '''
  }
}
```

### GitLab CI

```yaml
verdict:
  stage: gate
  script:
    - pytest --junitxml=results.xml || true
    - RUN_ID=$(testlookup upload file results.xml --project "$PROJECT_ID" --build "$CI_PIPELINE_IID" --output json | jq -r '.run_id')
    - testlookup ci-verdict --run "$RUN_ID" --project "$PROJECT_ID" --timeout-seconds 120
```

Note the `|| true` on the raw test command: the runner must not stop on the framework's own red exit, or the verdict step never runs. The gate's honesty moves to `ci-verdict` — which still fails the build on any real failure.

## Recommended rhythm

- **Weekly**: clear **Awaiting review** — approve the genuinely flaky, reject the rest. An un-reviewed queue silently expires, which wastes the detector's work.
- **Per quarantine**: treat Active as a to-fix list with an owner, not a parking lot. The step-flip panel usually points at the exact wait/retry/assertion to fix.
- **On release**: verify a few green runs in the coach's history before releasing — releasing a still-flaky test puts the noise straight back into the gate.

## Tips

- **Fix the step, not the test.** When the step-flip panel isolates one flipping step (a wait, a network call, an animation), the fix is usually local — the rest of the test is fine.
- **Quarantine is for flaky, period.** If something red is blocking the gate and it *isn't* flaky, the answers are a fix, a defect, or (rarely) a reasoned gate override — see [Release gates](release-gates.md).
- **Confidence grows with history.** Fresh tests can't be confidently called flaky; if the coach shows thin history, let it accumulate runs before acting.
