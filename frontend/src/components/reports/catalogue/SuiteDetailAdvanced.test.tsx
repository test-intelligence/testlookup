/**
 * Suite detail's Wave-3 composite (no flag gate since Phase D, S4): the two
 * lazy sections it mounts with the page's suite, and its error isolation.
 * The sections are stubs here that show what they were handed; each has its
 * own test.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
// EXEC-04b: "Suite execution time", the third section (its own test is SuiteExecutionSection.test.tsx).
vi.mock('./SuiteExecutionSection', () => {
  sections.loads.push('execution')
  const SuiteExecutionSection = (props: { days: number; suiteName: string; projectId?: string | null }) => (
    <div data-testid="execution" data-props={JSON.stringify(props)} />
  )
  return { default: SuiteExecutionSection }
})

import SuiteDetailAdvanced from './SuiteDetailAdvanced'

beforeEach(() => {
  sections.heatmapThrows = false
})
afterEach(() => {
  vi.restoreAllMocks()
})

const propsOf = (testId: string) => JSON.parse(screen.getByTestId(testId).getAttribute('data-props') ?? '{}')

/** Lazy section chunks can outlast findBy's default 1 s on a loaded machine. */
const LAZY_TIMEOUT = 5_000

describe('SuiteDetailAdvanced', () => {
  it('the test x run heatmap, the suite scatter and the suite execution time, all for the page’s one suite and window', async () => {
    render(<SuiteDetailAdvanced days={14} suiteName="Auth" projectId="proj-1" />)
    await waitFor(() => expect(screen.getByTestId('scatter')).toBeInTheDocument(), { timeout: LAZY_TIMEOUT })
    expect(await screen.findByTestId('heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(await screen.findByTestId('execution', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(propsOf('heatmap')).toEqual({ days: 14, suiteFilter: ['Auth'], kinds: ['test_run'] })
    expect(propsOf('scatter')).toEqual({ days: 14, suiteFilter: ['Auth'], placement: 'suite' })
    // EXEC-04b: the suite's own project reaches the execution-time read.
    expect(propsOf('execution')).toEqual({ days: 14, suiteName: 'Auth', projectId: 'proj-1' })
    // The heatmap first, the scatter under it, the execution time last.
    const order = Array.from(document.querySelectorAll('[data-testid]'), (el) => el.getAttribute('data-testid'))
    expect(order).toEqual(['heatmap', 'scatter', 'execution'])
  })

  it('no suite: renders nothing', () => {
    const { container } = render(<SuiteDetailAdvanced days={30} suiteName="" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('a section that throws is one error card; the other section still draws', async () => {
    sections.heatmapThrows = true
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<SuiteDetailAdvanced days={30} suiteName="Auth" />)
    expect(await screen.findByText('Could not load the test x run heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(await screen.findByTestId('scatter', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
  })
})
