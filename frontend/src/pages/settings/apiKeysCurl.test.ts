import { describe, expect, it } from 'vitest'
import { streamIngestCommand } from './apiKeysCurl'

describe('streamIngestCommand', () => {
  const cmd = streamIngestCommand('tl_live_abc123', 'https://ci.example.com')

  it('targets the streaming-ingest endpoint on the given base URL with the key', () => {
    expect(cmd).toContain('https://ci.example.com/api/v1/stream/ingest')
    expect(cmd).toContain('-H "X-API-Key: tl_live_abc123"')
  })

  it('leads the curl with -sS --fail-with-body so a rejected upload fails the CI step', () => {
    // Regression: a bare `curl -X POST` exits 0 on HTTP 4xx/5xx, so a runner
    // with a bad key (401) or a malformed payload (422) got a GREEN step while
    // nothing was ingested. --fail-with-body makes curl exit non-zero on any
    // HTTP >= 400 AND print the backend's error body; -sS keeps CI logs quiet
    // while still surfacing transport errors. Mirrors ingestApiCommand's guard.
    const curlLine = cmd.split('\n').find((l) => l.startsWith('curl'))
    expect(curlLine).toBeDefined()
    expect(curlLine).toMatch(/^curl -sS --fail-with-body -X POST /)
    // Never regress to a bare, silently-passing POST.
    expect(cmd).not.toMatch(/^curl -X POST/m)
  })

  it('sends a terminating run_complete event so the run finalises', () => {
    expect(cmd).toContain('"event_type":"run_complete"')
  })
})
