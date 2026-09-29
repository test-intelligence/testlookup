import { describe, expect, it } from 'vitest'
import {
  DEFAULT_TEXT,
  INTENSITY_THRESHOLDS,
  MIXED_BAND_HEIGHT,
  MIXED_DAY_FILL,
  buildDayStripModel,
  cellFace,
  countTones,
  dayWindow,
  intensityLevel,
  intensityRangeText,
  severityShare,
  type DayStripCell,
  type DayStripLevel,
  type DayStripMode,
  type DayStripTone,
} from './dayStrip.model'
import { NON_TEXT_EDGE } from './tokens'

describe('intensityLevel — Coverage buckets 0 / 1-5 / 6-20 / 21-50 / more than 50', () => {
  it.each([
    [0, 0],
    [1, 1],
    [5, 1],
    [6, 2],
    [20, 2],
    [21, 3],
    [50, 3],
    [51, 4],
    [10_000, 4],
  ])('%i executions is level %i', (count, level) => {
    expect(intensityLevel(count)).toBe(level)
  })

  it('reads a negative or non-finite count as level 0, never a high level', () => {
    expect(intensityLevel(-3)).toBe(0)
    expect(intensityLevel(Number.NaN)).toBe(0)
  })

  it('names each level by the range it stands for', () => {
    expect(INTENSITY_THRESHOLDS).toEqual([0, 5, 20, 50])
    expect(([0, 1, 2, 3, 4] as DayStripLevel[]).map(intensityRangeText)).toEqual([
      '0',
      '1-5',
      '6-20',
      '21-50',
      'more than 50',
    ])
  })
})

describe('dayWindow', () => {
  it('has one day per day of the window, oldest first, ending today', () => {
    const days = dayWindow(14, '2026-09-28')
    expect(days).toHaveLength(14)
    expect(days[0]).toBe('2026-09-15')
    expect(days[13]).toBe('2026-09-28')
    expect(new Set(days).size).toBe(14)
  })

  it('crosses a month and a year without skipping a day', () => {
    expect(dayWindow(3, '2027-01-01')).toEqual(['2026-12-30', '2026-12-31', '2027-01-01'])
    expect(dayWindow(90, '2026-09-28')).toHaveLength(90)
  })

  it('is empty for no days', () => {
    expect(dayWindow(0, '2026-09-28')).toEqual([])
    expect(dayWindow(-2, '2026-09-28')).toEqual([])
  })
})

describe('cellFace', () => {
  it('presence: a mixed day keeps the 80/20 mixed-day fill and carries the band cue', () => {
    expect(cellFace('presence', 'mixed')).toMatchObject({ background: MIXED_DAY_FILL, cue: 'mixed' })
    expect(cellFace('presence', 'pass')).toMatchObject({ background: 'var(--status-passed)', cue: null })
    expect(cellFace('presence', 'none')).toMatchObject({ background: 'var(--color-bg-secondary)', cue: null })
  })

  it('never lets a mixed day’s failed band shrink under 3 px, and the fill splits where the band starts (R2 G4)', () => {
    expect(MIXED_BAND_HEIGHT).toBe('max(20%, 3px)')
    // Passed down to the band, failed from it: one boundary, the band's.
    expect(MIXED_DAY_FILL).toBe(
      'linear-gradient(180deg, var(--status-passed) 0 calc(100% - max(20%, 3px)), var(--status-failed) calc(100% - max(20%, 3px)) 100%)',
    )
  })

  it('outlines an empty day and edges a failed day so each is at least 3:1 on the card (R2 F9)', () => {
    for (const mode of ['presence', 'status'] as const) {
      expect(cellFace(mode, 'none').border).toBe(`1px solid ${NON_TEXT_EDGE}`)
    }
    expect(cellFace('intensity', 'none', 0).border).toBe(`1px solid ${NON_TEXT_EDGE}`)
    // The faintest failure still has a full-strength edge; the shade keeps the severity.
    for (const severity of [0, 0.3, 1, undefined]) {
      expect(cellFace('status', 'fail', 0, severity).border).toBe('1px solid var(--status-failed)')
    }
  })

  it('intensity: the fill is the level, never the tone', () => {
    const shares = ([0, 1, 2, 3, 4] as DayStripLevel[]).map((level) => cellFace('intensity', 'pass', level).background)
    expect(shares).toEqual([
      'var(--color-bg-secondary)',
      'color-mix(in srgb, var(--status-passed) 18%, transparent)',
      'color-mix(in srgb, var(--status-passed) 40%, transparent)',
      'color-mix(in srgb, var(--status-passed) 70%, transparent)',
      'var(--status-passed)',
    ])
  })

  it('intensity draws the failure cue only where the page marked a failure (Coverage marks none)', () => {
    expect(cellFace('intensity', 'pass', 3).cue).toBeNull()
    expect(cellFace('intensity', 'mixed', 3)).toMatchObject({
      cue: 'mixed',
      background: 'color-mix(in srgb, var(--status-passed) 70%, transparent)',
    })
  })

  it('status: a pass is the soft 70 %, a fail deepens with its severity', () => {
    expect(cellFace('status', 'pass').background).toBe('color-mix(in srgb, var(--status-passed) 70%, transparent)')
    expect(cellFace('status', 'fail')).toMatchObject({ background: 'var(--status-failed)', cue: 'fail' })
    expect(cellFace('status', 'fail', 0, 0).background).toBe('color-mix(in srgb, var(--status-failed) 45%, transparent)')
    expect(cellFace('status', 'fail', 0, 0.5).background).toBe('color-mix(in srgb, var(--status-failed) 73%, transparent)')
    expect(cellFace('status', 'fail', 0, 1).background).toBe('var(--status-failed)')
  })

  it('clamps a severity outside 0..1 and ignores a non-finite one', () => {
    expect(severityShare(4)).toBe(100)
    expect(severityShare(-1)).toBe(45)
    expect(severityShare(Number.NaN)).toBe(100)
    expect(severityShare(undefined)).toBe(100)
  })

  it('uses tokens only: no colour literal in any face', () => {
    const modes: DayStripMode[] = ['presence', 'intensity', 'status']
    const tones: DayStripTone[] = ['none', 'pass', 'fail', 'mixed']
    for (const mode of modes) {
      for (const tone of tones) {
        for (const level of [0, 1, 2, 3, 4] as DayStripLevel[]) {
          const face = cellFace(mode, tone, level, 0.3)
          const painted = `${face.background} ${face.border}`
          expect(painted).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i)
          expect(painted).toMatch(/var\(--|transparent/)
        }
      }
    }
  })
})

describe('buildDayStripModel', () => {
  const cells: DayStripCell[] = [
    { key: '2026-09-26', label: 'Sep 26 · no runs', tone: 'none', marker: 'gap-edge' },
    { key: '2026-09-27', label: 'Sep 27 · 4 executions (1 failed)', tone: 'mixed' },
    { key: '2026-09-28', label: 'Sep 28 · 9 executions', tone: 'pass', marker: 'today' },
  ]

  it('keeps every cell, in order, with its state and marker in words', () => {
    const model = buildDayStripModel(cells, 'presence')
    expect(model.cells.map((c) => c.key)).toEqual(['2026-09-26', '2026-09-27', '2026-09-28'])
    expect(model.cells.map((c) => c.state)).toEqual(['No runs', 'Runs with failures', 'Runs'])
    expect(model.cells.map((c) => c.markerText)).toEqual([DEFAULT_TEXT.presence.gapEdge, null, 'Today'])
    expect(model.cells[2].text).toBe('Sep 28 · 9 executions. Runs. Today')
    expect(model.counts).toEqual({ none: 1, pass: 1, fail: 0, mixed: 1 })
  })

  it('lets a page rename states and markers (a build strip is not a day strip)', () => {
    const model = buildDayStripModel(cells, 'status', { none: 'No build', today: 'Latest build' })
    expect(model.cells[0].state).toBe('No build')
    expect(model.cells[2].markerText).toBe('Latest build')
  })

  it('presence legend: two entries for a clean window, the failure cue only when a failure is drawn', () => {
    const clean = cells.map((c) => ({ ...c, tone: c.tone === 'mixed' ? ('pass' as const) : c.tone }))
    expect(buildDayStripModel(clean, 'presence').legend.map((l) => l.key)).toEqual(['none', 'pass'])
    const legend = buildDayStripModel(cells, 'presence').legend
    expect(legend.map((l) => l.key)).toEqual(['none', 'pass', 'mixed'])
    expect(legend[2].face.cue).toBe('mixed')
  })

  it('status legend: pass, fail and nothing, fail with its cue', () => {
    const legend = buildDayStripModel(cells.filter((c) => c.tone !== 'mixed'), 'status').legend
    expect(legend.map((l) => l.key)).toEqual(['pass', 'fail', 'none'])
    expect(legend[1].face.cue).toBe('fail')
  })

  it('intensity: the state names the level and its range; the legend runs 0, 2, 4', () => {
    const model = buildDayStripModel(
      [
        { key: 'a', label: 'a', tone: 'none', level: 0 },
        { key: 'b', label: 'b', tone: 'pass', level: 2 },
      ],
      'intensity',
    )
    expect(model.cells.map((c) => c.state)).toEqual(['Activity level 0 of 4 (0)', 'Activity level 2 of 4 (6-20)'])
    expect(model.legend.map((l) => l.key)).toEqual(['level-0', 'level-2', 'level-4'])
  })

  it('countTones counts each tone', () => {
    expect(countTones([{ tone: 'fail' }, { tone: 'fail' }, { tone: 'none' }])).toEqual({ none: 1, pass: 0, fail: 2, mixed: 0 })
  })
})
