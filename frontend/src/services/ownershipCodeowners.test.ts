import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockPost = vi.hoisted(() => vi.fn())
const mockGet = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: { post: mockPost, get: mockGet } }))

import {
  CODEOWNERS_SERVICE,
  getCodeownersCoverage,
  importCodeowners,
} from './ownershipService'

describe('CODEOWNERS ownership service (US-8.3)', () => {
  beforeEach(() => {
    mockPost.mockReset()
    mockGet.mockReset()
  })

  it('exposes the CODEOWNERS provenance marker as a constant', () => {
    expect(CODEOWNERS_SERVICE).toBe('CODEOWNERS')
  })

  it('posts the paste-text import body to the project-scoped endpoint', async () => {
    mockPost.mockResolvedValue({
      data: {
        imported: 2, rules_created: 2, rules_replaced: 0, source: 'text',
        coverage: { path_rules: 2, codeowners_rules: 2, sampled: 0, located: 0, matched: 0, coverage_pct: 0, lookback_days: 30 },
      },
    })

    const res = await importCodeowners('p1', { source: 'text', text: 'src/**  @alice\n' })

    expect(res.rules_created).toBe(2)
    const [url, body] = mockPost.mock.calls[0]
    expect(url).toBe('/api/v1/projects/p1/ownership/codeowners/import')
    expect(body).toEqual({ source: 'text', text: 'src/**  @alice\n' })
  })

  it('posts the github-fetch import body without text', async () => {
    mockPost.mockResolvedValue({
      data: {
        imported: 1, rules_created: 1, rules_replaced: 3, source: 'github',
        coverage: { path_rules: 1, codeowners_rules: 1, sampled: 0, located: 0, matched: 0, coverage_pct: 0, lookback_days: 30 },
      },
    })

    await importCodeowners('p1', { source: 'github' })

    const [, body] = mockPost.mock.calls[0]
    expect(body).toEqual({ source: 'github', text: undefined })
  })

  it('fetches coverage with the days param', async () => {
    mockGet.mockResolvedValue({
      data: { path_rules: 4, codeowners_rules: 3, sampled: 20, located: 10, matched: 7, coverage_pct: 70, lookback_days: 14 },
    })

    const cov = await getCodeownersCoverage('p1', 14)

    expect(cov.coverage_pct).toBe(70)
    const [url, config] = mockGet.mock.calls[0]
    expect(url).toBe('/api/v1/projects/p1/ownership/codeowners/coverage')
    expect(config).toEqual({ params: { days: 14 } })
  })
})
