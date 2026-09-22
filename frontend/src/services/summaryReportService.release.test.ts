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

const apiGet = vi.hoisted(() =>
  vi.fn(async (_url: string, _config?: { params?: Record<string, unknown> }) => ({
    data: new Blob(['%PDF-1.4']),
  })),
)
vi.mock('./api', () => ({ api: { get: apiGet } }))

import { summaryReportService } from './summaryReportService'

const PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'
const RELEASE = 'rrrrrrrr-0000-0000-0000-000000000001'

function lastParams(): Record<string, unknown> {
  const calls = getData.mock.calls
  return calls[calls.length - 1]?.[1]?.params ?? {}
}

beforeEach(() => {
  getData.mockClear()
  apiGet.mockClear()
})

describe('summaryReportService — the PDF covers what the screen showed', () => {
  // The screen is release-scoped; the PDF used to be built from its own param
  // list, which had no release — so a sign-off PDF exported under a release
  // filter silently covered every release. Both requests now go through one
  // builder; these pin that the two query strings are IDENTICAL.
  const cases: { name: string; release_id?: string | null }[] = [
    { name: 'no release' },
    { name: 'no release (explicit null)', release_id: null },
    { name: 'one release', release_id: RELEASE },
    { name: 'the unattributed sentinel', release_id: 'unattributed' },
  ]

  for (const c of cases) {
    it(`sends the same params for ${c.name}`, async () => {
      const args = { project_id: PROJECT, days: 30, mode: 'latest' as const, release_id: c.release_id }
      await summaryReportService.get(args)
      await summaryReportService.downloadPdf(args)

      const reportParams = lastParams()
      const pdfCall = apiGet.mock.calls[apiGet.mock.calls.length - 1]
      expect(pdfCall[0]).toBe('/api/v1/reports/summary/pdf')
      expect(pdfCall[1]?.params).toEqual(reportParams)

      // And the equality is not vacuous: the release is (or is not) there.
      if (c.release_id) {
        expect(pdfCall[1]?.params?.release_id).toBe(c.release_id)
      } else {
        expect(pdfCall[1]?.params).not.toHaveProperty('release_id')
      }
      expect(pdfCall[1]?.params?.project_id).toBe(PROJECT)
    })
  }
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
