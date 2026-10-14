/**
 * EXEC-04: the waterfall as drawn — one row per lane, one bar per test filled
 * with its status pattern, a click opens the test, the hover tooltip is text
 * (a hostile name is not parsed), and the model's notes are stated.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { RunTimeline, RunTimelineItem } from '@/types/executionMetrics'
import ExecutionTimeline, { MIN_BAR_WIDTH } from './ExecutionTimeline'
import { buildExecutionTimelineModel } from './executionTimeline.model'

const T0 = Date.parse('2026-10-14T10:00:00.000Z')
const iso = (ms: number) => new Date(T0 + ms).toISOString()
const item = (id: string, start: number, end: number, extra: Partial<RunTimelineItem> = {}): RunTimelineItem => ({
  test_id: id,
  test_name: `test_${id}`,
  suite_name: 'checkout',
  status: 'PASSED',
  worker_id: '0',
  started_at: iso(start),
  finished_at: iso(end),
  offset_ms: start,
  duration_ms: end - start,
  ...extra,
})
const timeline = (items: RunTimelineItem[], extra: Partial<RunTimeline> = {}): RunTimeline => ({
  measured: true, reason: null, limit: 2000, total: items.length, truncated: false,
  origin: iso(0), end: iso(Math.max(...items.map((i) => Date.parse(i.finished_at) - T0))), items, ...extra,
})

function draw(items: RunTimelineItem[], extra: Partial<RunTimeline> = {}, onSelect = vi.fn()) {
  const model = buildExecutionTimelineModel(timeline(items, extra))
  if (!model) throw new Error('no model')
  const view = render(<ExecutionTimeline model={model} onSelect={onSelect} />)
  return { ...view, model, onSelect }
}

describe('ExecutionTimeline', () => {
  it('draws one row per worker and one bar per test, the failed bar in the failed pattern', () => {
    const { container } = draw([
      item('a', 0, 1500),
      item('b', 100, 2100, { worker_id: '1' }),
      item('c', 1560, 2760, { status: 'FAILED' }),
    ])
    const lanes = container.querySelectorAll('[data-timeline-lane]')
    expect(Array.from(lanes).map((l) => l.textContent)).toEqual(['Worker 02', 'Worker 11'])
    const bars = container.querySelectorAll('[data-timeline-bar]')
    expect(bars).toHaveLength(3)
    const failed = container.querySelector('[data-timeline-bar][data-test-id="c"]') as SVGRectElement
    expect(failed.getAttribute('data-status')).toBe('failed')
    expect(failed.getAttribute('fill')).toMatch(/^url\(#.+chart-pattern-failed\)$/)
    // The failed pattern exists, with the diagonal decal (status is never hue alone).
    const id = (failed.getAttribute('fill') as string).slice(5, -1)
    expect(container.querySelector(`pattern[id="${id}"]`)?.getAttribute('data-chart-pattern')).toBe('diagonal')
    // Bars on worker 1's row sit on lane 1; lengths follow the clock.
    expect(container.querySelector('[data-test-id="b"]')?.getAttribute('data-lane')).toBe('1')
    const a = container.querySelector('[data-test-id="a"]') as SVGRectElement
    expect(Number(a.getAttribute('x'))).toBe(0)
    expect(Number(a.getAttribute('width'))).toBeGreaterThan(Number(failed.getAttribute('width')))
    // The status legend names only what is drawn.
    expect(Array.from(container.querySelectorAll('[data-legend-status]')).map((e) => e.getAttribute('data-legend-status'))).toEqual(['passed', 'failed'])
  })

  it('a bar of an instantaneous test is still a visible mark', () => {
    const { container } = draw([item('a', 0, 60_000), item('b', 30_000, 30_001, { worker_id: '1' })])
    expect(Number(container.querySelector('[data-test-id="b"]')?.getAttribute('width'))).toBe(MIN_BAR_WIDTH)
  })

  it('clicking a bar opens its test', () => {
    const { container, onSelect } = draw([item('a', 0, 1500), item('c', 1560, 2760, { status: 'FAILED' })])
    fireEvent.click(container.querySelector('[data-test-id="c"]') as Element)
    expect(onSelect).toHaveBeenCalledWith('c')
  })

  it('hovering shows the tooltip as text: a hostile name is shown, never parsed', () => {
    const hostile = '<img src=x onerror=alert(1)>'
    const { container } = draw([item('a', 0, 1500, { test_name: hostile })])
    fireEvent.mouseEnter(container.querySelector('[data-test-id="a"]') as Element)
    const tip = container.querySelector('[data-timeline-tip]') as HTMLElement
    expect(tip).not.toBeNull()
    expect(tip.textContent).toContain(hostile)
    expect(tip.textContent).toContain('Took1.5s')
    expect(tip.querySelector('img')).toBeNull()
    expect(container.querySelector('[data-timeline-mark="hovered"]')).not.toBeNull()
    fireEvent.mouseLeave(container.querySelector('[data-test-id="a"]') as Element)
    expect(container.querySelector('[data-timeline-tip]')).toBeNull()
  })

  it('states the model notes: packed rows are not workers, a cut list is the longest N', () => {
    draw(
      [item('a', 0, 1000, { worker_id: null }), item('b', 200, 800, { worker_id: null })],
      { truncated: true, total: 5000 },
    )
    expect(screen.getByText(/The rows are not workers/)).toBeInTheDocument()
    expect(screen.getByText('Showing the 2 longest of 5,000 timed tests.')).toBeInTheDocument()
    expect(screen.getAllByText(/^Lane \d$/)).toHaveLength(2)
  })

  it('ticks the time axis in the axis unit from zero', () => {
    const { container } = draw([item('a', 0, 9000)])
    const ticks = Array.from(container.querySelectorAll('[data-timeline-axis] text')).map((t) => t.textContent)
    expect(ticks[0]).toBe('0s')
    expect(ticks[ticks.length - 1]).toMatch(/^\d+\.\ds$/)
  })
})
