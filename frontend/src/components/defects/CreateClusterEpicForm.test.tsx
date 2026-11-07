/**
 * STRAT-03 — the cluster epic form: preview first, editable key + summary, Create -> the contract's POST body,
 * the 409 outcome-unknown retry, inline errors, the link on success. The real hook + service run; only the
 * HTTP client is mocked, so the URLs and bodies asserted are the ones the browser would send.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ClusterEpicPreview } from '@/services/defectJiraService'
import CreateClusterEpicForm from './CreateClusterEpicForm'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/services/api', () => ({ api: http }))
const toastError = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: toastError, success: vi.fn() }) }))

const KEY = 'a'.repeat(32)
const PREVIEW_URL = `/api/v1/projects/p1/systemic-clusters/${KEY}/epic/preview`
const POST_URL = `/api/v1/projects/p1/systemic-clusters/${KEY}/epic`

const PREVIEW: ClusterEpicPreview = {
  membership_key: KEY,
  summary: '[TestLookup] Recurring failure cluster: checkout tests fail together',
  description_text: 'Cause family: external_dependency\nMembers: test_pay, test_cart\nCo-failure runs: 9',
  labels: ['testlookup', 'systemic-cluster-epic'],
  jira_project_key: 'QA',
  member_count: 14,
  co_failure_runs: 9,
  cluster_label: 'checkout tests fail together',
}

const httpError = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })

let previewAnswer: unknown

function renderForm(props: { onClose?: () => void; onCreated?: () => void } = {}) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>{children}</SWRConfig>
  )
  return render(
    <CreateClusterEpicForm
      projectId="p1"
      membershipKey={KEY}
      clusterLabel="checkout tests fail together"
      onClose={props.onClose ?? vi.fn()}
      onCreated={props.onCreated}
    />,
    { wrapper },
  )
}

const keyField = () => screen.getByRole('textbox', { name: 'Jira project key' })
const summaryField = () => screen.getByRole('textbox', { name: 'Summary' })

beforeEach(() => {
  previewAnswer = PREVIEW
  http.get.mockReset()
  http.post.mockReset()
  toastError.mockReset()
  http.get.mockImplementation((url: string) =>
    url === PREVIEW_URL
      ? previewAnswer instanceof Error
        ? Promise.reject(previewAnswer)
        : Promise.resolve({ data: previewAnswer })
      : Promise.reject(httpError(500, 'no fixture')),
  )
})

describe('CreateClusterEpicForm', () => {
  it('shows the preview first: key and summary prefilled and editable, description and labels read-only; nothing posted', async () => {
    renderForm()
    expect(screen.getByRole('region', { name: 'Create epic for this cluster' })).toBeInTheDocument()
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    expect(http.get).toHaveBeenCalledWith(PREVIEW_URL, { suppressToast: true })
    expect(summaryField()).toHaveValue(PREVIEW.summary)
    expect(summaryField()).toHaveAttribute('maxLength', '255')
    expect(keyField()).toHaveAttribute('maxLength', '50')
    expect(screen.getByLabelText('Prefilled description')).toHaveTextContent('Co-failure runs: 9')
    expect(screen.getByText('testlookup')).toBeInTheDocument()
    expect(screen.getByText('systemic-cluster-epic')).toBeInTheDocument()
    expect(screen.getByText(/14 tests · failed together in 9 runs/)).toHaveTextContent('Nothing is filed until you click Create epic.')
    expect(http.post).not.toHaveBeenCalled()
  })

  it('Create sends the edited key and summary with confirm_not_filed false; success shows the link and tells the host', async () => {
    const onCreated = vi.fn()
    http.post.mockResolvedValue({
      data: { membership_key: KEY, jira_key: 'OPS-42', jira_url: 'https://jira.example/browse/OPS-42', created: true },
    })
    renderForm({ onCreated })
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    fireEvent.change(keyField(), { target: { value: ' OPS ' } })
    fireEvent.change(summaryField(), { target: { value: 'Checkout cluster keeps failing' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Filed epic OPS-42 in Jira.'))
    expect(http.post).toHaveBeenCalledTimes(1)
    expect(http.post).toHaveBeenCalledWith(
      POST_URL,
      { jira_project_key: 'OPS', summary: 'Checkout cluster keeps failing', confirm_not_filed: false },
      { suppressToast: true },
    )
    const link = screen.getByRole('link', { name: 'OPS-42' })
    expect(link).toHaveAttribute('href', 'https://jira.example/browse/OPS-42')
    expect(link).toHaveAttribute('target', '_blank')
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ jira_key: 'OPS-42', created: true }))
    // Filed: no second Create, only Close.
    expect(screen.queryByRole('button', { name: 'Create epic' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('an emptied key and summary are sent as null (the server falls back to the project / config key)', async () => {
    http.post.mockResolvedValue({ data: { membership_key: KEY, jira_key: 'QA-1', jira_url: null, created: true } })
    renderForm()
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    fireEvent.change(keyField(), { target: { value: '' } })
    fireEvent.change(summaryField(), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Filed epic QA-1 in Jira.'))
    expect(http.post.mock.calls[0][1]).toEqual({ jira_project_key: null, summary: null, confirm_not_filed: false })
    // No URL: the key is shown, not linked.
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('created:false (a stored link) says nothing new was filed', async () => {
    http.post.mockResolvedValue({
      data: { membership_key: KEY, jira_key: 'QA-7', jira_url: 'https://jira.example/browse/QA-7', created: false },
    })
    renderForm()
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('This cluster already has epic QA-7; nothing new was filed.'),
    )
  })

  it('409 jira_outcome_unknown: names the label; "I checked Jira, file it" retries with confirm_not_filed true', async () => {
    http.post
      .mockRejectedValueOnce(
        httpError(409, {
          code: 'jira_outcome_unknown',
          message: 'An earlier request may already have filed this epic.',
          jira_label: 'testlookup-sig-abc123',
        }),
      )
      .mockResolvedValueOnce({
        data: { membership_key: KEY, jira_key: 'QA-9', jira_url: 'https://jira.example/browse/QA-9', created: true },
      })
    renderForm()
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Jira may already have this epic')
    expect(alert).toHaveTextContent('An earlier request may already have filed this epic.')
    expect(alert).toHaveTextContent('testlookup-sig-abc123')
    expect(screen.queryByRole('status')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'I checked Jira, file it' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Filed epic QA-9 in Jira.'))
    expect(http.post.mock.calls[1][1]).toEqual({
      jira_project_key: 'QA',
      summary: PREVIEW.summary,
      confirm_not_filed: true,
    })
    expect(screen.queryByText('Jira may already have this epic')).toBeNull()
  })

  it('any other refusal is shown inline with the server sentence, no toast; the form stays open to retry', async () => {
    http.post.mockRejectedValue(httpError(503, 'Jira is offline: AI_OFFLINE_MODE is on.'))
    renderForm()
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Jira is offline: AI_OFFLINE_MODE is on.'))
    expect(toastError).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Create epic' })).toBeEnabled()
  })

  it.each([
    ['422 no_epic_type', httpError(422, { code: 'no_epic_type', message: 'Jira offers no Epic issue type, so an epic cannot be filed.' }), 'Jira offers no Epic issue type, so an epic cannot be filed.'],
    ['502 issue types unreadable', httpError(502, "Could not read Jira's issue types (unreachable)."), "Could not read Jira's issue types (unreachable)."],
  ])('a POST refusal (%s) shows its message inline, never the code', async (_name, failure, words) => {
    http.post.mockRejectedValue(failure)
    renderForm()
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    fireEvent.click(screen.getByRole('button', { name: 'Create epic' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(words)
    expect(alert.textContent).not.toMatch(/no_epic_type|code/)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('a preview refusal (422 not_recurring) is shown inline and Create stays disabled', async () => {
    previewAnswer = httpError(422, { code: 'not_recurring', message: 'This cluster failed together in fewer than 3 runs.' })
    renderForm()
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('This cluster failed together in fewer than 3 runs.'),
    )
    expect(screen.getByRole('button', { name: 'Create epic' })).toBeDisabled()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('Cancel closes without posting', async () => {
    const onClose = vi.fn()
    renderForm({ onClose })
    await waitFor(() => expect(keyField()).toHaveValue('QA'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    })
    expect(onClose).toHaveBeenCalled()
    expect(http.post).not.toHaveBeenCalled()
  })
})
