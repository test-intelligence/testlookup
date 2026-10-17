/**
 * PolicyEditorPage — release-gate policy authoring.
 *
 * This page had **zero** test coverage (backlog: "zero-coverage surfaces the
 * loop never reached"), and writing the coverage immediately found a defect:
 * **you could not create a policy at all.**
 *
 * `/policies/new` was registered as a STATIC route beside
 * `policies/:policyId`. React Router ranks a static segment above a dynamic
 * one, so that route won and `useParams().policyId` was `undefined`. The page
 * derives `listMode = isNew && !policyId`, which is then true — so
 * `/policies/new` rendered the LIST. The "New Policy" button on that list
 * navigates to `/policies/new`, i.e. straight back to the list it was clicked
 * from. The editor's own `isNew ? 'New Policy' : ...` heading was unreachable
 * code.
 *
 * Measured before the fix, rendering the real route table at `/policies/new`::
 *
 *     headings: ["Release Gate Policies"]   buttons: ["New Policy"]  inputs: []
 *
 * and after::
 *
 *     headings: ["New Policy", "Metadata", "Thresholds", ...]
 *     buttons:  ["Back to policies", "Save Draft", "Simulate"]
 *
 * The routes are therefore exercised through a real `<Routes>` here rather
 * than by mocking `useParams`: mocking the param would have hard-coded the
 * very thing that was broken, and the tests would have passed against the bug.
 *
 * The rest of the file guards the save-time validations, because what this
 * page writes decides whether a release reads GO / CONDITIONAL_GO / NO_GO, and
 * a broken validation fails *open* — it saves the incoherent policy.
 *
 * Threshold semantics read backwards until you know the composite is a RISK
 * score (higher = worse). From `criticality_service.evaluate`:
 *
 *     composite >= no_go_threshold  -> NO_GO
 *     composite >= go_threshold     -> CONDITIONAL_GO
 *     composite <  go_threshold     -> GO
 *
 * so `go_threshold` (20) sits BELOW `no_go_threshold` (55). The hint text is
 * pinned against those boundaries: the NO_GO hint read "Composite above this
 * → NO_GO", which is wrong at exactly the threshold — the backend returns
 * NO_GO there, so an operator setting 55 was told 55 would not trip it.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PolicyEditorPage from './PolicyEditorPage'
import { useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'

const mockCreatePolicy = vi.fn()
const mockPublishPolicy = vi.fn()
const mockToastError = vi.fn()
const mockToastSuccess = vi.fn()
const mockAppMutate = vi.fn()
const mockDeactivatePolicy = vi.fn()
const mockRefreshPolicies = vi.fn()
/** Every `usePolicies(enabled)` call, so a test can see the list asked for after a refused publish;
 *  `policies` is what the list shows (empty unless a test lists some). */
const listHook = vi.hoisted(() => ({ enabledCalls: [] as boolean[], policies: [] as unknown[] }))

vi.mock('react-hot-toast', () => ({
  default: { error: (m: string) => mockToastError(m), success: (m: string) => mockToastSuccess(m) },
}))

vi.mock('@/hooks/usePolicyEditor', () => ({
  usePolicies: (enabled: boolean) => {
    listHook.enabledCalls.push(enabled)
    return { policies: listHook.policies, isLoading: false, isError: false, mutate: mockRefreshPolicies }
  },
  usePolicy: () => ({ policy: null, isLoading: false, isError: false }),
}))

vi.mock('@/utils/swrCacheMutate', () => ({
  appMutate: (...a: unknown[]) => mockAppMutate(...a),
}))

vi.mock('../services/policyService', async () => {
  const actual = await vi.importActual<typeof import('../services/policyService')>(
    '../services/policyService',
  )
  return {
    ...actual,
    createPolicy: (...a: unknown[]) => mockCreatePolicy(...a),
    updatePolicy: vi.fn(),
    publishPolicy: (...a: unknown[]) => mockPublishPolicy(...a),
    deactivatePolicy: (...a: unknown[]) => mockDeactivatePolicy(...a),
    simulatePolicy: vi.fn(),
  }
})

/**
 * Mirrors the App.tsx registration for the policy routes. If App.tsx regains a
 * static `policies/new` entry, this table must be updated with it — and the
 * first test below then fails, which is the point.
 */
function renderAt(path: string) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="policies" element={<PolicyEditorPage />} />
          <Route path="policies/:policyId" element={<PolicyEditorPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const nameBox = () => screen.getByPlaceholderText(/policy name/i)
const saveButton = () => screen.getByRole('button', { name: /save draft/i })

/**
 * Numeric inputs, in DOM order. Selecting by displayed value is not safe here:
 * the defaults contain `20` twice (GO threshold and a hard cap) and `90`
 * twice, so `getByDisplayValue` throws on multiple matches.
 *
 * Order, verified by rendering the page: [0] GO threshold, [1] NO_GO
 * threshold, [2] pass-rate minimum, [3] hard-floor factor, [4..6] bands. The
 * hard caps and the dimension weights are in collapsed disclosures (UX
 * redesign P5), so they are not rendered until opened.
 */
const numbers = () => screen.getAllByRole('spinbutton') as HTMLInputElement[]
const goThreshold = () => numbers()[0]

const DIMENSION_LABELS = [
  'User Impact', 'Env Sensitivity', 'Reproducibility', 'Regression Likely',
  'Hist. Recurrence', 'Blast Radius', 'Diagnosis Conf',
]
const disclosure = (title: string) => screen.getByRole('button', { name: new RegExp(`^${title}`) })
/** The seven weights, by their labels, after opening their disclosure. */
function weightInputs(): HTMLInputElement[] {
  const toggle = disclosure('Dimension weights')
  if (toggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(toggle)
  return DIMENSION_LABELS.map(label => screen.getByLabelText(label) as HTMLInputElement)
}

function nameIt(value = 'Release gate') {
  fireEvent.change(nameBox(), { target: { value } })
}

beforeEach(() => {
  vi.clearAllMocks()
  listHook.enabledCalls.length = 0
  listHook.policies = []
  // Every write on this page is ADMIN on the API; these tests author policies.
  // What a QA lead sees is PolicyEditorPage.roles.test.tsx.
  useAuthStore.setState({ user: { id: 'admin', role: 'ADMIN' } as never })
})

describe('creating a policy is reachable', () => {
  it('renders the editor at /policies/new, not the list', () => {
    renderAt('/policies/new')

    // The bug: this route rendered "Release Gate Policies" with no inputs, so
    // the New Policy button was a loop back to its own list.
    expect(screen.getByRole('heading', { name: /^new policy$/i })).toBeInTheDocument()
    expect(nameBox()).toBeInTheDocument()
    expect(saveButton()).toBeInTheDocument()
  })

  it('still renders the list at /policies', () => {
    renderAt('/policies')

    expect(screen.getByRole('heading', { name: /release gate policies/i })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/policy name/i)).not.toBeInTheDocument()
  })

  it('still renders the editor for an existing policy id', () => {
    renderAt('/policies/abc123')

    // Distinct from the new-policy heading, so the two branches cannot be
    // confused by a selector that matches both.
    expect(screen.getByRole('heading', { name: /^edit:/i })).toBeInTheDocument()
    expect(nameBox()).toBeInTheDocument()
  })
})

describe('threshold semantics are described accurately', () => {
  it('says NO_GO triggers AT the threshold, not only above it', () => {
    renderAt('/policies/new')
    expect(screen.getByText(/composite at or above this → NO_GO/i)).toBeInTheDocument()
  })

  it('says GO applies strictly below the GO threshold', () => {
    renderAt('/policies/new')
    // `composite < go_threshold -> GO`; AT the value it is CONDITIONAL_GO, so
    // "below this" is the correct word for this one.
    expect(screen.getByText(/composite below this → GO/i)).toBeInTheDocument()
  })

  it('ships defaults that satisfy its own ordering rule', () => {
    renderAt('/policies/new')
    // 20 and 55: go below no_go. A default set that violates the validation
    // would make every fresh policy unsaveable.
    expect(goThreshold().value).toBe('20')
    expect(numbers()[1].value).toBe('55')
    expect(Number(goThreshold().value)).toBeLessThan(Number(numbers()[1].value))
  })
})

describe('save-time validation refuses incoherent policies', () => {
  it('requires a name', () => {
    renderAt('/policies/new')

    fireEvent.click(saveButton())

    expect(mockToastError).toHaveBeenCalledWith(expect.stringMatching(/name is required/i))
    expect(mockCreatePolicy).not.toHaveBeenCalled()
  })

  it('refuses a GO threshold that is not below the NO_GO threshold', async () => {
    renderAt('/policies/new')
    nameIt()

    // Inverted, the CONDITIONAL_GO band collapses and the two branches
    // overlap, so the verdict depends on evaluation order rather than policy.
    fireEvent.change(goThreshold(), { target: { value: '90' } })
    fireEvent.click(saveButton())

    await waitFor(() =>
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringMatching(/GO threshold must be less than NO_GO threshold/i),
      ),
    )
    expect(mockCreatePolicy).not.toHaveBeenCalled()
  })

  it('refuses dimension weights that do not sum to 1.0', async () => {
    renderAt('/policies/new')
    nameIt()

    // Weights form a weighted mean; off-sum silently rescales the composite
    // and shifts every verdict.
    // Must be an actual WEIGHT, not merely the first fractional input on the
    // page — that one is the hard-floor factor (0.7), and changing it leaves
    // the weight sum at 1.0, so the test would pass while asserting nothing.
    const weights = weightInputs()
    expect(weights.reduce((sum, el) => sum + Number(el.value), 0)).toBeCloseTo(1.0, 2)
    fireEvent.change(weights[0], { target: { value: '0.99' } })
    fireEvent.click(saveButton())

    await waitFor(() =>
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringMatching(/weights must sum to 1\.0/i),
      ),
    )
    expect(mockCreatePolicy).not.toHaveBeenCalled()
  })
})

describe('the editor layout (UX redesign P5 item 6)', () => {
  it('keeps the decision rules open and collapses the tuning behind them', () => {
    renderAt('/policies/new')

    for (const heading of ['Metadata', 'Thresholds', 'Pass-Rate Bands', 'Rules']) {
      expect(screen.getByRole('heading', { name: heading, level: 2 })).toBeInTheDocument()
    }
    // The old open sections are gone as headings: each is a disclosure now.
    for (const old of ['Hard Caps', 'Failure-Kind Weighting', 'Dimension Weights']) {
      expect(screen.queryByRole('heading', { name: old })).not.toBeInTheDocument()
    }
    for (const title of ['Hard caps', 'Failure-kind weighting', 'Dimension weights']) {
      expect(disclosure(title)).toHaveAttribute('aria-expanded', 'false')
    }
    // Collapsed means not rendered: the caps' and weights' fields are absent.
    expect(screen.queryByLabelText('Max P0 defects')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('User Impact')).not.toBeInTheDocument()
    expect(numbers()).toHaveLength(7) // four thresholds, three bands

    fireEvent.click(disclosure('Hard caps'))
    expect(screen.getByLabelText('Max P0 defects')).toHaveValue(0)
  })

  it('puts the open sections before the disclosures, inside the primary content', () => {
    const { container } = renderAt('/policies/new')
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(primary).not.toBeNull()
    const rules = screen.getByRole('heading', { name: 'Rules', level: 2 })
    const firstDisclosure = container.querySelector('[data-disclosure]') as HTMLElement
    expect(primary.contains(rules)).toBe(true)
    expect(primary.contains(firstDisclosure)).toBe(true)
    expect(rules.compareDocumentPosition(firstDisclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('states each collapsed section in its summary line', () => {
    renderAt('/policies/new')
    expect(disclosure('Hard caps')).toHaveTextContent('P0 defects ≤ 0 · flaky ≤ 10 · new failures (24h) ≤ 20')
    expect(disclosure('Failure-kind weighting')).toHaveTextContent('Off (opt-in)')
    expect(disclosure('Dimension weights')).toHaveTextContent('Sum: 1.00')
    expect(disclosure('Dimension weights')).not.toHaveTextContent('must be 1.00')
  })

  it('says an off-sum weight set is invalid while its section is closed', () => {
    renderAt('/policies/new')
    fireEvent.change(weightInputs()[0], { target: { value: '0.5' } })
    fireEvent.click(disclosure('Dimension weights')) // close it again
    expect(screen.queryByLabelText('User Impact')).not.toBeInTheDocument()
    expect(disclosure('Dimension weights')).toHaveTextContent('Sum: 1.25 (must be 1.00)')
  })

  it('puts the simulator in its own right-hand column at >= 1280 px, after the form', () => {
    const { container } = renderAt('/policies/new')
    const layout = container.querySelector('[data-policy-layout]') as HTMLElement
    expect(layout.className).toContain('xl:grid-cols-[minmax(0,1fr)_360px]')
    const simulator = container.querySelector('[data-policy-simulator]') as HTMLElement
    expect(simulator.parentElement).toBe(layout)
    expect(simulator.className).toContain('xl:sticky')
    expect(screen.getByRole('heading', { name: 'Policy Simulator' })).toBeInTheDocument()
    const form = container.querySelector('[data-policy-form]') as HTMLElement
    expect(form.compareDocumentPosition(simulator) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('labels every metadata field, two side by side and the description full width', () => {
    renderAt('/policies/new')
    expect(screen.getByLabelText('Name')).toBe(nameBox())
    expect(screen.getByLabelText('Project')).toBeInTheDocument()
    const description = screen.getByLabelText('Description')
    expect(description.tagName).toBe('TEXTAREA')
    // The two short fields share one row; the textarea is outside that grid.
    const row = nameBox().closest('.grid') as HTMLElement
    expect(row.className).toContain('grid-cols-2')
    expect(row.contains(screen.getByLabelText('Project'))).toBe(true)
    expect(row.contains(description)).toBe(false)
  })

  // Browser E2E pass (2026-10-08): the project was a free-text "Project ID"
  // a person had to paste a UUID into.
  it('picks the project from a list, the system default first', () => {
    useProjectStore.setState({
      projects: [
        { id: 'p-1', name: 'Checkout' },
        { id: 'p-2', name: 'Payments' },
      ] as never,
    })
    renderAt('/policies/new')
    const picker = screen.getByLabelText('Project') as HTMLSelectElement
    expect(picker.tagName).toBe('SELECT')
    expect(Array.from(picker.options).map(o => o.textContent)).toEqual([
      'System default (every project)', 'Checkout', 'Payments',
    ])
    expect(picker.value).toBe('')
    fireEvent.change(picker, { target: { value: 'p-2' } })
    expect(picker.value).toBe('p-2')
    expect(screen.getByText('Project: Payments')).toBeInTheDocument()
  })

  it('has a compact header with the help topic, and "Back to policies" in the overflow menu', () => {
    const { container } = renderAt('/policies/new')
    expect(container.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: New Policy' })).toHaveAttribute('data-help-topic', 'releases')
    expect(screen.queryByRole('button', { name: 'Back to policies' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Back to policies' })).toHaveAttribute('href', '/policies')
  })

  it('simulates the policy as edited, not as saved', async () => {
    const { simulatePolicy } = await import('../services/policyService')
    ;(simulatePolicy as ReturnType<typeof vi.fn>).mockResolvedValue({
      original_recommendation: 'NO_GO',
      simulated_recommendation: 'CONDITIONAL_GO',
      original_composite: 61.2,
      simulated_composite: 48.0,
      rule_evaluations: [],
      diff_summary: 'The lower NO_GO threshold changes the verdict.',
    })
    renderAt('/policies/new')
    fireEvent.change(goThreshold(), { target: { value: '15' } })
    fireEvent.change(screen.getByLabelText('Run ID'), { target: { value: 'run-42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Simulate' }))

    await waitFor(() => expect(screen.getByText('The lower NO_GO threshold changes the verdict.')).toBeInTheDocument())
    expect(simulatePolicy).toHaveBeenCalledWith(
      'run-42',
      expect.objectContaining({ thresholds: expect.objectContaining({ go_threshold: 15 }) }),
    )
    expect(screen.getByText('CONDITIONAL_GO')).toBeInTheDocument()
  })
})

describe('the list (UX redesign P5)', () => {
  it('has a compact header with the help topic and New Policy as its one action', () => {
    const { container } = renderAt('/policies')
    expect(container.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: Release Gate Policies' })).toHaveAttribute(
      'data-help-anchor',
      'the-decision-rules-in-order',
    )
    expect(screen.getByRole('button', { name: /new policy/i })).toBeInTheDocument()
  })
})

describe('a valid policy is saved', () => {
  it('creates the policy when every validation passes', async () => {
    mockCreatePolicy.mockResolvedValue({ id: 'pol-1' })
    renderAt('/policies/new')
    nameIt('Nightly gate')

    fireEvent.click(saveButton())

    await waitFor(() => expect(mockCreatePolicy).toHaveBeenCalledTimes(1))
    // The validations must not reject the page's own defaults — a gate that
    // refuses its shipped configuration is worse than no gate.
    expect(mockToastError).not.toHaveBeenCalled()
    expect(mockCreatePolicy).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Nightly gate' }),
    )
  })
})

/**
 * EXEC-05 (owner decision D44): a slowdown blocks a release only through the
 * opt-in "Performance regression" rule. A new policy does not have it; adding
 * it saves a `perf_regression` rule with its thresholds.
 */
describe('the Performance regression rule is opt-in', () => {
  it('is offered, absent from a new policy, and saved with its thresholds when added', async () => {
    mockCreatePolicy.mockResolvedValue({ id: 'pol-1' })
    const { container } = renderAt('/policies/new')
    expect(container.querySelector('[data-policy-rule="perf_regression"]')).toBeNull()
    const add = screen.getByLabelText('Add rule') as HTMLSelectElement
    expect([...add.options].map(o => o.textContent)).toContain('Performance regression')

    fireEvent.change(add, { target: { value: 'perf_regression' } })
    const rule = container.querySelector('[data-policy-rule="perf_regression"]') as HTMLElement
    expect(rule).not.toBeNull()
    expect((screen.getByLabelText('Max slower tests') as HTMLInputElement).value).toBe('0')
    expect((screen.getByLabelText('Min σ above baseline') as HTMLInputElement).value).toBe('3')
    expect((screen.getByLabelText('Suite slower by (×)') as HTMLInputElement).value).toBe('1.5')
    fireEvent.change(screen.getByLabelText('Max slower tests'), { target: { value: '2' } })

    nameIt('Perf gate')
    fireEvent.click(saveButton())
    await waitFor(() => expect(mockCreatePolicy).toHaveBeenCalledTimes(1))
    const created = mockCreatePolicy.mock.calls[0][0] as { rules: { rules: { type: string; enabled: boolean; params: Record<string, unknown> }[] } }
    const [saved] = created.rules.rules.filter(r => r.type === 'perf_regression')
    expect(saved.enabled).toBe(true)
    expect(saved.params).toEqual({ max_spikes: 2, min_sigma: 3, suite_ratio: 1.5, max_suite_regressions: 0, action: 'BLOCK' })
  })
})

/**
 * GATE-03: two admins publishing two drafts of one scope at once. The database
 * now keeps one active policy per scope, and the loser gets a 409 with a
 * sentence. The page showed TWO toasts: the interceptor's (the server's detail)
 * and its own `err.message`, Axios's "Request failed with status code 409".
 * The request now suppresses the interceptor's toast (`policyService`); the
 * page shows one, carrying the detail, and reloads the list so the winner's
 * Active badge is what the reader sees next.
 */
/** The header's Publish, then the strip's Publish (W27b: the question is asked in the page). */
function publishThroughTheStrip() {
  fireEvent.click(screen.getByRole('button', { name: 'Publish' }))
  const asked = document.querySelector('[data-inline-confirm]') as HTMLElement
  fireEvent.click(within(asked).getByRole('button', { name: 'Publish' }))
}

describe('a refused publish', () => {
  const RACE =
    'Another policy was published for this scope at the same moment. Reload the policies and publish again if you still want this version.'

  function refuse(status: number, detail: unknown) {
    mockPublishPolicy.mockRejectedValue(
      Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } }),
    )
  }

  it('shows exactly one toast, the server sentence, and reloads the policy list', async () => {
    refuse(409, RACE)
    renderAt('/policies/pol-9')
    expect(listHook.enabledCalls.every((enabled) => !enabled)).toBe(true)

    publishThroughTheStrip()

    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1))
    expect(mockPublishPolicy).toHaveBeenCalledWith('pol-9')
    expect(mockToastError).toHaveBeenCalledWith(RACE)
    expect(mockToastError).not.toHaveBeenCalledWith(expect.stringMatching(/status code/))
    // The list is revalidated and shown: the winner's Active badge, not this draft's stale editor.
    expect(mockAppMutate).toHaveBeenCalledWith(['release-gate-policies'])
    await waitFor(() => expect(screen.getByRole('heading', { name: /release gate policies/i })).toBeInTheDocument())
    expect(listHook.enabledCalls[listHook.enabledCalls.length - 1]).toBe(true)
  })

  it('says "Policy is already published" for the same draft published twice', async () => {
    refuse(409, 'Policy is already published')
    renderAt('/policies/pol-9')
    publishThroughTheStrip()
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1))
    expect(mockToastError).toHaveBeenCalledWith('Policy is already published')
  })

  it('stays on the editor for a failure that is not a conflict, still with one toast', async () => {
    mockPublishPolicy.mockRejectedValue(new Error('Network Error'))
    renderAt('/policies/pol-9')
    publishThroughTheStrip()
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1))
    expect(mockToastError).toHaveBeenCalledWith('Publish failed')
    expect(screen.getByRole('heading', { name: /^edit:/i })).toBeInTheDocument()
  })
})

/**
 * W27b PAN-05 (Y1, Y2): Publish and Deactivate ask in the page, not with the
 * browser's prompt. The first click sends nothing; the strip's own button is
 * the only sender, once; Cancel and Escape send nothing; the sentence is the
 * old prompt's, word for word.
 */
const ACTIVE_POLICY = {
  id: 'p-active', name: 'Checkout gate', version: 2, is_active: true, is_draft: false,
  project_id: null, created_at: '2026-10-01T00:00:00Z', description: '', rules: {},
}
const PUBLISH_SENTENCE = 'Publish this policy? It will become active and the previous version will be deactivated.'

const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null

describe('Publish and Deactivate ask in the page', () => {
  it('Publish: the header button only asks, right under the page header; the strip sends once', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    mockPublishPolicy.mockResolvedValue(undefined)
    const { container } = renderAt('/policies/pol-9')

    fireEvent.click(screen.getByRole('button', { name: 'Publish' }))
    expect(mockPublishPolicy).not.toHaveBeenCalled()
    const asked = strip() as HTMLElement
    expect(asked).not.toBeNull()
    expect(asked.querySelector('p')?.textContent).toBe(PUBLISH_SENTENCE)
    // Right under the page header, in the page (no overlay).
    expect(container.querySelector('[data-page-header]')?.nextElementSibling).toBe(asked)
    expect(container.querySelector('[aria-modal], [role="dialog"]')).toBeNull()

    fireEvent.click(within(asked).getByRole('button', { name: 'Publish' }))
    expect(strip()).toBeNull()
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith('Policy published and activated'))
    expect(mockPublishPolicy).toHaveBeenCalledTimes(1)
    expect(mockPublishPolicy).toHaveBeenCalledWith('pol-9')
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('Publish: Cancel and Escape close the strip and send nothing', () => {
    renderAt('/policies/pol-9')
    fireEvent.click(screen.getByRole('button', { name: 'Publish' }))
    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
    expect(strip()).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Publish' }))
    fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(strip()).toBeNull()
    expect(mockPublishPolicy).not.toHaveBeenCalled()
  })

  it('Deactivate: asks right under its policy card; the strip sends once and re-reads the list', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    listHook.policies = [ACTIVE_POLICY]
    mockDeactivatePolicy.mockResolvedValue(undefined)
    renderAt('/policies')

    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }))
    expect(mockDeactivatePolicy).not.toHaveBeenCalled()
    const asked = strip() as HTMLElement
    expect(asked.querySelector('p')?.textContent).toBe('Deactivate this policy?')
    const card = screen.getByText('Checkout gate').closest('.card') as HTMLElement
    expect(card.nextElementSibling).toBe(asked.closest('[data-in-page-row]'))
    expect(card.contains(asked)).toBe(false)

    fireEvent.click(within(asked).getByRole('button', { name: 'Deactivate' }))
    expect(strip()).toBeNull()
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith('Policy deactivated'))
    expect(mockDeactivatePolicy).toHaveBeenCalledTimes(1)
    expect(mockDeactivatePolicy).toHaveBeenCalledWith('p-active')
    expect(mockRefreshPolicies).toHaveBeenCalledTimes(1)
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('Deactivate: Escape in the strip is Cancel', () => {
    listHook.policies = [ACTIVE_POLICY]
    renderAt('/policies')
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }))
    fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(strip()).toBeNull()
    expect(mockDeactivatePolicy).not.toHaveBeenCalled()
  })
})
