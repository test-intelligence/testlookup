/**
 * Suite detail's Wave-3 composite: the flag gate (through the one seam), the
 * two lazy sections it mounts with the page's suite, and its error isolation.
 * The sections are stubs here that show what they were handed; each has its
 * own test.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const flags = vi.hoisted(() => ({ advanced: false }))
vi.mock('./useCatalogueRollout', () => ({ useAdvancedRollout: () => flags.advanced }))

const sections = vi.hoisted(() => ({ heatmapThrows: false, loads: [] as string[] }))
vi.mock('./HeatmapSection', () => {
  sections.loads.push('heatmap')
  const HeatmapSection = (props: { days: number; suiteFilter: readonly string[]; kinds: readonly string[] }) => {
    if (sections.heatmapThrows) throw new Error('boom')
    return <div data-testid="heatmap" data-props={JSON.stringify(props)} />
  }
  return { HeatmapSection, default: HeatmapSection }
})
vi.mock('./ScatterSection', () => {
  sections.loads.push('scatter')
  const ScatterSection = (props: { days: number; suiteFilter: readonly string[]; placement: string }) => (
    <div data-testid="scatter" data-props={JSON.stringify(props)} />
  )
  return { ScatterSection, default: ScatterSection }
})

import SuiteDetailAdvanced from './SuiteDetailAdvanced'

beforeEach(() => {
  flags.advanced = false
  sections.heatmapThrows = false
})
afterEach(() => {
  vi.restoreAllMocks()
})

const propsOf = (testId: string) => JSON.parse(screen.getByTestId(testId).getAttribute('data-props') ?? '{}')

/** Lazy section chunks can outlast findBy's default 1 s on a loaded machine. */
const LAZY_TIMEOUT = 5_000

describe('SuiteDetailAdvanced', () => {
  it('advanced flag off: renders nothing at all (no placeholder, no chunk)', async () => {
    const { container } = render(<SuiteDetailAdvanced days={30} suiteName="Auth" />)
    expect(container).toBeEmptyDOMElement()
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(sections.loads).toEqual([])
  })

  it('on: the test x run heatmap and the suite scatter, both for the page’s one suite and window', async () => {
    flags.advanced = true
    render(<SuiteDetailAdvanced days={14} suiteName="Auth" />)
    await waitFor(() => expect(screen.getByTestId('scatter')).toBeInTheDocument(), { timeout: LAZY_TIMEOUT })
    expect(await screen.findByTestId('heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(propsOf('heatmap')).toEqual({ days: 14, suiteFilter: ['Auth'], kinds: ['test_run'] })
    expect(propsOf('scatter')).toEqual({ days: 14, suiteFilter: ['Auth'], placement: 'suite' })
    // The heatmap first, the scatter under it.
    const order = Array.from(document.querySelectorAll('[data-testid]'), (el) => el.getAttribute('data-testid'))
    expect(order).toEqual(['heatmap', 'scatter'])
  })

  it('no suite: renders nothing', () => {
    flags.advanced = true
    const { container } = render(<SuiteDetailAdvanced days={30} suiteName="" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('a section that throws is one error card; the other section still draws', async () => {
    flags.advanced = true
    sections.heatmapThrows = true
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<SuiteDetailAdvanced days={30} suiteName="Auth" />)
    expect(await screen.findByText('Failed to load the test x run heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(await screen.findByTestId('scatter', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
  })
})
