import { test, expect, type Page } from '@playwright/test'

/**
 * E7.5 (architecture section 7.1): no /agents list row ever shows a status
 * outside `in_progress | completed | failed | passed`.
 *
 * The pipelines list is mocked with every internal state the API can return,
 * half of them carrying the server's `public_status` and half not (a payload
 * that predates it), so the assertion covers both the server projection and
 * the client fallback in the real rendered page. The rest of the page talks to
 * the live backend, as the other specs here do.
 */

const INTERNAL = ['pending', 'running', 'retry_wait', 'completed', 'passed', 'failed'] as const
const PUBLIC_OF: Record<(typeof INTERNAL)[number], string> = {
  pending: 'in_progress',
  running: 'in_progress',
  retry_wait: 'in_progress',
  completed: 'completed',
  passed: 'passed',
  failed: 'failed',
}
const ALLOWED = ['IN PROGRESS', 'COMPLETED', 'FAILED', 'PASSED']

function row(i: number, status: (typeof INTERNAL)[number], withPublic: boolean) {
  const hex = i.toString(16).padStart(12, '0')
  return {
    id: `00000000-0000-4000-8000-${hex}`,
    test_run_id: `00000000-0000-4000-9000-${hex}`,
    workflow_type: 'offline',
    status,
    ...(withPublic ? { public_status: PUBLIC_OF[status] } : {}),
    attempt: status === 'retry_wait' ? 2 : 1,
    max_attempts: 5,
    started_at: '2026-09-12T10:00:00Z',
    completed_at: status === 'completed' || status === 'passed' || status === 'failed' ? '2026-09-12T10:05:00Z' : null,
    error: status === 'failed' ? 'boom' : null,
    created_at: new Date(Date.UTC(2026, 8, 12, 10, i)).toISOString(),
    execution_metadata: {},
    provenance_metadata: null,
    build_number: `e75-${i}`,
    run_seq: 900 + i,
    suite_name: null,
  }
}

async function mockPipelines(page: Page) {
  const rows = [
    ...INTERNAL.map((s, i) => row(i, s, true)),
    ...INTERNAL.map((s, i) => row(i + INTERNAL.length, s, false)),
  ]
  // The list call only (".../agents/pipelines" with an optional query), not
  // ".../pipelines/{id}/..." or ".../pipelines/trigger".
  await page.route(/\/api\/v1\/agents\/pipelines(\?[^/]*)?$/, async route => {
    if (route.request().method() !== 'GET') return route.continue()
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) })
  })
  return rows.length
}

test.describe('/agents status chips (E7.5)', () => {
  test('every pipeline card shows one of the four public statuses', async ({ page }) => {
    const expected = await mockPipelines(page)

    await page.goto('/agents')

    const chips = page.getByTestId('pipeline-status-chip')
    await expect(chips).toHaveCount(expected, { timeout: 15_000 })

    const texts = (await chips.allTextContents()).map(t => t.trim())
    for (const text of texts) {
      expect(ALLOWED, `chip "${text}"`).toContain(text)
    }
    // pending + running + retry_wait, twice (projected and fallback)
    expect(texts.filter(t => t === 'IN PROGRESS')).toHaveLength(6)
    expect(texts.filter(t => t === 'PASSED')).toHaveLength(2)
  })

  test('a run waiting to retry says so without leaking the internal state', async ({ page }) => {
    await mockPipelines(page)
    await page.goto('/agents')

    await expect(page.getByTestId('pipeline-retry-detail').first()).toHaveText(/retrying · 2\/5/)
    await expect(page.getByText(/retry_wait/i)).toHaveCount(0)
  })
})
