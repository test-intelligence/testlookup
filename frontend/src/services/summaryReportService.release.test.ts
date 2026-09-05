/**
 * NFR1 at the wire, for the summary report.
 *
 * The sibling of `analyticsService.release.test.ts`, and it exists for the same
 * reason that file gives: the hook-level tests mock this whole module, so
 * nothing there exercises the param builder. Mutation testing proved the point
 * — deleting `release_id` from this service, and sending it as `null` on every
 * call, both survived the hook tests untouched.
 *
 * The guarantee is that omitting a release produces the request this endpoint
 * produced before the release axis existed, not one with a null `release_id`
 * hanging off it. The backend's fragment is conditional precisely so the
 * planner keeps using `ix_test_runs_project_release_created`; a client that
 * always sent the key would make the server take the release branch for every
 * caller — a performance regression with no visible symptom.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getData = vi.hoisted(() =>
  vi.fn(async (_url: string, _config?: { params?: Record<string, unknown> }) => ({})),
)
vi.mock('./http', () => ({ getData, postData: vi.fn(), putData: vi.fn(), deleteData: vi.fn() }))

import { summaryReportService } from './summaryReportService'

const PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'
const RELEASE = 'rrrrrrrr-0000-0000-0000-000000000001'

function lastParams(): Record<string, unknown> {
  const calls = getData.mock.calls
  return calls[calls.length - 1]?.[1]?.params ?? {}
}

beforeEach(() => {
  getData.mockClear()
})

describe('summaryReportService — the release axis at the wire', () => {
  it('sends release_id when one is selected', async () => {
    await summaryReportService.get({
      project_id: PROJECT, days: 30, mode: 'window', release_id: RELEASE,
    })
    expect(lastParams().release_id).toBe(RELEASE)
  })

  it('omits release_id entirely when there is none', async () => {
    await summaryReportService.get({ project_id: PROJECT, days: 30, mode: 'window' })

    // `not.toHaveProperty` rather than a falsy check: `release_id: undefined`
    // still serialises the key on some clients, and the point is that the key
    // is absent — that is what keeps the backend's SQL byte-identical.
    expect(lastParams()).not.toHaveProperty('release_id')
  })

  it('omits release_id when it is explicitly null', async () => {
    // `useReleaseScope` returns null for "no release", and that must be the
    // same wire shape as not passing one at all.
    await summaryReportService.get({
      project_id: PROJECT, days: 30, mode: 'window', release_id: null,
    })
    expect(lastParams()).not.toHaveProperty('release_id')
  })

  it('still sends the parameters it always sent', async () => {
    // The control: a param builder that dropped everything would satisfy the
    // omission tests above.
    await summaryReportService.get({
      project_id: PROJECT, days: 14, mode: 'latest', release_id: RELEASE,
    })
    const params = lastParams()
    expect(params.project_id).toBe(PROJECT)
    expect(params.days).toBe(14)
    expect(params.mode).toBe('latest')
  })
})
