/**
 * TRND-10 — the Explorer's Customise spec: a title and lines per panel, the
 * kit's guard rails, and `Pick "Lines" first` while no series is drawn.
 */
import { describe, expect, it } from 'vitest'
import {
  applyExploreField,
  exploreCustomiseSpec,
  exploreUnavailable,
  isDefaultExploreCustomise,
  LINES_DEFAULT,
  LINES_NEED_SERIES_REASON,
  resetExploreCustomise,
} from './exploreCustomise'
import { EXPLORE_TITLE_MAX } from './exploreModel'
import { DEFAULT_EXPLORE } from './exploreUrl'

const field = (spec: ReturnType<typeof exploreCustomiseSpec>, id: string) => {
  const found = spec.fields.find((f) => f.id === id)
  if (!found) throw new Error(id)
  return found
}

describe('exploreCustomiseSpec', () => {
  it('the default: a blank title, the default lines, line / linear only, the guard rails with reasons, nothing to reset', () => {
    const spec = exploreCustomiseSpec(DEFAULT_EXPLORE)
    expect(spec.fields.map((f) => f.id)).toEqual(['title', 'topN', 'chartType', 'scale'])
    expect(field(spec, 'title')).toMatchObject({ kind: 'text', value: '', maxLength: EXPLORE_TITLE_MAX })
    const lines = field(spec, 'topN')
    expect(lines.value).toBe(LINES_DEFAULT)
    expect(lines.options?.filter((o) => !o.disabled).map((o) => o.value)).toEqual([LINES_DEFAULT, '3', '5', '7'])
    expect(lines.options?.find((o) => o.label === 'More than 7')).toMatchObject({ disabled: true })
    expect(field(spec, 'chartType').options?.map((o) => [o.label, Boolean(o.disabled)])).toEqual([
      ['Line', false],
      ['Pie', true],
      ['Stacked area', true],
    ])
    expect(field(spec, 'scale').options?.map((o) => [o.label, Boolean(o.disabled)])).toEqual([
      ['Linear', false],
      ['Log', true],
    ])
    expect(spec.isDefault).toBe(true)
    // Failure rate is a rate: the stacked / log reasons are the rate ones.
    expect(spec.unavailable.find((o) => o.label === 'Stacked area')?.reason).toMatch(/Rates cannot be stacked/)
    expect(spec.unavailable.find((o) => o.label === 'Log')?.reason).toMatch(/cannot show 0%/)
  })

  it('a count metric gives the count reasons', () => {
    const unavailable = exploreUnavailable({ metric: 'executions', series: 'environment' })
    expect(unavailable.find((o) => o.label === 'Stacked area')?.reason).toMatch(/Stacking hides/)
    expect(unavailable.find((o) => o.label === 'Log')?.reason).toMatch(/a day with 0/)
  })

  it('no series: lines per panel is listed as unavailable with "Pick Lines first", and every option is disabled', () => {
    const spec = exploreCustomiseSpec({ ...DEFAULT_EXPLORE, series: null })
    expect(spec.unavailable[0]).toEqual({ control: 'topN', label: 'Lines per panel', reason: LINES_NEED_SERIES_REASON })
    const lines = field(spec, 'topN')
    expect(lines.options?.every((o) => o.disabled)).toBe(true)
    expect(lines.options?.filter((o) => o.reason === LINES_NEED_SERIES_REASON).map((o) => o.value)).toEqual([LINES_DEFAULT, '3', '5', '7'])
  })

  it('a customised config is not the default, and says so', () => {
    expect(exploreCustomiseSpec({ ...DEFAULT_EXPLORE, topN: 3 }).isDefault).toBe(false)
    expect(exploreCustomiseSpec({ ...DEFAULT_EXPLORE, title: 'Mine' }).isDefault).toBe(false)
    expect(isDefaultExploreCustomise({ topN: null, title: '   ' })).toBe(true)
  })
})

describe('applyExploreField / resetExploreCustomise', () => {
  it('sets the lines per panel, back to the default, and ignores anything else', () => {
    expect(applyExploreField(DEFAULT_EXPLORE, 'topN', '3').topN).toBe(3)
    expect(applyExploreField({ ...DEFAULT_EXPLORE, topN: 5 }, 'topN', LINES_DEFAULT).topN).toBeNull()
    for (const bad of ['4', '8', '3.0', 'unavailable:More than 7', '']) {
      expect(applyExploreField(DEFAULT_EXPLORE, 'topN', bad), bad).toBe(DEFAULT_EXPLORE)
    }
    // No series: nothing to cap.
    const none = { ...DEFAULT_EXPLORE, series: null }
    expect(applyExploreField(none, 'topN', '3')).toBe(none)
    // The metric, axis, lines and panels are the Explorer's own pickers, not the panel's.
    expect(applyExploreField(DEFAULT_EXPLORE, 'metric', 'pass_rate')).toBe(DEFAULT_EXPLORE)
    expect(applyExploreField(DEFAULT_EXPLORE, 'chartType', 'unavailable:Pie')).toBe(DEFAULT_EXPLORE)
  })

  it('sets a title as typed, at most 80 characters; blank removes it', () => {
    expect(applyExploreField(DEFAULT_EXPLORE, 'title', 'Branch ').title).toBe('Branch ')
    expect(applyExploreField(DEFAULT_EXPLORE, 'title', 'x'.repeat(90)).title).toHaveLength(EXPLORE_TITLE_MAX)
    const cleared = applyExploreField({ ...DEFAULT_EXPLORE, title: 'Mine' }, 'title', '  ')
    expect(cleared).not.toHaveProperty('title')
  })

  it('reset clears both and nothing else', () => {
    const custom = { ...DEFAULT_EXPLORE, metric: 'pass_rate' as const, topN: 7 as const, title: 'Mine' }
    expect(resetExploreCustomise(custom)).toEqual({ ...DEFAULT_EXPLORE, metric: 'pass_rate', topN: null })
  })
})
