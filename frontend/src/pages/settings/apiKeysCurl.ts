/**
 * Copy-paste snippets shown on the API Keys page after a key is generated.
 *
 * Kept in a non-component module (not ApiKeysPage.tsx) so the pure helpers stay
 * unit-testable and don't trip react-refresh's "only export components" rule —
 * mirroring how the first-run guide keeps `ingestApiCommand` in firstRunSteps.ts.
 */

/**
 * The `curl` shown for CI runners that hit the streaming-ingest endpoint
 * directly rather than installing an SDK.
 *
 * It leads with `-sS --fail-with-body` because it is meant to run unattended in
 * a CI step. Plain `curl` exits 0 even when the ingest endpoint REJECTS the
 * batch (a bad API key → 401, a wrong project id → 404, a malformed event
 * payload → 422), so a self-hoster who wires the bare command into their
 * pipeline sees a green step while nothing was ingested — the same silent "your
 * CI thinks it worked when it didn't" trap the first-run guide's ingest snippet
 * already closes. `--fail` makes curl exit non-zero on any HTTP >= 400; the
 * `-with-body` variant additionally prints the backend's error response so the
 * operator can see WHY it was rejected (curl 7.76+, 2021). `-sS`
 * (`--silent --show-error`) drops the progress meter from CI logs while still
 * surfacing transport errors.
 */
export function streamIngestCommand(apiKey: string, baseUrl: string): string {
  return (
    `# Send test results AND a final run_complete event so the run finalises
# (creates the TestRun row + triggers AI analysis). Without run_complete
# the session stays active and the run won't appear in Runs / Overview.
curl -sS --fail-with-body -X POST ${baseUrl}/api/v1/stream/ingest \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "run_id": "ci-build-1",
    "meta": {"launch_name": "Smoke suite"},
    "events": [
      {"event_type":"test_result","test_name":"smoke","status":"PASSED","duration_ms":15},
      {"event_type":"run_complete"}
    ]
  }'`
  )
}
