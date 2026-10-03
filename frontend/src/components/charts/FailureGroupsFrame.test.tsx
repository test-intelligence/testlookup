import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GraphChart } from '@/lib/viz/contracts'
import type { ChartResponse, ChartState } from './chartStateCore'
import FailureGroupsFrame, { NO_GROUPS_MESSAGE } from './FailureGroupsFrame'
import {
  FAILURE_GROUPS_META,
  failureGroupsSeries,
  type FailureGroupsFixtureOptions,
} from './failureGroups/failureGroups.fixtures'
import { failureGroupsModel, groupsSeries, relationsSeries } from './failureGroups/failureGroups.model'
import { EMPTY_ANSWER_HEIGHT } from './failureGroups/plot.model'

const ready = (options: FailureGroupsFixtureOptions = {}): ChartState<ChartResponse<GraphChart>> => ({
  status: 'ready',
  data: { meta: FAILURE_GROUPS_META, series: failureGroupsSeries(options) },
  meta: FAILURE_GROUPS_META,
  revalidating: false,
})

function renderFrame(
  state: ChartState<ChartResponse<GraphChart>>,
  props: Partial<Parameters<typeof FailureGroupsFrame>[0]> = {},
) {
  return render(
    <FailureGroupsFrame
      title="Failures grouped by error message"
      headingLevel={3}
      state={state}
      onOpenGroup={vi.fn()}
      plotWidth={900}
      {...props}
    />,
  )
}

describe('FailureGroupsFrame', () => {
  it('draws the bubbles and the ranked table, with the Pareto takeaway', () => {
    const { container } = renderFrame(ready({ groups: 6 }))
    expect(container.querySelector('[data-group-plot="bubbles"]')).not.toBeNull()
    expect(screen.getByRole('table', { name: 'Failure groups, largest first' })).toBeInTheDocument()
    expect(container.querySelector('[data-chart-takeaway]')).toHaveTextContent(/^Top 3 groups = \d+(\.\d)?% of failures$/)
  })

  it('names the table and its scrolling region by tableCaption, so two frames on one page differ (landmark-unique)', () => {
    renderFrame(ready({ groups: 6 }), { tableCaption: 'Nightly: groups, largest first' })
    expect(screen.getByRole('table', { name: 'Nightly: groups, largest first' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Nightly: groups, largest first' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Failure groups, largest first' })).toBeNull()
  })

  it('states the roll-ups that are not circles in the footer', () => {
    const { container } = renderFrame(ready({ groups: 3 }))
    expect(container.querySelector('[data-group-rollup="no-message"]')).toHaveTextContent(/^No error message: 12 failures \(/)
    expect(container.querySelector('[data-group-rollup="singletons"]')).toHaveTextContent(
      /^Seen once: 30 failures in 30 signatures/,
    )
    expect(container.querySelector('[data-group-rollup="omitted"]')).toBeNull()
  })

  it('offers "Related groups" only when the groups are linked (M-504c), and switches to it', () => {
    const { container, unmount } = renderFrame(ready({ groups: 6, edges: false }))
    expect(screen.queryByRole('button', { name: 'Related groups' })).toBeNull()
    expect(container.querySelector('[data-group-view-switch]')).toBeNull()
    unmount()

    const linked = renderFrame(ready({ groups: 6 }))
    const related = screen.getByRole('button', { name: 'Related groups' })
    expect(screen.getByRole('button', { name: 'Bubbles' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(related)
    expect(related).toHaveAttribute('aria-pressed', 'true')
    expect(linked.container.querySelector('[data-group-plot="relations"]')).not.toBeNull()
    expect(linked.container.querySelector('[data-group-relations-caption]')).toHaveTextContent(
      'Linked groups fail in the same tests. Position has no other meaning.',
    )
  })

  it('an initial "relations" view falls back to bubbles when nothing is linked', () => {
    const { container } = renderFrame(ready({ groups: 4, edges: false }), { initialView: 'relations' })
    expect(container.querySelector('[data-group-plot="bubbles"]')).not.toBeNull()
  })

  it('the table view lists the groups (bubbles) or the links (related groups), labels kept as text', () => {
    renderFrame(ready({ groups: 4, hostile: true }))
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const table = screen.getByRole('table', { name: /data table$/ })
    expect(within(table).getAllByRole('row').length).toBe(5)
    expect(within(table).getByText('__proto__')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Related groups' }))
    expect(
      within(screen.getByRole('table', { name: /data table$/ }))
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['Source', 'Target', 'Weight'])
  })

  it('failures but no group (all seen once): says so, keeps the roll-ups, offers no table', () => {
    const series = failureGroupsSeries({ groups: 0 })
    const state: ChartState<ChartResponse<GraphChart>> = {
      status: 'ready',
      data: { meta: FAILURE_GROUPS_META, series },
      meta: FAILURE_GROUPS_META,
      revalidating: false,
    }
    const { container } = renderFrame(state)
    expect(container.querySelector('[data-group-none]')).toHaveTextContent(NO_GROUPS_MESSAGE)
    // R2-B F-14: one sentence, not a 360 px band.
    expect((container.querySelector('[data-chart-body]') as HTMLElement).style.minHeight).toBe(`${EMPTY_ANSWER_HEIGHT}px`)
    expect(container.querySelector('[data-group-rollup="singletons"]')).not.toBeNull()
    expect(screen.queryByRole('button', { name: 'View as table' })).toBeNull()
  })

  it('the frame owns the other states', () => {
    const { container } = renderFrame({ status: 'loading' })
    expect(container.querySelector('[data-chart-state="loading"]')).not.toBeNull()
    expect(container.querySelector('[data-group-plot]')).toBeNull()
  })

  it('opening a group from the table and activating from the plot reach the host', () => {
    const onOpenGroup = vi.fn()
    const onMarkActivate = vi.fn()
    const { container } = renderFrame(ready({ groups: 3 }), { onOpenGroup, onMarkActivate })
    fireEvent.click(screen.getByRole('button', { name: 'Error 1: TimeoutError at step 1' }))
    expect(onOpenGroup).toHaveBeenCalledWith(expect.objectContaining({ rank: 2 }))
    const surface = container.querySelector('[data-group-plot]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'Enter' })
    expect(onMarkActivate).toHaveBeenCalledWith(expect.objectContaining({ dimension: 'error_signature' }), 'drill')
  })

  it('never says "AI"', () => {
    const { container } = renderFrame(ready({ groups: 5 }))
    expect(container.textContent).not.toMatch(/\bAI\b/)
  })
})

describe('frame series', () => {
  it('groupsSeries: one category point per group, labels as own properties (hostile ids included)', () => {
    const model = failureGroupsModel({
      kind: 'graph',
      nodes: [
        { id: '__proto__', label: 'p', size: 3 },
        { id: 'constructor', label: 'c', size: 2 },
      ],
      edges: [],
    })
    const series = groupsSeries(model)
    expect(series.series[0].points).toEqual([
      { x: '__proto__', y: 3, n: 3 },
      { x: 'constructor', y: 2, n: 2 },
    ])
    expect(Object.prototype.hasOwnProperty.call(series.x_labels, '__proto__')).toBe(true)
    expect(series.x_labels?.['__proto__']).toBe('p')
    expect(Object.getPrototypeOf(series.x_labels)).toBe(Object.prototype)
  })

  it('relationsSeries: the groups and their links', () => {
    const model = failureGroupsModel(failureGroupsSeries({ groups: 4 }))
    const series = relationsSeries(model)
    expect(series.nodes).toHaveLength(4)
    expect(series.edges).toEqual(model.edges)
  })
})
