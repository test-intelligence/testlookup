/**
 * The circle views (VIZ-504): one tab stop, arrows in rank order, Enter =
 * the first intent, the readout beside the plot, hostile names as text.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import SidePanel from '@/components/ui/SidePanel'
import { usePresentationStore } from '@/store/presentationStore'
import { PRESENTATION_MODE_SCALE } from '../framePlotHeight'
import type { ChartMark, MarkIntent } from '../marks'
import { MARK_BUTTON_LABEL_MAX } from '../MarkActions'
import { failureGroupsSeries, HOSTILE_GROUP_LABELS } from './failureGroups.fixtures'
import { failureGroupsModel } from './failureGroups.model'
import FailureGroupBubbles from './FailureGroupBubbles'
import FailureGroupRelations, { RELATIONS_CAPTION } from './FailureGroupRelations'
import { GROUP_PLOT_HINT, GROUP_PLOT_HINT_RESERVE } from './GroupPlot'
import { wordTruncate } from './plot.model'

const announcer = vi.hoisted(() => ({ assertive: vi.fn(), report: vi.fn() }))
vi.mock('../ChartAnnouncer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../ChartAnnouncer')>()),
  useChartAnnouncer: () => announcer,
}))

const model = failureGroupsModel(failureGroupsSeries({ groups: 12, hostile: true }))

function plot(container: HTMLElement) {
  return container.querySelector('[data-group-plot]') as HTMLElement
}

function renderBubbles(props: Partial<Parameters<typeof FailureGroupBubbles>[0]> = {}) {
  return render(
    <FailureGroupBubbles groups={model.groups} height={360} width={900} description="Failure groups as circles" {...props} />,
  )
}

beforeEach(() => {
  announcer.assertive.mockClear()
  usePresentationStore.setState({ enabled: false })
})
afterEach(() => {
  vi.restoreAllMocks()
  usePresentationStore.setState({ enabled: false })
})

describe('FailureGroupBubbles', () => {
  it('draws one circle per group, labels only the five largest that fit, as TEXT', () => {
    const { container } = renderBubbles()
    expect(container.querySelectorAll('[data-group-id]')).toHaveLength(12)
    const labels = [...container.querySelectorAll('[data-group-label]')].map((t) => t.textContent ?? '')
    expect(labels.length).toBeGreaterThan(0)
    expect(labels.length).toBeLessThanOrEqual(5)
    // A hostile name is characters, never an element.
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    // Every circle carries a category pattern, defined in the same svg.
    const fills = [...container.querySelectorAll('[data-group-id] > circle:first-child')].map((c) => c.getAttribute('fill'))
    for (const fill of fills) {
      const id = /^url\(#(.+)\)$/.exec(fill ?? '')?.[1] as string
      expect(container.querySelector(`pattern[id="${id}"]`)).not.toBeNull()
    }
  })

  it('a label that is markup is drawn as its characters (never parsed)', () => {
    const markup = failureGroupsModel({
      kind: 'graph',
      nodes: [
        { id: 'a', label: '<b>bold</b>', size: 40 },
        { id: 'b', label: 'x', size: 2 },
      ],
      edges: [],
    })
    const { container } = render(<FailureGroupBubbles groups={markup.groups} height={360} width={900} description="d" />)
    expect(container.querySelector('svg b')).toBeNull()
    expect(container.querySelector('[data-group-label]')?.textContent).toBe('<b>bold</b>')
  })

  it('every circle is a 24 px target at least', () => {
    const { container } = renderBubbles({ width: 400 })
    const radii = [...container.querySelectorAll('[data-group-id] > circle:first-child')].map((c) => Number(c.getAttribute('r')))
    expect(Math.min(...radii)).toBeGreaterThanOrEqual(12)
  })

  it('is ONE tab stop named by its description, with the keyboard hint', () => {
    const { container } = renderBubbles()
    const surface = plot(container)
    expect(surface).toHaveAttribute('tabindex', '0')
    expect(surface).toHaveAttribute('role', 'group')
    expect(surface).toHaveAccessibleName('Failure groups as circles')
    expect(screen.getByText(GROUP_PLOT_HINT)).toBeInTheDocument()
    expect(container.querySelectorAll('[tabindex="0"]')).toHaveLength(1)
  })

  it('arrows walk the groups in RANK order, each said through the one announcer, and shown in the readout', () => {
    const { container } = renderBubbles()
    const surface = plot(container)
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(surface).toHaveAttribute('data-active-index', '0')
    expect(container.querySelector('[data-group-rank="1"]')).toHaveAttribute('data-active', 'true')
    expect(announcer.assertive).toHaveBeenLastCalledWith(expect.stringContaining(`#1 ${HOSTILE_GROUP_LABELS[0]}`))
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(announcer.assertive).toHaveBeenLastCalledWith(expect.stringContaining('#2 __proto__'))
    const readout = container.querySelector('[data-group-readout]') as HTMLElement
    expect(readout).toHaveTextContent('#2 __proto__')
    expect(readout).toHaveTextContent('CategoryInfrastructure')
    fireEvent.keyDown(surface, { key: 'End' })
    expect(surface).toHaveAttribute('data-active-index', '11')
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(surface).toHaveAttribute('data-active-index', '')
  })

  it('without a handler: no buttons, Enter does nothing, and the readout invites the reader', () => {
    const { container } = renderBubbles()
    const surface = plot(container)
    expect(container.querySelector('[data-group-readout]')).toHaveTextContent('Point at a circle')
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
    expect(fireEvent.keyDown(surface, { key: 'Enter' })).toBe(true) // not prevented
  })

  describe('with a handler', () => {
    const intentsFor = (intents: MarkIntent[]) => () => intents

    it('Enter hands the focused group and the FIRST intent; Shift+Enter filters only where offered', () => {
      const onMarkActivate = vi.fn<(mark: ChartMark, intent: MarkIntent) => void>()
      const { container } = renderBubbles({ onMarkActivate, markIntents: intentsFor(['drill', 'rows']) })
      const surface = plot(container)
      fireEvent.keyDown(surface, { key: 'ArrowRight' })
      fireEvent.keyDown(surface, { key: 'Enter' })
      expect(onMarkActivate).toHaveBeenLastCalledWith(
        { dimension: 'error_signature', value: model.groups[0].id, label: HOSTILE_GROUP_LABELS[0], y: 400, n: null },
        'drill',
      )
      fireEvent.keyDown(surface, { key: 'Enter', shiftKey: true })
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: model.groups[0].id }), 'drill')
    })

    it('the readout offers every intent as a button for the keyboard’s group', () => {
      const onMarkActivate = vi.fn()
      const { container } = renderBubbles({ onMarkActivate, markIntents: intentsFor(['drill', 'rows']) })
      fireEvent.keyDown(plot(container), { key: 'ArrowRight' })
      fireEvent.keyDown(plot(container), { key: 'ArrowRight' })
      const actions = container.querySelector('[data-mark-actions]') as HTMLElement
      const buttons = within(actions).getAllByRole('button')
      expect(buttons.map((b) => b.textContent)).toEqual(['Drill into __proto__', 'View rows'])
      fireEvent.click(buttons[1])
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: model.groups[1].id }), 'rows')
    })

    it('a click acts at once (Shift = filter when offered); a touch selects and shows the buttons', () => {
      const onMarkActivate = vi.fn()
      const { container } = renderBubbles({ onMarkActivate, markIntents: intentsFor(['drill', 'rows', 'filter']) })
      const third = container.querySelector('[data-group-rank="3"]') as Element
      fireEvent.click(third)
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: model.groups[2].id }), 'drill')
      fireEvent.click(third, { shiftKey: true })
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: model.groups[2].id }), 'filter')
      onMarkActivate.mockClear()
      const touch = new MouseEvent('click', { bubbles: true })
      Object.defineProperty(touch, 'pointerType', { value: 'touch' })
      fireEvent(container.querySelector('[data-group-rank="4"]') as Element, touch)
      expect(onMarkActivate).not.toHaveBeenCalled()
      const readout = container.querySelector('[data-group-readout]') as HTMLElement
      expect(readout).toHaveTextContent(`#4 ${HOSTILE_GROUP_LABELS[3]}`)
      expect(within(readout).getAllByRole('button')).toHaveLength(3)
    })

    // R2-B F-23: "Drill into TimeoutError: waiting fo… / imeout 30000ms exceeded" split words at the cut.
    it('the readout’s button names a long group cut on WORD boundaries, and hands the whole mark', () => {
      const onMarkActivate = vi.fn()
      const { container } = renderBubbles({ onMarkActivate, markIntents: intentsFor(['drill']) })
      for (let i = 0; i < 4; i++) fireEvent.keyDown(plot(container), { key: 'ArrowRight' })
      const label = HOSTILE_GROUP_LABELS[3]
      const button = within(container.querySelector('[data-mark-actions]') as HTMLElement).getByRole('button')
      expect(button.textContent).toBe(`Drill into ${wordTruncate(label, MARK_BUTTON_LABEL_MAX)}`)
      expect(button.textContent).toBe('Drill into TimeoutError: waiting…selector #checkout-button')
      fireEvent.click(button)
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: model.groups[3].id, label }), 'drill')
    })

    // X4 request 1 (R1B-4 / F-24 on the circle views): a readout button opens a panel; closing it must not drop
    // focus on <body> because the button is gone: focus goes to the plot BEFORE the action.
    it('a readout button hands focus to the plot first, so closing the panel it opens returns there', () => {
      function Host() {
        const [open, setOpen] = useState(false)
        return (
          <>
            <FailureGroupBubbles
              groups={model.groups}
              height={360}
              width={900}
              description="Failure groups as circles"
              onMarkActivate={() => setOpen(true)}
              markIntents={intentsFor(['rows'])}
            />
            <SidePanel open={open} onClose={() => setOpen(false)} title="Rows" closeLabel="Close executions">
              <p>rows</p>
            </SidePanel>
          </>
        )
      }
      const { container } = render(<Host />)
      const surface = plot(container)
      surface.focus()
      fireEvent.keyDown(surface, { key: 'ArrowRight' })
      const button = within(container.querySelector('[data-mark-actions]') as HTMLElement).getByRole('button', { name: 'View rows' })
      button.focus()
      fireEvent.click(button)
      const close = screen.getByRole('button', { name: 'Close executions' })
      expect(document.activeElement).toBe(close)
      fireEvent.click(close)
      expect(document.activeElement).toBe(surface)
    })

    it('a hover shows the group in the readout without buttons (a pointer acts on the circle)', () => {
      const { container } = renderBubbles({ onMarkActivate: vi.fn(), markIntents: intentsFor(['drill']) })
      const fifth = container.querySelector('[data-group-rank="5"]') as Element
      fireEvent.pointerOver(fifth)
      const readout = container.querySelector('[data-group-readout]') as HTMLElement
      expect(readout).toHaveTextContent('#5')
      expect(container.querySelector('[data-mark-actions]')).toBeNull()
      fireEvent.pointerOut(fifth)
      expect(readout).toHaveTextContent('Point at a circle')
    })
  })

  it('marks the selected group with a dashed ring', () => {
    const { container } = renderBubbles({ selectedId: model.groups[1].id })
    expect(container.querySelector('[data-group-rank="2"] [data-group-selected]')).not.toBeNull()
    expect(container.querySelectorAll('[data-group-selected]')).toHaveLength(1)
  })

  it('says when the smallest circles are drawn larger than their counts', () => {
    const many = failureGroupsModel(failureGroupsSeries({ groups: 150 }))
    const { container } = render(<FailureGroupBubbles groups={many.groups} height={300} width={700} description="d" />)
    expect(container.querySelector('[data-group-layout-note]')).toHaveTextContent(
      /^Groups with fewer than \d+ failures are drawn at the smallest size/,
    )
  })

  // R2-B F-02: the plot sat at the left of a 940 px frame, the readout column empty beside it.
  it('centres the plot in the room the readout leaves, the readout a fixed column at the right', () => {
    const { container } = renderBubbles()
    const area = container.querySelector('[data-group-plot-area]') as HTMLElement
    expect(area.style.flex).toBe('1 1 0%')
    expect(area.style.justifyContent).toBe('center')
    expect(area.style.minWidth).toBe('0px')
    expect(area.querySelector('svg')).toHaveAttribute('width', '360')
    const readout = container.querySelector('[data-group-readout]') as HTMLElement
    expect(readout.style.flex).toBe('0 0 240px')
  })

  // R2-B F-21: the hint chip (drawn under the plot while it has keyboard focus) covered the table's caption.
  it('reserves the keyboard hint’s line under the plot', () => {
    const { container } = renderBubbles()
    const outer = plot(container).parentElement as HTMLElement
    expect(outer.style.paddingBottom).toBe(`${GROUP_PLOT_HINT_RESERVE}px`)
    expect(GROUP_PLOT_HINT_RESERVE).toBeGreaterThanOrEqual(16)
    const hint = screen.getByText(GROUP_PLOT_HINT)
    expect(hint).toHaveClass('absolute', 'top-full')
  })

  // R2-B F-22: the circle labels stayed 11 px while every other chart grew in presentation mode.
  it('draws the circle labels at the presentation scale', () => {
    const { container } = renderBubbles()
    const labelSize = () => Number(container.querySelector('[data-group-label]')?.getAttribute('font-size'))
    expect(labelSize()).toBe(11)
    expect(plot(container).querySelector('[data-chart-presentation]')).toHaveAttribute('data-chart-presentation', '1')
    act(() => usePresentationStore.setState({ enabled: true }))
    expect(labelSize()).toBeCloseTo(16, 5)
    expect(plot(container).querySelector('[data-chart-presentation]')).toHaveAttribute(
      'data-chart-presentation',
      String(PRESENTATION_MODE_SCALE),
    )
  })

  it('draws nothing before it has a width', () => {
    const { container } = render(<FailureGroupBubbles groups={model.groups} height={300} description="d" />)
    expect(container.querySelector('svg')).toBeNull()
  })
})

describe('FailureGroupRelations', () => {
  it('captions the view in the EPIC words and draws the links', () => {
    const { container } = render(
      <FailureGroupRelations groups={model.groups} edges={model.edges} height={360} width={900} description="Related groups" />,
    )
    expect(container.querySelector('[data-group-relations-caption]')).toHaveTextContent(RELATIONS_CAPTION)
    expect(RELATIONS_CAPTION).toBe('Linked groups fail in the same tests. Position has no other meaning.')
    expect(container.querySelectorAll('[data-group-link]').length).toBe(model.edges.length)
    expect(plot(container)).toHaveAttribute('data-group-plot', 'relations')
  })

  it('lays out the 60 largest and says so', () => {
    const many = failureGroupsModel(failureGroupsSeries({ groups: 80 }))
    const { container } = render(
      <FailureGroupRelations groups={many.groups} edges={many.edges} height={360} width={900} description="d" />,
    )
    expect(container.querySelectorAll('[data-group-id]')).toHaveLength(60)
    expect(container.querySelector('[data-group-relations-caption]')).toHaveTextContent('Showing the 60 largest groups.')
  })

  it('highlights the links of the group in the readout', () => {
    const { container } = render(
      <FailureGroupRelations groups={model.groups} edges={model.edges} height={360} width={900} description="d" />,
    )
    const edge = model.edges[0]
    const index = model.groups.findIndex((g) => g.id === edge.source)
    const surface = plot(container)
    for (let i = 0; i <= index; i++) fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const strong = [...container.querySelectorAll('[data-group-link]')].filter((l) => l.getAttribute('stroke-opacity') === '0.9')
    expect(strong.length).toBeGreaterThan(0)
  })
})
