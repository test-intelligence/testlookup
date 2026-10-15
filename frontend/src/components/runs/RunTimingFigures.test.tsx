/**
 * EXEC-04b: the run header's two durations. The summed test time is labelled
 * as such and never stands in for a wall clock the report did not carry.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import RunTimingFigures from './RunTimingFigures'
import { runParallelism, wallClockReason } from './runHeaderTiming'

const figure = (key: string) => document.querySelector(`[data-run-timing-figure="${key}"]`) as HTMLElement | null

describe('RunTimingFigures', () => {
  it('a timed run: wall clock, test time (summed) and the parallelism between them', () => {
    render(<RunTimingFigures run={{ wall_clock_ms: 9_000, duration_ms: 17_400, timing_source: 'derived' }} />)
    expect(document.querySelector('[data-run-timing]')).toHaveTextContent('Wall clock9.0sTest time (summed)17.4sParallelism1.9×')
    expect(screen.getByText('Test time (summed)')).toBeInTheDocument()
  })

  it('no wall clock (a report with no timestamps): "not reported", why in the tooltip, and no parallelism', () => {
    render(<RunTimingFigures run={{ wall_clock_ms: null, duration_ms: 12_456, timing_source: 'derived_sum' }} />)
    expect(figure('wall')).toHaveTextContent('not reported')
    expect(figure('wall')?.parentElement).toHaveAttribute('title', wallClockReason({ timing_source: 'derived_sum' }))
    expect(figure('summed')).toHaveTextContent('12.5s')
    // The summed time is shown once, as the summed time — never in the wall-clock slot.
    expect(screen.getAllByText('12.5s')).toHaveLength(1)
    expect(figure('parallelism')).toBeNull()
  })

  it('an older API (no timing fields at all): wall clock not reported, the summed time kept', () => {
    render(<RunTimingFigures run={{ duration_ms: 3_000 }} />)
    expect(figure('wall')).toHaveTextContent('not reported')
    expect(figure('summed')).toHaveTextContent('3.0s')
  })

  it('the reasons and the ratio', () => {
    expect(wallClockReason({ timing_source: null })).toBe('The run has not been timed yet.')
    expect(runParallelism({ wall_clock_ms: 9_000, duration_ms: 17_400 })).toBe(1.9)
    expect(runParallelism({ wall_clock_ms: 0, duration_ms: 17_400 })).toBeNull()
    expect(runParallelism({ wall_clock_ms: null, duration_ms: 17_400 })).toBeNull()
  })
})
