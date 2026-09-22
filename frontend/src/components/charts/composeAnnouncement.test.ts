import { describe, expect, it } from 'vitest'
import { composeAnnouncement } from './ChartAnnouncer'

describe('composeAnnouncement', () => {
  it('names one chart, counts several', () => {
    expect(composeAnnouncement([{ title: 'Pass rate', change: 'error' }])).toBe('Pass rate chart: error')
    expect(
      composeAnnouncement([
        { title: 'A', change: 'updated' },
        { title: 'B', change: 'updated' },
        { title: 'C', change: 'error' },
      ]),
    ).toBe('2 charts updated, 1 chart: error')
  })

  it('announces a summary line as itself, never as a chart, and first', () => {
    expect(composeAnnouncement([{ title: 'Filtered data', change: 'Showing 3 of 9 runs', kind: 'summary' }])).toBe(
      'Showing 3 of 9 runs',
    )
    expect(
      composeAnnouncement([
        { title: 'A', change: 'updated' },
        { title: 'Filtered data', change: 'Showing 3 of 9 runs', kind: 'summary' },
        { title: 'B', change: 'updated' },
      ]),
    ).toBe('Showing 3 of 9 runs. 2 charts updated')
  })

  it('is empty for no changes', () => {
    expect(composeAnnouncement([])).toBe('')
  })
})
