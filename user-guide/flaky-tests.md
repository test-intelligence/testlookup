# Quarantine policy and CI gating

How flaky-test quarantine is switched on or off for a project, how to tune its policy, and how to make a CI pipeline fail only on real (non-quarantined) failures. Using the **Flaky tests** page itself (the Detected, Proposed, Quarantined, History and Policy tabs, the flakiness score, proposing and approving) is covered in the in-app guide: **Flaky tests** (`/docs/flaky`, source [flaky.md](../frontend/src/content/guide/flaky.md)). Internals: [architecture/FLAKY_INTELLIGENCE.md](../architecture/FLAKY_INTELLIGENCE.md).

TestLookup treats flakiness as a **verdict backed by evidence**, not a failure-rate threshold: pass/fail alternation, error-signature variety, a confidence interval on the failure ratio and an ML score combine into a flaky verdict and a likely cause. A test that fails *consistently with the same error* is a **regression** and is never recommended for quarantine.

## Turning quarantine on (and off)

Quarantine is switched by the **`flaky_auto_quarantine`** feature flag. Migration 0065 seeded it **off**; since migration **0219** it is **on** for a new install and for every existing install whose row is still that untouched seed (off, never edited by an administrator). A row an administrator changed (switched on or off, given a project list, toggled later) keeps its value. Turning it on quarantines nothing by itself: a test is quarantined only after a QA lead approves a proposal, so the release gate and `testlookup ci-verdict` behave exactly as before until someone does.

With the flag off for a project:

- **Propose quarantine** is unavailable (the Detected tab says "Quarantine is off for this project, so no test can be proposed for it." and names the flag; **Mute** on Failures is disabled with the same reason; the API answers `503`).
- **Existing quarantines are not enforced**: the release gate counts their failures again, and the quarantine manifest that `testlookup ci-verdict` reads is **empty**, so every failure counts as real.
- Proposals do not expire.

To turn it off, or limit it, use **Settings → System → Feature flags** (administrators): switch `enabled_global` off for the whole instance, or keep it on and list the projects that may use it (`enabled_projects`, an allow-list; a project left out reads off). See the [feature-flag reference](../docs/operations/feature-flags.md) for how project lists and rollout combine; the change applies within 30 seconds. Migration 0219's downgrade turns the flag back off only where 0219 itself turned it on and nobody has edited it since.

## The quarantine workflow

With the flag on, quarantine is a reviewed, audited holding state:

1. **Propose** — a QA lead proposes a test (**Propose quarantine** on Flaky tests, **Mute** on Failures, the MCP `propose_quarantine` tool, or Ask AI's suggested action). The flaky sentinel also proposes tests whose verdict is flaky.
2. **Review** — a QA lead approves or rejects each proposal under **Flaky tests → Proposed** or **Inbox → Approvals**. Proposals nobody decides **expire after 7 days** (the nightly maintenance at 04:00 UTC).
3. **Quarantined** — approval quarantines the test for the policy's SLA (**14 days** by default). It still runs and still records results; it is excluded from the release-gate failure signal and listed in the CI manifest.
4. **Release** — when the fix lands, a QA lead releases it (**Release**, or **Promote out** once it has passed often enough). It counts again.

Every transition is audit-logged. All quarantine writes need `QA_LEAD`.

## Quarantine policy

Each project has a quarantine policy, on the **Policy** tab of **Flaky tests** (`/flaky?tab=policy`) and served at `GET/PUT /api/v1/projects/{project_id}/quarantine/policy`. Everyone can read it; changing it needs `QA_LEAD`, and each change is recorded in the activity feed as `quarantine_policy.updated` ("Quarantine policy changed: SLA days, auto-release"). Until a project saves one, the tab reads "Using defaults: …" and the defaults apply:

| Setting | Field | Default |
|---|---|---|
| Quarantine SLA (1-365 days) | `sla_days` | 14 days |
| File an internal TestLookup defect when a quarantine is approved (no Jira issue) | `auto_create_defect` | off |
| Release automatically after the consecutive passes below (off: the row is only flagged "Ready to promote") | `auto_promote` | off |
| Consecutive passes before a test is ready to promote out (1-1000) | `promote_after_passes` | 20 |
| Detection threshold: the flip rate (0-1) over at least the minimum runs (1-1000) at which the flaky sentinel proposes a test | `detection_flip_rate_threshold`, `detection_min_runs` | 20% over 10 runs |
| Warn when more than this many tests are quarantined (0-1000, `0` = no limit) | `max_active_quarantined` | 8 |

`max_active_quarantined` only warns: past it the **Quarantined** tab says "12 tests are quarantined; this project's policy warns past 8." It never blocks an approval. A `PUT` replaces every field, so a body without `max_active_quarantined` resets it to 8. A new SLA applies to tests quarantined after the change. The exact schema is in the API reference (`/api-docs`, the flaky-quarantine section).

## Gating CI on real failures only (`testlookup ci-verdict`)

Quarantine only pays off if it actually unblocks merges. `testlookup ci-verdict` closes the loop with **zero test-code changes**: after uploading results, ask TestLookup whether the run's failures are all quarantined or known-flaky, and let the exit code gate the pipeline.

```bash
testlookup ci-verdict --run "$RUN_ID" --project "$PROJECT_ID"
```

- **Exit `0`** — no *real* failures. Quarantined failures do not block (that is the point).
- **Exit `1`** — at least one non-quarantined failure. With `--strict`, ANY failure blocks, quarantined included.
- **Exit `2`** — TestLookup unreachable, an API error, or the run did not finish inside `--timeout-seconds`. **Fails closed by default** — a broken TestLookup must never silently green a build. Teams that prefer availability can pass `--fail-open` (exit 0 with a loud warning on infrastructure errors).

How it decides: the CLI fetches the run's FAILED/BROKEN tests and the project's **quarantine manifest** (`GET /api/v1/projects/{project_id}/quarantine/manifest` — currently active quarantines only, ETag-cached; also an MCP resource, `testlookup://projects/{id}/quarantine-manifest`), then matches primarily by test fingerprint (`sha256(class::name)`, recomputed client-side), falling back to an exact `(test_name, suite_name)` match. `--output json` emits the full partition (`{verdict, counts, real_failures, quarantined_failures}`); `--timeout-seconds N` polls a still-running run before judging.

> With `flaky_auto_quarantine` off the manifest is empty, so `ci-verdict` treats every failure as real.

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

The GitHub integration's check run follows the same rule: a run whose failures are all known-flaky or quarantined concludes `neutral` instead of `failure` (see [Administration → GitHub](administration.md#github-settingsgithub)).

## Recommended rhythm

- **Weekly**: clear **Flaky tests → Proposed** — approve the genuinely flaky, reject the rest. An un-reviewed queue silently expires after 7 days.
- **Per quarantine**: treat **Quarantined** as a to-fix list with an owner, not a parking lot. The test page's **Cross-run step flakiness** usually points at the exact wait, retry or assertion to fix. Rows marked **stale** have outlived their SLA.
- **On release**: check a few green runs in the test's history before releasing it — releasing a still-flaky test puts the noise straight back into the gate. **Ready to promote** marks tests that have passed often enough.
- **Fix the step, not the test**, and remember quarantine is for flaky tests only. A red test that is not flaky needs a fix, a defect, or a reasoned gate override — see [Gate policies and CI gating](release-gates.md).
