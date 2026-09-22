import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { validateContract, type EnvelopeMeta } from '@/lib/viz/contracts'
import { GALLERY_CASES } from '@/pages/dev/reportContextFixtures'
import ReportContextHeader from './ReportContextHeader'
import { buildContextEntries, CONTEXT_ORDER, formatGeneratedAt } from './contextModel'

const fixture = (id: string): EnvelopeMeta => {
  const found = GALLERY_CASES.find((c) => c.id === id)
  if (!found?.meta) throw new Error(`no fixture ${id}`)
  return found.meta
}

const withScope = (base: EnvelopeMeta, scope: Partial<EnvelopeMeta['scope']>, rest: Partial<EnvelopeMeta> = {}) => ({
  ...base,
  ...rest,
  scope: { ...base.scope, ...scope },
})

function entries() {
  return Array.from(document.querySelectorAll('[data-context-entry]')).map((el) => ({
    key: el.getAttribute('data-context-entry'),
    label: el.querySelector('dt')?.textContent,
    value: el.querySelector('dd')?.textContent,
  }))
}

describe('ReportContextHeader', () => {
  it('is a definition list in the fixed order Project → Release → Test Suite → Window → Basis → Generated', () => {
    render(<ReportContextHeader meta={fixture('filtered')} allProjects={false} />)
    expect(screen.getByRole('region', { name: 'Report context' }).querySelector('dl')).not.toBeNull()
    expect(entries().map((e) => e.key)).toEqual([...CONTEXT_ORDER])
    expect(entries().map((e) => e.label)).toEqual(['Project', 'Release', 'Test Suite', 'Window', 'Basis', 'Generated'])
  })

  it('shows the values the server applied', () => {
    render(<ReportContextHeader meta={fixture('filtered')} allProjects={false} />)
    const byKey = Object.fromEntries(entries().map((e) => [e.key, e.value]))
    expect(byKey.project).toBe('Checkout')
    expect(byKey.release).toBe('2026.09, 2026.08')
    expect(byKey.suite).toBe('payments, cart')
    expect(byKey.window).toContain('Last 14 days')
    expect(byKey.window).toContain('2026-09-05 → 2026-09-19 UTC')
    expect(byKey.basis).toBe('Pass rate of executions')
    expect(byKey.generated).toBe('2026-09-19 10:42 UTC')
  })

  it('says "All releases" and "All suites" explicitly when nothing is filtered', () => {
    render(<ReportContextHeader meta={fixture('unfiltered')} allProjects={false} />)
    const byKey = Object.fromEntries(entries().map((e) => [e.key, e.value]))
    expect(byKey.release).toBe('All releases')
    expect(byKey.suite).toBe('All suites')
  })

  it('shows what the SERVER applied, not what was asked: a release the API dropped reads "All releases"', () => {
    // The client asked for a release; the API ignored it and says so.
    const meta = fixture('ignored-filter')
    expect(meta.scope.releases).toEqual([])
    render(<ReportContextHeader meta={meta} allProjects={false} />)
    const release = document.querySelector('[data-context-entry="release"]') as HTMLElement
    expect(within(release).getByText('All releases')).toBeInTheDocument()
    expect(release.querySelector('[data-context-ignored]')?.textContent).toMatch(/Filter not applied: Defect counts/)
  })

  it('All-Projects mode reads "All projects (N)" with N from the applied scope', () => {
    render(<ReportContextHeader meta={fixture('all-projects')} allProjects />)
    expect(entries()[0].value).toBe('All projects (12)')
  })

  it('tags an archived release and names unattributed runs', () => {
    render(<ReportContextHeader meta={fixture('archived-unattributed')} allProjects={false} />)
    const release = document.querySelector('[data-context-entry="release"]') as HTMLElement
    expect(release.querySelector('dd')?.textContent).toBe('2026.02Archived, Unattributed runsUnattributed')
    expect(Array.from(release.querySelectorAll('[data-context-tag]')).map((t) => t.textContent)).toEqual([
      'Archived',
      'Unattributed',
    ])
  })

  it('omits Basis when the response states none, and never invents a Generated time', () => {
    const base = fixture('unfiltered')
    render(<ReportContextHeader meta={{ ...base, pass_rate_basis: null, generated_at: '' }} allProjects={false} />)
    expect(entries().map((e) => e.key)).toEqual(['project', 'release', 'suite', 'window'])
    expect(document.body.textContent).not.toMatch(/ago|just now/)
  })

  it('renders the Generated instant from meta.generated_at in a <time>', () => {
    render(<ReportContextHeader meta={fixture('unfiltered')} allProjects={false} />)
    const time = document.querySelector('time') as HTMLTimeElement
    expect(time.getAttribute('datetime')).toBe('2026-09-19T10:42:07Z')
    expect(formatGeneratedAt('not a date')).toBeNull()
  })

  it('loading and unavailable states claim no values', () => {
    const { rerender } = render(<ReportContextHeader meta={null} allProjects={false} loading />)
    const region = screen.getByRole('region', { name: 'Report context' })
    expect(region).toHaveAttribute('aria-busy', 'true')
    expect(region.querySelector('[data-context-value]')).toBeNull()
    rerender(<ReportContextHeader meta={null} allProjects={false} unavailableReason="No scope in this response." />)
    expect(screen.getByText('No scope in this response.')).toBeInTheDocument()
  })

  it('binds values to --text-xl and labels to --text-sm', () => {
    render(<ReportContextHeader meta={fixture('filtered')} allProjects={false} />)
    for (const value of document.querySelectorAll('[data-context-value]')) {
      expect(value.className).toContain('text-[length:var(--text-xl)]')
    }
    for (const label of document.querySelectorAll('dt')) {
      expect(label.className).toContain('text-[length:var(--text-sm)]')
    }
  })
})

describe('more than three suites', () => {
  const meta = () => fixture('many-suites')

  it('shows three inline and "+4" for the rest', () => {
    render(<ReportContextHeader meta={meta()} allProjects={false} />)
    const suite = document.querySelector('[data-context-entry="suite"]') as HTMLElement
    expect(suite.querySelector('dd')?.textContent).toBe('payments, cart, search+4')
    const more = screen.getByRole('button', { name: '+4 more: show all 7 suites' })
    expect(more).toHaveTextContent('+4')
    // WCAG 2.5.3: the accessible name CONTAINS the visible label (voice control says "+4").
    expect(more.getAttribute('aria-label')?.startsWith(more.textContent ?? '?')).toBe(true)
  })

  it('a window the chrome capped says so beside the value (fix round B, M2)', () => {
    render(<ReportContextHeader meta={meta()} allProjects={false} windowNote="(max for summary)" />)
    const windowEntry = document.querySelector('[data-context-entry="window"] dd') as HTMLElement
    expect(windowEntry.textContent).toMatch(/^Last \d+ days \(max for summary\)/)
  })

  it('lists every suite in a portal popover; a click inside keeps it open, outside closes it', () => {
    render(<ReportContextHeader meta={meta()} allProjects={false} />)
    const trigger = screen.getByRole('button', { name: '+4 more: show all 7 suites' })
    fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'All suites (7)' })
    // A portal: not inside the header.
    expect(document.querySelector('[data-report-context-header]')?.contains(dialog)).toBe(false)
    expect(within(dialog).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'payments',
      'cart',
      'search',
      'checkout-api',
      'inventory',
      'auth',
      'notifications',
    ])
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    // Inside the portal: NOT an outside click (a ref.contains check on the header would close it).
    fireEvent.mouseDown(within(dialog).getByText('inventory'))
    expect(screen.getByRole('dialog', { name: 'All suites (7)' })).toBeInTheDocument()

    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('moves focus into the list and Escape returns it to the trigger', () => {
    render(<ReportContextHeader meta={meta()} allProjects={false} />)
    const trigger = screen.getByRole('button', { name: '+4 more: show all 7 suites' })
    fireEvent.click(trigger)
    expect(document.activeElement).toBe(document.querySelector('[data-context-popover]'))
    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('shows three suites inline with no "+" when there are exactly three', () => {
    render(
      <ReportContextHeader meta={withScope(meta(), { suites: ['a', 'b', 'c'] })} allProjects={false} />,
    )
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
  })
})

describe('buildContextEntries', () => {
  it('names a release with an empty name by a short id rather than rendering blank', () => {
    const base = fixture('unfiltered')
    const out = buildContextEntries({
      meta: withScope(base, { releases: [{ id: '22222222-2222-4222-8222-000000000009', name: ' ', status: 'released' }] }),
      allProjects: false,
    })
    expect(out[1].values).toEqual([{ text: 'Release 22222222' }])
  })

  it('every gallery meta except totals-missing is a valid C2 envelope', () => {
    for (const c of GALLERY_CASES) {
      if (!c.meta) continue
      const result = validateContract('envelope', c.meta)
      if (c.id === 'totals-missing') expect(result.ok).toBe(false)
      else expect(result, c.id).toMatchObject({ ok: true })
    }
  })
})
