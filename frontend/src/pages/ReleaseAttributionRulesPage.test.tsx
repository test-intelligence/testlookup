/**
 * W11 REL-09: the release attribution rules page.
 *
 * The REAL service and SWR hooks run (under an isolated SWRConfig); only the
 * HTTP helpers (`services/http`) are mocked, so every assertion on a request is
 * on the URL, body and query the browser would send.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { AttributionPreviewResponse, AttributionRule } from '@/types/attributionRules'
import ReleaseAttributionRulesPage from './ReleaseAttributionRulesPage'

const { http, storeState, perms } = vi.hoisted(() => ({
  http: {
    getData: vi.fn(),
    postData: vi.fn(),
    putData: vi.fn(),
    deleteData: vi.fn(),
  },
  storeState: {
    activeProjectId: 'proj-1' as string | null,
    projects: [{ id: 'proj-1', name: 'Checkout' }],
    setActiveProject: vi.fn(),
  },
  perms: { isQaLead: true },
}))

vi.mock('@/services/http', () => ({
  getData: (...a: unknown[]) => http.getData(...a),
  postData: (...a: unknown[]) => http.postData(...a),
  putData: (...a: unknown[]) => http.putData(...a),
  deleteData: (...a: unknown[]) => http.deleteData(...a),
}))

vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (sel: (s: typeof storeState) => unknown) => sel(storeState),
  }
})

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isQaLead: perms.isQaLead, canAccessManagement: perms.isQaLead }),
}))

// The top bar's cached release list (no request of the page's own).
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: [{ id: 'rel-1', name: '2.5' }, { id: 'rel-2', name: '2026.11' }] } }),
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const BASE = '/api/v1/projects/proj-1/attribution-rules'

function rule(over: Partial<AttributionRule>): AttributionRule {
  return {
    id: 'r-x',
    project_id: 'proj-1',
    name: 'Rule',
    match_field: 'branch',
    match_pattern: 'release/*',
    target_release_name: '2.5',
    priority: 100,
    is_enabled: true,
    created_at: '2026-10-01T00:00:00Z',
    ...over,
  }
}

// Deliberately NOT sorted by priority: the page shows the server's order.
const RULES = [
  rule({ id: 'r-1', name: 'Hotfix branches', match_field: 'branch', match_pattern: 'hotfix/*', target_release_name: '2.4.1', priority: 10 }),
  rule({ id: 'r-2', name: 'RC tags', match_field: 'tag', match_pattern: 'rc-*', target_release_name: '2.5', priority: 5, is_enabled: false }),
  rule({ id: 'r-3', name: 'Staging', match_field: 'environment', match_pattern: 'staging', target_release_name: '2026.11', priority: 50 }),
]

const PREVIEW: AttributionPreviewResponse = {
  considered: 200,
  matched: 12,
  field_present: 183,
  shadowed: 3,
  explicit: 2,
  target_exists: false,
  unattributed_recent: 4,
  sample: [
    {
      run_id: 'run-a',
      build_number: '#981',
      branch: '<script>alert(1)</script>',
      environment: 'staging',
      tags: ['rc-1', '<b>x</b>'],
      created_at: '2026-10-20T10:00:00Z',
      current_release_name: null,
      would_land: 'earlier_rule',
    },
    {
      run_id: 'run-b',
      build_number: '#982',
      branch: 'release/2.6',
      environment: null,
      tags: [],
      created_at: '2026-10-20T11:00:00Z',
      current_release_name: '2.5.0',
      would_land: 'explicit',
    },
  ],
}

function answerReads({ rules = RULES, failRules = false }: { rules?: AttributionRule[]; failRules?: boolean } = {}) {
  http.getData.mockImplementation(async (url: string) => {
    if (url === BASE) {
      if (failRules) throw Object.assign(new Error('boom'), { response: { status: 500, data: { detail: 'down' } } })
      return { items: rules, total: rules.length }
    }
    if (url === `${BASE}/fields`) {
      return {
        fields: [
          { value: 'branch', label: 'Branch', glob: true, help: "The run's git branch. Wildcards: * and ?, e.g. release/2.5*." },
          { value: 'build_number', label: 'Build number', glob: true, help: 'Build help.' },
          { value: 'environment', label: 'Environment', glob: false, help: 'Environment help.' },
          { value: 'tag', label: 'Tag', glob: true, help: 'Tag help.' },
        ],
      }
    }
    throw new Error(`unexpected GET ${url}`)
  })
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/releases/attribution-rules']}>
        <ReleaseAttributionRulesPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

async function rows() {
  const table = await screen.findByRole('table', { name: 'Attribution rules' })
  return within(table).getAllByRole('row').slice(1)
}

function fillNewRule() {
  fireEvent.click(screen.getByRole('button', { name: 'New rule' }))
  const form = screen.getByRole('region', { name: 'New attribution rule' })
  fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Release line' } })
  fireEvent.change(within(form).getByLabelText('Pattern'), { target: { value: 'release/2.5*' } })
  fireEvent.change(within(form).getByLabelText('Release'), { target: { value: '2.6' } })
  return form
}

beforeEach(() => {
  storeState.activeProjectId = 'proj-1'
  perms.isQaLead = true
  answerReads()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('ReleaseAttributionRulesPage', () => {
  it('lists the rules in the server order with their evaluation order 1..n', async () => {
    renderPage()
    const body = await rows()
    expect(body.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual(['1', '2', '3'])
    expect(body.map((r) => within(r).getAllByRole('cell')[1].textContent)).toEqual(['Hotfix branches', 'RC tags', 'Staging'])
    expect(within(body[0]).getByText('hotfix/*')).toHaveClass('font-mono')
    expect(within(body[1]).getAllByRole('cell')[2]).toHaveTextContent('Tag rc-*')
    // A target no release carries yet is created by the first matching run.
    expect(within(body[0]).getByText('(created on first match)')).toBeInTheDocument()
    expect(within(body[1]).queryByText('(created on first match)')).toBeNull()
    // Reads on load: the rules and the fields, nothing else.
    expect(http.getData.mock.calls.map((c) => c[0]).sort()).toEqual([BASE, `${BASE}/fields`])
    expect(screen.getByText(/When a run arrives, it is linked to the first of these that applies/)).toBeInTheDocument()
    expect(screen.getByText('3. These rules, top to bottom')).toBeInTheDocument()
  })

  it('gives a QA engineer the explanation and the table only', async () => {
    perms.isQaLead = false
    renderPage()
    const body = await rows()
    expect(screen.queryByRole('button', { name: 'New rule' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Edit rule/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Delete rule/ })).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(within(body[0]).getAllByRole('cell')[5]).toHaveTextContent('Yes')
    expect(within(body[1]).getAllByRole('cell')[5]).toHaveTextContent('No')
  })

  it('says so when the project has no rules', async () => {
    answerReads({ rules: [] })
    renderPage()
    expect(
      await screen.findByText('No rules yet. Runs that name no release land in the cutoff window or the active release.'),
    ).toBeInTheDocument()
  })

  it('previews the form with detail=true, saves nothing, and renders the answer as text', async () => {
    http.postData.mockResolvedValue(PREVIEW)
    const { container } = renderPage()
    await rows()
    const form = fillNewRule()
    // The field's help comes from the fields route.
    expect(within(form).getByText("The run's git branch. Wildcards: * and ?, e.g. release/2.5*.")).toBeInTheDocument()

    fireEvent.click(within(form).getByRole('button', { name: 'Preview' }))

    expect(await within(form).findByText('Matches 12 of the last 200 runs (183 carry a branch).')).toBeInTheDocument()
    expect(http.postData).toHaveBeenCalledTimes(1)
    expect(http.postData).toHaveBeenCalledWith(
      `${BASE}/preview`,
      {
        name: 'Release line',
        match_field: 'branch',
        match_pattern: 'release/2.5*',
        target_release_name: '2.6',
        priority: 100,
        is_enabled: true,
      },
      { params: { detail: true }, suppressToast: true },
    )
    expect(within(form).getByText('3 of them already match an earlier rule, which wins.')).toBeInTheDocument()
    expect(within(form).getByText('2 named their release outright; a rule does not move them.')).toBeInTheDocument()
    expect(within(form).getByText('No release named "2.6" yet: the first matching run creates it.')).toBeInTheDocument()
    // The samples: branches and tags are text, never markup.
    expect(within(form).getByText('<script>alert(1)</script>')).toBeInTheDocument()
    expect(within(form).getByText('rc-1, <b>x</b>')).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(within(form).getByText('earlier rule')).toBeInTheDocument()
    expect(within(form).getByText('stays (named)')).toBeInTheDocument()
    expect(within(form).getByText('Unattributed')).toBeInTheDocument()
    expect(within(form).getByText('2.5.0')).toBeInTheDocument()
    // Nothing was written.
    expect(http.putData).not.toHaveBeenCalled()
    expect(http.deleteData).not.toHaveBeenCalled()
    // The footer counts the Unattributed runs among them, with a link that filters Runs.
    const footer = container.querySelector('[data-attribution-footer]') as HTMLElement
    expect(footer).toHaveTextContent('4 of the last 200 runs have no release (Unattributed).')
    expect(within(footer).getByRole('link', { name: 'Show them in Runs' })).toHaveAttribute('href', '/runs?release=unattributed')
  })

  it('without a preview the footer points at the Runs filter, with no extra read', async () => {
    const { container } = renderPage()
    await rows()
    expect(container.querySelector('[data-attribution-footer]')).toHaveTextContent(
      'Runs with no release are listed under Runs › Release › Unattributed.',
    )
    expect(http.postData).not.toHaveBeenCalled()
  })

  it('checks the form before sending anything', async () => {
    renderPage()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'New rule' }))
    const form = screen.getByRole('region', { name: 'New attribution rule' })
    fireEvent.click(within(form).getByRole('button', { name: 'Preview' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('Name the rule.')
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('Name the rule.')
    expect(http.postData).not.toHaveBeenCalled()
  })

  it('Save posts the rule, closes the form and re-reads the list', async () => {
    http.postData.mockResolvedValue(rule({ id: 'r-new', name: 'Release line' }))
    renderPage()
    await rows()
    const form = fillNewRule()

    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.queryByRole('region', { name: 'New attribution rule' })).toBeNull())
    expect(http.postData).toHaveBeenCalledWith(
      BASE,
      expect.objectContaining({ name: 'Release line', match_pattern: 'release/2.5*', target_release_name: '2.6' }),
      { suppressToast: true },
    )
    await waitFor(() => expect(http.getData.mock.calls.filter((c) => c[0] === BASE).length).toBeGreaterThanOrEqual(2))
  })

  it("renders the server's 422 in the form", async () => {
    http.postData.mockRejectedValue({
      response: { status: 422, data: { detail: "match_field must be one of ['branch', 'build_number', 'environment', 'tag']" } },
    })
    renderPage()
    await rows()
    const form = fillNewRule()

    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      "match_field must be one of ['branch', 'build_number', 'environment', 'tag']",
    )
    expect(screen.getByRole('region', { name: 'New attribution rule' })).toBeInTheDocument()
  })

  it('Edit opens the rule in the form and previews it with its id', async () => {
    http.postData.mockResolvedValue(PREVIEW)
    renderPage()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'Edit rule RC tags' }))
    const form = screen.getByRole('region', { name: 'Edit attribution rule' })
    expect(within(form).getByLabelText('Name')).toHaveValue('RC tags')
    expect(within(form).getByLabelText('Field')).toHaveValue('tag')
    expect(within(form).getByLabelText('Priority')).toHaveValue(5)
    expect(within(form).getByLabelText('Enabled')).not.toBeChecked()

    fireEvent.click(within(form).getByRole('button', { name: 'Preview' }))

    await within(form).findByText('Matches 12 of the last 200 runs (183 carry a tag).')
    expect(http.postData.mock.calls[0][2]).toEqual({ params: { detail: true, rule_id: 'r-2' }, suppressToast: true })
  })

  it('an edit saves through PUT', async () => {
    http.putData.mockResolvedValue(rule({ id: 'r-3' }))
    renderPage()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'Edit rule Staging' }))
    const form = screen.getByRole('region', { name: 'Edit attribution rule' })
    fireEvent.change(within(form).getByLabelText('Priority'), { target: { value: '60' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(http.putData).toHaveBeenCalledTimes(1))
    expect(http.putData.mock.calls[0][0]).toBe(`${BASE}/r-3`)
    expect(http.putData.mock.calls[0][1]).toEqual(expect.objectContaining({ priority: 60, match_field: 'environment' }))
    expect(http.postData).not.toHaveBeenCalled()
  })

  it('the Enabled toggle sends only is_enabled', async () => {
    http.putData.mockResolvedValue(rule({ id: 'r-1', is_enabled: false }))
    renderPage()
    await rows()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled: Hotfix branches' }))
    await waitFor(() => expect(http.putData).toHaveBeenCalledWith(`${BASE}/r-1`, { is_enabled: false }, { suppressToast: true }))
  })

  it('Delete asks in the row and sends DELETE only on confirm', async () => {
    http.deleteData.mockResolvedValue(undefined)
    renderPage()
    await rows()
    fireEvent.click(screen.getByRole('button', { name: 'Delete rule Hotfix branches' }))

    const question = screen.getByRole('group', { name: /Delete rule/ })
    expect(question).toHaveTextContent('Delete rule "Hotfix branches"? Runs it already matched stay where they are.')
    expect(http.deleteData).not.toHaveBeenCalled()

    fireEvent.click(within(question).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('group', { name: /Delete rule/ })).toBeNull()
    expect(http.deleteData).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Delete rule Hotfix branches' }))
    fireEvent.click(within(screen.getByRole('group', { name: /Delete rule/ })).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(http.deleteData).toHaveBeenCalledWith(`${BASE}/r-1`, { suppressToast: true }))
  })

  it('a failed list read says so, never "no rules"', async () => {
    answerReads({ failRules: true })
    renderPage()
    expect(await screen.findByText('Could not load attribution rules.')).toBeInTheDocument()
    expect(screen.getByTestId('attribution-rules-unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/No rules yet/)).toBeNull()
  })

  it('asks for one project in All Projects, and reads nothing', async () => {
    storeState.activeProjectId = ALL_PROJECTS_ID
    renderPage()
    expect(await screen.findByText('Select a project')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
    expect(http.getData).not.toHaveBeenCalled()
  })

  it('opens no dialog: every form and question is in the page', async () => {
    http.postData.mockResolvedValue(PREVIEW)
    renderPage()
    await rows()
    fillNewRule()
    fireEvent.click(screen.getByRole('button', { name: 'Delete rule Staging' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })
})
