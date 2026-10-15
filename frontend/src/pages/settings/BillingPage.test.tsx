/**
 * BillingPage — workspace LLM spend and per-project quotas.
 *
 * Zero coverage before this (backlog: "zero-coverage surfaces"). Unlike the
 * two pages swept before it, **no defect was found here** — the figures and
 * the input bounds were already correct. These tests exist to keep them that
 * way, because each property below is the kind that regresses silently.
 *
 * What was checked against the backend and confirmed sound:
 *
 * - `utilization_pct` is `total_cost / hard_cap * 100`, and `hard_cap_usd`
 *   defaults to 0. The division IS guarded (`llm_cost_budget.py:255`), and
 *   more importantly `get_current_usage` returns `utilization_pct: None` for a
 *   project with no quota rather than `0.0`. That distinction is the whole
 *   point: a project with no cap that has spent real money must not render
 *   "0%", which reads as "comfortably under budget" when the truth is "no
 *   budget exists". It renders "—".
 *
 * - The backend emits FOUR statuses — OK, SOFT_WARN, CAPPED and **UNLIMITED**.
 *   A consumer rendering from a narrower copy of a producer's vocabulary is a
 *   recurring defect class in this codebase, so the UNLIMITED row is pinned
 *   here: `UsageStatus` includes it and `StatusPill`'s fallback branch renders
 *   it rather than dropping the row.
 *
 * - The soft-warn input's `min`/`max` (1/100) match the backend's
 *   `Field(100, ge=1, le=100)` exactly, and the handler rounds to an integer
 *   to match the backend's `int`. Bounds that drift from the schema turn a
 *   typo into a 422 the user cannot act on.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BillingPage from './BillingPage'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'
import type {
  BillingOverviewProject,
  BillingOverviewResponse,
} from '@/services/llmBudgetService'

let overview: BillingOverviewResponse | undefined
let isAdmin = true
let billingError: unknown
let billingIsError = false
const refreshBilling = vi.fn()

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@/hooks/useLlmBudget', () => ({
  useBillingOverview: () => ({
    overview,
    error: billingError,
    isLoading: false,
    isError: billingIsError,
    refresh: refreshBilling,
  }),
  useProjectQuota: () => ({ quota: null, isLoading: false, refresh: vi.fn() }),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin }),
}))

function project(overrides: Partial<BillingOverviewProject> = {}): BillingOverviewProject {
  return {
    project_id: 'p-1',
    project_name: 'Checkout Service',
    current_cost_usd: 12.3456,
    hard_cap_usd: 100,
    utilization_pct: 12.3456,
    status: 'OK',
    cap_hits: 0,
    ...overrides,
  }
}

function renderWith(projects: BillingOverviewProject[], totals: Partial<BillingOverviewResponse> = {}) {
  overview = {
    period_start: '2026-08-01T00:00:00Z',
    period_end: '2026-08-31T23:59:59Z',
    total_cost_usd: projects.reduce((s, p) => s + p.current_cost_usd, 0),
    total_llm_calls: 42,
    projects,
    ...totals,
  }
  return render(
    <MemoryRouter>
      <BillingPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  isAdmin = true
  billingError = undefined
  billingIsError = false
})

describe('a project with no quota is not reported as 0% used', () => {
  it('renders an em dash for utilization, not a number', () => {
    renderWith([
      project({
        project_name: 'Uncapped',
        status: 'UNLIMITED',
        hard_cap_usd: null,
        utilization_pct: null,
        current_cost_usd: 512.5,
      }),
    ])

    // The row exists and shows real spend...
    expect(screen.getByText('Uncapped')).toBeInTheDocument()
    // ...but must not claim a percentage of a cap that does not exist. "0%"
    // here would read as "well under budget" for a project that has spent
    // $512 against no budget at all.
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2) // cap + utilization
  })

  it('renders the UNLIMITED status rather than dropping it', () => {
    renderWith([project({ status: 'UNLIMITED', hard_cap_usd: null, utilization_pct: null })])

    // The backend emits four statuses; a consumer that knows only three
    // silently renders nothing for the fourth.
    expect(screen.getByText('UNLIMITED')).toBeInTheDocument()
  })
})

describe('capped and warning projects are distinguishable', () => {
  it.each([
    ['CAPPED' as const, 100],
    ['SOFT_WARN' as const, 82],
    ['OK' as const, 12],
  ])('renders the %s status with its utilization', (status, pct) => {
    renderWith([project({ status, utilization_pct: pct })])

    expect(screen.getByText(status)).toBeInTheDocument()
    expect(screen.getByText(`${pct}%`)).toBeInTheDocument()
  })
})

describe('money is rendered at a precision that does not hide spend', () => {
  it('shows sub-cent LLM cost rather than rounding it away', () => {
    // LLM costs are frequently fractions of a cent; 2dp would render a
    // month of real spend as "$0.00".
    renderWith([project({ current_cost_usd: 0.0037, utilization_pct: 5 })])

    // Two places show it: the workspace KPI total and the project row. With a
    // single project those must agree — a total that does not reconcile with
    // the rows beneath it is the "figures that cannot all be true" class.
    expect(screen.getAllByText('$0.0037')).toHaveLength(2)
  })
})

describe('billing period and outage truthfulness', () => {
  it('renders the backend UTC half-open month as inclusive UTC calendar dates', () => {
    renderWith([], {
      period_start: '2026-09-01T00:00:00Z',
      period_end: '2026-10-01T00:00:00Z',
    })

    expect(screen.getByText(/Current period: Sep 1 — Sep 30/)).toBeInTheDocument()
    expect(screen.queryByText(/Aug 31/)).toBeNull()
    expect(screen.queryByText(/Oct 1/)).toBeNull()
  })

  it('renders a retryable error instead of an empty billing state', () => {
    overview = undefined
    billingError = new Error('network unavailable')
    billingIsError = true
    render(<MemoryRouter><BillingPage /></MemoryRouter>)

    expect(screen.getByTestId('billing-data-unavailable')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(refreshBilling).toHaveBeenCalledTimes(1)
  })
})

describe('the quota form is bounded to what the backend accepts', () => {
  it('bounds the soft-warn threshold to 1-100 for an admin', () => {
    renderWith([project()])

    // Open the editor for the row.
    // fireEvent, not element.click() — React does not observe a raw DOM
    // click, so the dialog never opens and the assertion below reads as a
    // missing input rather than an unopened form.
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))

    const pct = document.querySelector<HTMLInputElement>('input[max="100"][min="1"]')
    // Backend: soft_warn_threshold_pct = Field(100, ge=1, le=100). Bounds that
    // drift from the schema turn a typo into a 422 the user cannot act on.
    expect(pct, 'soft-warn input must carry the backend bounds').toBeTruthy()
  })
})

// ── W04 AIC-06: "Included per month" and "Overage rate" compute an overage ──

const OVERAGE: Partial<BillingOverviewProject> = {
  project_id: 'p-over',
  project_name: 'Payments',
  current_cost_usd: 30,
  included_usd: 20,
  hard_cap_usd: 100,
  utilization_pct: 30,
  status: 'OK',
  overage_rate_usd: 1.5,
  covered_usd: 20,
  overage_usd: 10,
  overage_charge_usd: 15,
  projected_period_cost_usd: 90,
  projected_overage_usd: 70,
  projected_overage_charge_usd: 105,
  projection_basis: 'linear_elapsed_days',
}

const rowOf = (id: string) => document.querySelector(`[data-billing-project="${id}"]`) as HTMLElement
const cell = (id: string, name: string) => rowOf(id).querySelector(`[data-billing-${name}]`) as HTMLElement

describe('W04 AIC-06: covered, overage, charge and the month-end projection', () => {
  it('the table has the overage columns between Spend and Cap', () => {
    renderWith([project()])
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['Project', 'Spend', 'Covered', 'Overage', 'Charge', 'Cap', 'Utilization', 'Status', 'Cap hits', ''])
    expect(document.querySelector('[data-billing-table]')).not.toBeNull()
  })

  it('Included 20, rate 1.5, spend 30: Covered $20.00, Overage $10.00, Charge $15.00 and the projection', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-08-11T00:00:00Z'))
    try {
      renderWith([project(OVERAGE)], { period_start: '2026-08-01T00:00:00Z', period_end: '2026-09-01T00:00:00Z' })
      expect(cell('p-over', 'spend')).toHaveTextContent('$30.00')
      expect(cell('p-over', 'covered')).toHaveTextContent('$20.00')
      expect(cell('p-over', 'overage')).toHaveTextContent('$10.00')
      expect(cell('p-over', 'charge')).toHaveTextContent(/^\$15\.00/)
      const projection = cell('p-over', 'projection')
      expect(projection).toHaveTextContent('proj. $105.00 by month end')
      expect(projection).toHaveAttribute('title', 'linear projection from 10 of 31 days of the period')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a project without an enabled quota keeps UNLIMITED and shows — in the three new cells, with no projection', () => {
    renderWith([project({ project_id: 'p-free', status: 'UNLIMITED', hard_cap_usd: null, utilization_pct: null })])
    expect(screen.getByText('UNLIMITED')).toBeInTheDocument()
    for (const name of ['covered', 'overage', 'charge']) expect(cell('p-free', name)).toHaveTextContent(/^—$/)
    expect(cell('p-free', 'projection')).toBeNull()
  })

  it('a cap with no included allowance (the form default) prints no charge — the server sends nulls, never 0', () => {
    renderWith([project({ project_id: 'p-cap', overage_rate_usd: 1, covered_usd: null, overage_usd: null, overage_charge_usd: null })])
    for (const name of ['covered', 'overage', 'charge']) expect(cell('p-cap', name)).toHaveTextContent(/^—$/)
    expect(cell('p-cap', 'charge')).not.toHaveTextContent('$0.00')
  })

  it('no projection line before enough of the period has elapsed (the server sends null)', () => {
    renderWith([project({ ...OVERAGE, projected_overage_charge_usd: null, projected_overage_usd: null, projected_period_cost_usd: null, projection_basis: 'insufficient_elapsed_time' })])
    expect(cell('p-over', 'charge')).toHaveTextContent(/^\$15\.00$/)
    expect(cell('p-over', 'projection')).toBeNull()
  })

  it('the fourth tile sums the projected charges it has, and says — when there are none', () => {
    const { unmount } = renderWith([
      project(OVERAGE),
      project({ ...OVERAGE, project_id: 'p-2', project_name: 'Search', projected_overage_charge_usd: 4.5 }),
      project({ project_id: 'p-3', project_name: 'Free', status: 'UNLIMITED' }),
    ])
    expect(screen.getByTestId('billing-projected-charge')).toHaveTextContent('Projected overage charge')
    expect(screen.getByTestId('billing-projected-charge')).toHaveTextContent('$109.50')
    unmount()
    renderWith([project({ status: 'UNLIMITED' })])
    expect(screen.getByTestId('billing-projected-charge')).toHaveTextContent(/Projected overage charge—$/)
  })

  it('Spend uses 2 decimals from $1 up and 4 below', () => {
    renderWith([
      project({ project_id: 'a', current_cost_usd: 12.3456 }),
      project({ project_id: 'b', current_cost_usd: 0.0012 }),
      project({ project_id: 'c', current_cost_usd: 0 }),
    ])
    expect(cell('a', 'spend')).toHaveTextContent(/^\$12\.35$/)
    expect(cell('b', 'spend')).toHaveTextContent(/^\$0\.0012$/)
    expect(cell('c', 'spend')).toHaveTextContent(/^\$0\.00$/)
  })

  it('the quota editor says what Included and Overage rate do (and that the rate never limits spend)', () => {
    renderWith([project()])
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    expect(document.querySelector('[data-billing-hint="included"]')).toHaveTextContent(
      'Spend up to this amount each month is covered; spend above it is overage.',
    )
    expect(document.querySelector('[data-billing-hint="rate"]')).toHaveTextContent(
      'Charge = overage × rate. Shown in the table; it does not limit spend (the hard cap does).',
    )
    // The form default for the rate stays 1.
    const rate = screen.getByLabelText(/Overage rate/) as HTMLInputElement
    expect(rate.value).toBe('1')
  })
})

describe('the settings page template (UX redesign P5)', () => {
  it('has the compact header with the route help topic; the table needs no layout change', () => {
    renderWith([project()])
    expectTemplateHeader('LLM Cost Budget', '/settings/billing')
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})
