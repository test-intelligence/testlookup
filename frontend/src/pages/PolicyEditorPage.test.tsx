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
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PolicyEditorPage from './PolicyEditorPage'

const mockCreatePolicy = vi.fn()
const mockToastError = vi.fn()

vi.mock('react-hot-toast', () => ({
  default: { error: (m: string) => mockToastError(m), success: vi.fn() },
}))

vi.mock('@/hooks/usePolicyEditor', () => ({
  usePolicies: () => ({ policies: [], isLoading: false, isError: false, mutate: vi.fn() }),
  usePolicy: () => ({ policy: null, isLoading: false, isError: false }),
}))

vi.mock('../services/policyService', async () => {
  const actual = await vi.importActual<typeof import('../services/policyService')>(
    '../services/policyService',
  )
  return {
    ...actual,
    createPolicy: (...a: unknown[]) => mockCreatePolicy(...a),
    updatePolicy: vi.fn(),
    publishPolicy: vi.fn(),
    deactivatePolicy: vi.fn(),
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
 * threshold, [2] pass-rate minimum, [3] hard-floor factor, [4..9] bands and
 * hard caps, then the seven dimension weights LAST.
 */
const numbers = () => screen.getAllByRole('spinbutton') as HTMLInputElement[]
const goThreshold = () => numbers()[0]
/** The weights are the trailing seven numeric inputs. */
const weightInputs = () => numbers().slice(-7)

function nameIt(value = 'Release gate') {
  fireEvent.change(nameBox(), { target: { value } })
}

beforeEach(() => {
  vi.clearAllMocks()
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
