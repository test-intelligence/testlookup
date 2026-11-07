/**
 * STRAT-03 — the per-cluster epic action: exactly the four cases of the W36 contract §3 (linked / create /
 * disabled with the reason as visible text / nothing), the QA-engineer gate, the recurrence rule, and ONE status
 * request per project however many clusters ask. Real hooks + service; only the HTTP client is mocked.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SystemicCluster } from '@/components/charts/failureGroups/systemicClusters.model'
import type { ClusterEpicsStatus } from '@/services/defectJiraService'
import { useAuthStore } from '@/store/authStore'
import ClusterEpicAction, { CREATE_EPIC_LABEL, JIRA_UNAVAILABLE_FALLBACK, NO_EPIC_TYPE_REASON } from './ClusterEpicAction'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/services/api', () => ({ api: http }))

const STATUS_URL = '/api/v1/projects/p1/systemic-clusters/epics'
const KEY_A = 'a'.repeat(32)
const KEY_B = 'b'.repeat(32)

function cluster(over: Partial<SystemicCluster> = {}): SystemicCluster {
  return {
    key: KEY_A,
    membershipKey: KEY_A,
    clusterKey: 'sfc_001',
    label: 'checkout tests fail together',
    causeFamily: 'external_dependency',
    size: 14,
    cohesion: 0.82,
    coFailureRuns: 9,
    windowDays: 60,
    computedAt: '2026-10-01T06:40:00Z',
    members: [],
    ...over,
  }
}

const STATUS: ClusterEpicsStatus = {
  enabled: true,
  available: true,
  reason: null,
  epic_type_available: true,
  min_co_failure_runs: 3,
  links: [],
}

let status: unknown

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

function renderActions(clusters: SystemicCluster[]) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>{children}</SWRConfig>
  )
  return render(
    <ul>
      {clusters.map((c) => (
        <li key={c.key} data-cluster-key={c.key}>
          <ClusterEpicAction projectId="p1" cluster={c} />
        </li>
      ))}
    </ul>,
    { wrapper },
  )
}

const statusCalls = () => http.get.mock.calls.filter(([url]) => url === STATUS_URL)
const setRole = (role: string) => act(() => useAuthStore.setState({ user: { id: 'u-1', role } as never }))

beforeEach(() => {
  status = STATUS
  http.get.mockReset()
  http.post.mockReset()
  http.get.mockImplementation((url: string) =>
    url === STATUS_URL
      ? status instanceof Error
        ? Promise.reject(status)
        : Promise.resolve({ data: status })
      : Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } })),
  )
  setRole('QA_ENGINEER')
})
afterEach(() => {
  act(() => useAuthStore.setState({ user: null }))
})

describe('ClusterEpicAction', () => {
  it('opted in, recurring, Jira ready, QA engineer: "Create epic for this cluster"; ONE status request for two clusters', async () => {
    const { container } = renderActions([cluster(), cluster({ key: KEY_B, membershipKey: KEY_B, coFailureRuns: 4 })])
    await settle()
    expect(screen.getAllByRole('button', { name: CREATE_EPIC_LABEL })).toHaveLength(2)
    expect(container.querySelectorAll('[data-cluster-epic="create"]')).toHaveLength(2)
    expect(statusCalls()).toEqual([[STATUS_URL, { suppressToast: true }]])
    expect(http.post).not.toHaveBeenCalled()
  })

  it('Create opens the form under the row (the preview is asked; nothing is posted)', async () => {
    renderActions([cluster()])
    await settle()
    fireEvent.click(screen.getByRole('button', { name: CREATE_EPIC_LABEL }))
    expect(screen.getByRole('region', { name: 'Create epic for this cluster' })).toBeInTheDocument()
    await settle()
    expect(http.get).toHaveBeenCalledWith(`/api/v1/projects/p1/systemic-clusters/${KEY_A}/epic/preview`, {
      suppressToast: true,
    })
    expect(http.post).not.toHaveBeenCalled()
  })

  it('a stored link: "Epic <KEY>" opening Jira in a new tab, for any role, even below the recurrence rule', async () => {
    status = {
      ...STATUS,
      links: [
        {
          membership_key: KEY_A,
          jira_key: 'QA-12',
          jira_url: 'https://jira.example/browse/QA-12',
          cluster_label: 'checkout tests fail together',
          created_at: '2026-11-01T10:00:00Z',
          created_by_name: 'Dana',
        },
      ],
    }
    setRole('VIEWER')
    renderActions([cluster({ coFailureRuns: 1 }), cluster({ key: KEY_B, membershipKey: KEY_B })])
    await settle()
    const link = screen.getByRole('link', { name: 'Epic QA-12' })
    expect(link).toHaveAttribute('href', 'https://jira.example/browse/QA-12')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer')
    // The other cluster has no link, and a viewer is offered no Create.
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('Jira unavailable: the button disabled and the server reason as visible text', async () => {
    status = { ...STATUS, available: false, epic_type_available: null, reason: 'Jira is offline: AI_OFFLINE_MODE is on.' }
    const { container } = renderActions([cluster()])
    await settle()
    const button = screen.getByRole('button', { name: CREATE_EPIC_LABEL })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription('Jira is offline: AI_OFFLINE_MODE is on.')
    expect(container.querySelector('[data-cluster-epic-reason]')).toBeVisible()
    expect(screen.getByText('Jira is offline: AI_OFFLINE_MODE is on.')).toBeInTheDocument()
  })

  it('Jira unavailable without a sentence: a plain fallback, never a blank', async () => {
    status = { ...STATUS, available: false, epic_type_available: null, reason: null }
    renderActions([cluster()])
    await settle()
    expect(screen.getByRole('button', { name: CREATE_EPIC_LABEL })).toBeDisabled()
    expect(screen.getByText(JIRA_UNAVAILABLE_FALLBACK)).toBeInTheDocument()
  })

  it('no Epic issue type: the button disabled with that reason', async () => {
    status = { ...STATUS, epic_type_available: false }
    renderActions([cluster()])
    await settle()
    expect(screen.getByRole('button', { name: CREATE_EPIC_LABEL })).toBeDisabled()
    expect(screen.getByText(NO_EPIC_TYPE_REASON)).toBeInTheDocument()
  })

  it('not opted in: nothing at all, not even a stored link', async () => {
    status = {
      ...STATUS,
      enabled: false,
      links: [
        { membership_key: KEY_A, jira_key: 'QA-12', jira_url: null, cluster_label: null, created_at: null, created_by_name: null },
      ],
    }
    const { container } = renderActions([cluster(), cluster({ key: KEY_B, membershipKey: KEY_B })])
    await settle()
    expect(container.querySelector('[data-cluster-epic]')).toBeNull()
    expect(screen.queryByText(/Epic QA-12/)).toBeNull()
  })

  it('below the recurrence rule (co_failure_runs < min_co_failure_runs, or unknown): nothing', async () => {
    const { container } = renderActions([
      cluster({ coFailureRuns: 2 }),
      cluster({ key: KEY_B, membershipKey: KEY_B, coFailureRuns: null }),
    ])
    await settle()
    expect(container.querySelector('[data-cluster-epic]')).toBeNull()
  })

  it('exactly at the rule (3 of 3) the action is offered', async () => {
    renderActions([cluster({ coFailureRuns: 3 })])
    await settle()
    expect(screen.getByRole('button', { name: CREATE_EPIC_LABEL })).toBeEnabled()
  })

  it.each(['VIEWER', 'TESTER'])('%s: no Create, and no disabled button either', async (role) => {
    setRole(role)
    status = { ...STATUS, available: false, reason: 'Jira is offline.' }
    const { container } = renderActions([cluster()])
    await settle()
    expect(container.querySelector('[data-cluster-epic]')).toBeNull()
  })

  it('QA lead and admin are above QA engineer: offered', async () => {
    setRole('QA_LEAD')
    renderActions([cluster()])
    await settle()
    expect(screen.getByRole('button', { name: CREATE_EPIC_LABEL })).toBeEnabled()
  })

  it('a cluster with no membership key (member-less) cannot carry a link: nothing', async () => {
    const { container } = renderActions([cluster({ key: 'rank:sfc_009', membershipKey: null })])
    await settle()
    expect(container.querySelector('[data-cluster-epic]')).toBeNull()
  })

  it('a failed status request shows nothing (no action offered on an unknown opt-in)', async () => {
    status = Object.assign(new Error('boom'), { response: { status: 500, data: {} } })
    const { container } = renderActions([cluster()])
    await settle()
    expect(container.querySelector('[data-cluster-epic]')).toBeNull()
  })

  it('after a create the status is revalidated; once the form is closed the row shows the new link', async () => {
    http.post.mockResolvedValue({
      data: { membership_key: KEY_A, jira_key: 'QA-31', jira_url: 'https://jira.example/browse/QA-31', created: true },
    })
    http.get.mockImplementation((url: string) => {
      if (url === STATUS_URL) return Promise.resolve({ data: status })
      return Promise.resolve({
        data: {
          membership_key: KEY_A,
          summary: 'Recurring failure cluster',
          description_text: 'd',
          labels: ['testlookup', 'systemic-cluster-epic'],
          jira_project_key: 'QA',
          member_count: 14,
          co_failure_runs: 9,
          cluster_label: 'checkout tests fail together',
        },
      })
    })
    renderActions([cluster()])
    await settle()
    fireEvent.click(screen.getByRole('button', { name: CREATE_EPIC_LABEL }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Jira project key' })).toHaveValue('QA'))
    status = {
      ...STATUS,
      links: [
        {
          membership_key: KEY_A,
          jira_key: 'QA-31',
          jira_url: 'https://jira.example/browse/QA-31',
          cluster_label: null,
          created_at: null,
          created_by_name: null,
        },
      ],
    }
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Filed epic QA-31 in Jira.'))
    await waitFor(() => expect(statusCalls()).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.getByRole('link', { name: 'Epic QA-31' })).toHaveAttribute('href', 'https://jira.example/browse/QA-31')
    expect(screen.queryByRole('button', { name: CREATE_EPIC_LABEL })).toBeNull()
  })
})
