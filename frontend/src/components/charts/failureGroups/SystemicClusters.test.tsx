import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { __resetChartConcurrency } from '@/services/chartApi'
import { clustersBody } from './failureGroups.fixtures'
import { EMPTY_ANSWER_HEIGHT } from './plot.model'
import SystemicClusters, { ALL_PROJECTS_REASON } from './SystemicClusters'
import { EMPTY_CLUSTERS_FALLBACK, IDENTITY_NOTE, validateClustersResponse } from './systemicClusters.model'
import { CLUSTER_ACCESSORS } from './useSystemicClusters'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

let body: unknown
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

function renderClusters(props: Partial<Parameters<typeof SystemicClusters>[0]> = {}) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>{children}</SWRConfig>
  )
  return render(
    <SystemicClusters params={{ project_id: 'p1' }} allProjects={false} headingLevel={3} plotWidth={800} {...props} />,
    { wrapper },
  )
}

beforeEach(() => {
  body = clustersBody()
  get.mockReset()
  get.mockImplementation(() =>
    body instanceof Error ? Promise.reject(body) : Promise.resolve({ data: body, headers: { 'x-request-id': 'req-9' } }),
  )
  __resetChartConcurrency()
})

describe('CLUSTER_ACCESSORS', () => {
  it('an empty list is never "empty" (it is an answer); shown counts the clusters', () => {
    const value = { meta: null, clusters: [], emptyIsNormal: null, scopeNote: null }
    expect(CLUSTER_ACCESSORS.isEmpty(value)).toBe(false)
    expect(CLUSTER_ACCESSORS.shown({ ...value, clusters: [{} as never, {} as never] })).toBe(2)
    expect(CLUSTER_ACCESSORS.meta(value)).toBeNull()
  })
})

describe('SystemicClusters', () => {
  it('a given state (the gallery) is drawn and nothing is asked', async () => {
    const checked = validateClustersResponse(clustersBody())
    if (!checked.ok) throw new Error(checked.errors.join('; '))
    const { container } = renderClusters({ state: { status: 'ready', data: checked.value, meta: null, revalidating: false } })
    await settle()
    expect(get).not.toHaveBeenCalled()
    expect(container.querySelectorAll('[data-cluster-key]')).toHaveLength(2)
  })

  it('a host may name the frame (several on one page); by default the window names it', async () => {
    renderClusters({ title: 'Nightly clusters' })
    await settle()
    expect(screen.getByRole('heading', { name: 'Nightly clusters' })).toBeInTheDocument()
  })

  it('asks /analytics/systemic-clusters with the page scope and draws the clusters largest first', async () => {
    const { container } = renderClusters()
    await settle()
    expect(get).toHaveBeenCalledWith(
      '/api/v1/analytics/systemic-clusters',
      expect.objectContaining({ params: { project_id: 'p1' } }),
    )
    expect(screen.getByRole('heading', { name: 'Tests that fail together (last 60 days, whole project)' })).toBeInTheDocument()
    expect(container.querySelector('[data-chart-takeaway]')).toHaveTextContent('2 clusters; the largest has 14 tests')
    expect(container.querySelectorAll('[data-group-plot="clusters"] [data-group-id]')).toHaveLength(2)
    const items = [...container.querySelectorAll('[data-cluster-key]')]
    expect(items.map((li) => li.getAttribute('data-cluster-key'))).toEqual(['a'.repeat(32), 'b'.repeat(32)])
    expect(items[1]).toHaveTextContent('#2 constructor')
    expect(items[1]).toHaveTextContent('Unknown cause · 3 tests · cohesion 50.0% · 4 runs failing together')
    expect(container.querySelector('[data-clusters-identity]')).toHaveTextContent(IDENTITY_NOTE)
    expect(container.textContent).not.toMatch(/\bAI\b/)
  })

  it('member names are text, behind a disclosure', async () => {
    const { container } = renderClusters()
    await settle()
    const first = container.querySelector('[data-cluster-key]') as HTMLElement
    // R2-B F-09: the cluster has 14 tests and lists 2 of them; the toggle says so.
    fireEvent.click(within(first).getByText('Members (2 of 14)'))
    expect(first.querySelectorAll('[data-cluster-member]')).toHaveLength(2)
    expect(first.querySelector('[data-cluster-member]')).toHaveTextContent(
      '<img src=x onerror="window.__xss=1">test_pay (9 failing runs)',
    )
    expect(container.querySelector('img')).toBeNull()
  })

  it('a list as long as the cluster says only its length', async () => {
    const [first, second] = clustersBody().items as Record<string, unknown>[]
    body = clustersBody({ items: [{ ...first, size: 2 }, second] })
    const { container } = renderClusters()
    await settle()
    expect(within(container.querySelector('[data-cluster-key]') as HTMLElement).getByText('Members (2)')).toBeInTheDocument()
  })

  // R2-B F-14: the empty answer's one sentence sat on a 300 px body (a ~250 px empty band under it).
  it('the empty answer does not hold the plot’s height; a drawn list does', async () => {
    const drawn = renderClusters()
    await settle()
    expect((drawn.container.querySelector('[data-chart-body]') as HTMLElement).style.minHeight).toBe('300px')
    body = clustersBody({ items: [], total: 0 })
    const empty = renderClusters({ params: { project_id: 'p4' } })
    await settle()
    expect((empty.container.querySelector('[data-chart-body]') as HTMLElement).style.minHeight).toBe(
      `${EMPTY_ANSWER_HEIGHT}px`,
    )
    expect(EMPTY_ANSWER_HEIGHT).toBeLessThan(160)
  })

  it('an empty list is an answer: the server’s sentence, drawn, not "no data"', async () => {
    body = clustersBody({ items: [], total: 0 })
    const { container } = renderClusters()
    await settle()
    expect(container.querySelector('[data-chart-state]')).toHaveAttribute('data-chart-state', 'ready')
    expect(container.querySelector('[data-clusters-empty]')).toHaveTextContent('Most projects have no systemic clusters.')
    body = clustersBody({ items: [], total: 0, empty_is_normal: undefined })
    const again = renderClusters({ params: { project_id: 'p2' } })
    await settle()
    expect(again.container.querySelector('[data-clusters-empty]')).toHaveTextContent(EMPTY_CLUSTERS_FALLBACK)
  })

  it('a filtered list states that the statistics are project-wide', async () => {
    body = clustersBody({
      scope: { membership: 'suite', note: 'Cluster statistics are computed project-wide and are NOT recomputed per suite.' },
    })
    const { container } = renderClusters({ params: { project_id: 'p1', suite_name: 'payments' } })
    await settle()
    expect(container.querySelector('[data-clusters-scope]')).toHaveTextContent('NOT recomputed per suite')
  })

  it('All Projects: nothing asked; the frame says why', async () => {
    renderClusters({ allProjects: true })
    await settle()
    expect(get).not.toHaveBeenCalled()
    expect(screen.getByText(ALL_PROJECTS_REASON)).toBeInTheDocument()
  })

  it('a malformed body is an error frame naming the request; a failure offers Retry', async () => {
    body = { items: [{ size: 'x' }] }
    const { container } = renderClusters()
    await settle()
    expect(container.querySelector('[data-chart-error-kind="invalid-payload"]')).not.toBeNull()
    expect(container.querySelector('[data-chart-request-id]')).toHaveTextContent('req-9')
    body = Object.assign(new Error('boom'), { response: { status: 500, data: {} } })
    const failed = renderClusters({ params: { project_id: 'p3' } })
    await settle()
    expect(within(failed.container).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
