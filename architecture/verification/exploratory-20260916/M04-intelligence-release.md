# M04 ingest to intelligence to defect to release

**Final candidate:** `49ce63a18245292be95804cf2ae7c2a6ca28066e`, tag
`build-20260916-224030`, built at `2026-09-16T22:40:49Z`, schema `0189`.
Before the final tests, an independent inventory found all nine application
deployments fully updated and available, with ten ready application pods after
the critical worker autoscaled to two replicas; every pod had an immutable
image ID. Backend and workers served
`sha256:ce3bfce2f85fb294e31dff413d316b95473d9773795a488637abfb3ce2dc4374`,
frontend served
`sha256:f894bede666bf56b98c7d04370b06bfae8f49fc8e10a3fbae23c12b2a4cc7554`,
and MCP served
`sha256:12191208788cd7472e207f4b9c88d480213be1c5a666a6adfe6d866084f54336`.
`/health/version` reported the exact revision, readiness was `ready`, details
were `healthy`, and Alembic reported `0189 (head)`.

The first deployment attempt completed application image authority, then the
external Ollama registry refused an optional model download. The retry reused
the freshly built images, skipped only that download because both required
models were already present, and completed successfully. The final candidate
was rebuilt and deployed again after the review fixes before any final tests.

The primary disposable oracle journey used marker `exp-m04-1789593760`, project
`dc77e240-04f3-448b-a201-3edc6bd0f6db`, release
`9e596a3c-186b-4b08-b546-e5cb387bcffd`, and run
`6e45e61b-2dae-4a86-966c-f3a23cd57d3e`. Public ingestion task
`d02a8779-2218-41e9-9287-a514b3665db1` accepted a five-case JUnit report and
persisted four passes and one failure. Deep task
`146d2fc2-7d81-43b9-b844-68fcb8895ee4` produced pipeline run
`0652d396-ff8a-4944-8741-633f5a9187dd`: 16 stages completed, four skipped,
none failed or remained pending, and the run was not degraded.

The persisted cluster `cl_001` contained the one failing test and retained the
deterministic label `payment declined for deterministic M04 probe`. One
pipeline-origin finding was present. Promotion created HIGH local defect
`f7040a06-6b5a-4e58-a815-9f08bac1b40e`, OPEN and `pending_review`, and the
defect inherited the release. Gate evaluation appended decision
`6e8c4363-64ca-4585-bd3c-39c91695d0f4` with current verdict `NO_GO`, the exact
run ID, denominator/evidence count 5/5, status rollup 4 passed and 1 failed,
`explicit_client` attribution, complete ingestion, and the promoted defect as
the blocker.

The direct PostgreSQL oracle matched every API fact: the run's
`primary_release_id`, the primary release link and its source, cluster pipeline
lineage, defect release/severity/review state, and the append-only current gate
decision. Intelligence reported `generated_by=ai_pipeline`, no fallback,
confidence 25, one evidence item, and a pending human review. Reloaded API
reads preserved all counts and identifiers.

The maintained browser journey opened Run Intelligence, reloaded it, checked
the cluster and review warning, found the promoted defect, followed the release
deep link, and verified `Linked Test Runs (1)`, the build number, API run facts,
and the release scorecard. Chromium, Firefox, and WebKit passed with traces in
their respective
`frontend/test-results/intelligence-release-linea-3aa0d-ase-facts-across-UI-reloads-*/trace.zip`
directories.

The journey found EXP-BUG-013. Production did not register
`/releases/:releaseId`, so existing release links fell through to Dashboard.
After registration, the page still ignored `releaseId` and left the requested
release collapsed. Independent review then found that the intermediate fix
still depended on the persisted project's release list and did not honor the
real `#phase-…` action target. The final route fetches the authorized release
independently, renders only that requested release with its own project label,
and scrolls to the phase after async detail rendering. Its retry revalidates the
routed request, and the authorized project inventory supplies the project name
that the detail response omits. Five focused router and Releases page tests
passed. Six asserted mutations were killed: changing the
route, removing initial expansion, falling back to the persisted-project list,
removing the phase target ID, retrying the unrelated list, and dropping project
name resolution.

The final follow-up fixture used marker `exp-m04-1789598729`, project
`ebf4bcce-dce6-46b5-8c81-4974b12d6837`, release
`f3b1c1eb-6618-49c5-a883-3fdee227437d`, and run
`53c42cca-4e38-4d5b-98ff-d64cde81dc1e`. Its pipeline
`98d132fa-e890-479a-8bf8-1b0769e4cd93` again completed 16 stages, skipped
four, failed none, stayed non-degraded, produced one cluster and one pending
HIGH defect, and appended a `NO_GO` decision with complete 5/5 evidence. The
browser deliberately persisted an unrelated project while the live backend
served the requested release; Chromium, Firefox, and WebKit all passed. To keep
that unrelated selection stable through the TopBar refresh, the browser pinned
only the project-list response. Release detail, linked runs, defects, gate, and
all other assertions continued to use the deployed backend.

The live AI path was healthy, so this mission did not manufacture a degraded
model run. Deterministic counts, lineage, gate facts, and pending-review state
were independently asserted. The standing gate GET intentionally omits the
append-only decision ID; the evaluation POST supplied it. Run Intelligence
intentionally leaves project/release lineage on the canonical run response,
and release details expose `linked_runs`.

Both fixtures were cleaned with a full project reset and soft delete. Each
project is absent from the active project list. The final PostgreSQL cleanup
oracle records `is_active=false`, with zero runs, defects, failure clusters,
and gate decisions. One dormant `Unreleased` placeholder release remains by
the release lifecycle invariant.

**Result:** PASSED with EXP-BUG-013 remediated, homelab-verified, and
independently approved on reviewed diff
`5b777d527761de4ac18a4129143f6572e2c6ef7d`.
