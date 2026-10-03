/**
 * The Failures composite (plan 2.5): the flag matrix and the three pinned
 * section contracts. Flag-off the page gains ONE flag lookup and nothing else.
 */
import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import FailuresAdvanced from './FailuresAdvanced'

const CATALOGUE = 'viz_chart_data_api'
const ADVANCED = 'viz_advanced_charts'

/** Which flags were LOOKED UP (a lookup is a request), and their answers. */
const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean>, asked: [] as string[] }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => {
    flags.asked.push(key)
    return flags.values[key] ?? false
  },
  useFeatureFlagStatus: (key: string) => {
    flags.asked.push(key)
    return flags.values[key]
  },
}))

/** Each lazy section, replaced by a stub that records it was loaded and what it was handed. */
const loaded = vi.hoisted(() => ({ props: {} as Record<string, unknown>, drillThrows: false, chunk: 0 }))
/** The composite's sections chunk, really imported, counted when it is fetched (imported) at all. */
vi.mock('./FailuresAdvancedSections', async (importOriginal) => {
  loaded.chunk += 1
  return importOriginal()
})
vi.mock('./FailureGroupsSection', () => ({
  default: (props: unknown) => {
    loaded.props.groups = props
    return <div data-testid="groups" />
  },
}))
vi.mock('./FailuresDrill', () => ({
  default: (props: unknown) => {
    loaded.props.drill = props
    if (loaded.drillThrows) throw new Error('drill broke')
    return <div data-testid="drill" />
  },
}))
vi.mock('./ScatterSection', () => ({
  default: (props: unknown) => {
    loaded.props.scatter = props
    return <div data-testid="scatter" />
  },
}))

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

beforeEach(() => {
  flags.values = {}
  flags.asked = []
  loaded.props = {}
  loaded.drillThrows = false
})

/** How long a lazy section chunk may take to arrive on a loaded machine. */
const LAZY_TIMEOUT = 5_000

describe('FailuresAdvanced', () => {
  it('flags off: nothing rendered, nothing loaded, and only the catalogue flag looked up', async () => {
    const { container } = render(<FailuresAdvanced days={30} suiteFilter={null} />)
    await settle()
    expect(container).toBeEmptyDOMElement()
    expect(loaded.props).toEqual({})
    expect(loaded.chunk).toBe(0)
    expect(new Set(flags.asked)).toEqual(new Set([CATALOGUE]))
  })

  it('only the advanced flag: nothing (and the catalogue flag is the only lookup)', async () => {
    flags.values = { [ADVANCED]: true }
    const { container } = render(<FailuresAdvanced days={30} suiteFilter={null} />)
    await settle()
    expect(container).toBeEmptyDOMElement()
    expect(new Set(flags.asked)).toEqual(new Set([CATALOGUE]))
  })

  it('only the catalogue flag: nothing rendered and no section chunk loaded', async () => {
    flags.values = { [CATALOGUE]: true }
    const { container } = render(<FailuresAdvanced days={30} suiteFilter={null} />)
    await settle()
    expect(container).toBeEmptyDOMElement()
    expect(loaded.props).toEqual({})
    // The sections chunk was never even imported (this file's earlier tests import it neither).
    expect(loaded.chunk).toBe(0)
  })

  it('both: the three sections, in order, each handed the page scope through its contract', async () => {
    flags.values = { [CATALOGUE]: true, [ADVANCED]: true }
    const { container } = render(<FailuresAdvanced days={14} suiteFilter="payments" />)
    await settle()
    // The sections are lazy chunks: under a loaded machine (the push gate runs
    // vitest with coverage) the imports outlast findBy's default 1 s. This
    // test is about order and props, not speed.
    expect(await screen.findByTestId('scatter', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    const order = [...container.querySelectorAll('[data-testid]')].map((el) => el.getAttribute('data-testid'))
    expect(order).toEqual(['groups', 'drill', 'scatter'])
    expect(loaded.props).toEqual({
      groups: { days: 14, suiteFilter: 'payments' },
      drill: { days: 14, suiteFilter: 'payments' },
      scatter: { days: 14, suiteFilter: 'payments', placement: 'project' },
    })
  })

  it('a section that throws takes only itself down', async () => {
    flags.values = { [CATALOGUE]: true, [ADVANCED]: true }
    loaded.drillThrows = true
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<FailuresAdvanced days={14} suiteFilter={null} />)
    await settle()
    expect(await screen.findByText('Failures by suite failed to load', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(screen.getByTestId('groups')).toBeInTheDocument()
    expect(screen.getByTestId('scatter')).toBeInTheDocument()
    error.mockRestore()
  })
})
