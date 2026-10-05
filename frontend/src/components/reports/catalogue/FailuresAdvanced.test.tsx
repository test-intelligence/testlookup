/**
 * The Failures composite (plan 2.5): the three pinned section contracts. Since
 * Phase D, S5 it mounts unconditionally and asks no flag.
 */
import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import FailuresAdvanced from './FailuresAdvanced'

/** Which flags were LOOKED UP (a lookup is a request): none, since Phase D, S5. */
const flags = vi.hoisted(() => ({ asked: [] as string[] }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => {
    flags.asked.push(key)
    return false
  },
  useFeatureFlagStatus: (key: string) => {
    flags.asked.push(key)
    return false
  },
}))

/** Each lazy section, replaced by a stub that records it was loaded and what it was handed. */
const loaded = vi.hoisted(() => ({ props: {} as Record<string, unknown>, drillThrows: false }))
/** The composite's sections chunk, really imported. */
vi.mock('./FailuresAdvancedSections', async (importOriginal) => importOriginal())
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
  flags.asked = []
  loaded.props = {}
  loaded.drillThrows = false
})

/** How long a lazy section chunk may take to arrive on a loaded machine. */
const LAZY_TIMEOUT = 5_000

describe('FailuresAdvanced', () => {
  it('the three sections, in order, each handed the page scope through its contract, and no flag asked', async () => {
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
    expect(flags.asked).toEqual([])
  })

  it('a section that throws takes only itself down', async () => {
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
