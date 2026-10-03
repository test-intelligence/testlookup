/**
 * FK0 M0b: the drill path and the rows panel in the URL (C5 URL encoding).
 * Pure parse/write first, then the hook against a real memory router so a
 * PUSH and a REPLACE are what the history actually did.
 */
import { act, render } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  decodeDrillLevel,
  DRILL_DROP_WORDS,
  encodeDrillLevel,
  isRowsOwner,
  MAX_DRILL_URL_CHARS,
  ownedRows,
  parseDrillParams,
  readDrillLevels,
  useDrillPath,
  writeDrillParams,
  type DrillState,
} from './useDrillPath'

describe('encode / decode', () => {
  it('splits on the FIRST ~ only: a value may contain ~', () => {
    expect(decodeDrillLevel('test~a~b')).toEqual({ dimension: 'test', value: 'a~b' })
    expect(decodeDrillLevel('suite~')).toEqual({ dimension: 'suite', value: '' })
    expect(decodeDrillLevel('suite')).toBeNull()
    expect(encodeDrillLevel({ dimension: 'test', value: 'a~b' })).toBe('test~a~b')
  })

  it('round-trips hostile values through URLSearchParams alone', () => {
    const levels = [
      { dimension: 'suite' as const, value: '<img src=x onerror="window.__xss=1">&drill=status~failed' },
      { dimension: 'test' as const, value: '__proto__ %zz ~~ 💥\n' },
    ]
    const written = writeDrillParams('', { path: levels })
    const back = parseDrillParams(written.toString())
    expect(back.path).toEqual(levels)
    expect(back.dropped).toEqual([])
  })
})

describe('parseDrillParams', () => {
  it('reads the path in order and the rows selectors', () => {
    const parsed = parseDrillParams(
      '?drill=suite~payments&drill=status~failed&rows=by~trends-heatmap&rows=suite~payments&rows=day~2026-09-12',
    )
    expect(parsed.path).toEqual([
      { dimension: 'suite', value: 'payments' },
      { dimension: 'status', value: 'failed' },
    ])
    expect(parsed.rows).toEqual([
      { dimension: 'suite', value: 'payments' },
      { dimension: 'day', value: '2026-09-12' },
    ])
    expect(parsed.rowsOwner).toBe('trends-heatmap')
    expect(parsed.dropped).toEqual([])
  })

  it('applies a rows list only after a valid owner entry, and says so otherwise (FK4-1)', () => {
    for (const query of [
      '?rows=test~fp-1', // untagged: no host could claim it
      '?rows=test~fp-1&rows=by~scatter-suite', // the owner must come FIRST
      '?rows=by~Scatter&rows=test~fp-1', // not an owner id
      '?rows=by~&rows=test~fp-1',
      `?rows=by~${'a'.repeat(65)}&rows=test~fp-1`,
      '?rows=by~scatter-suite', // an owner with nothing selected
    ]) {
      const parsed = parseDrillParams(query)
      expect(parsed.rows, query).toEqual([])
      expect(parsed.rowsOwner, query).toBeNull()
      expect(parsed.dropped, query).toEqual([DRILL_DROP_WORDS.invalid])
    }
    const ok = parseDrillParams(`?rows=by~${'a'.repeat(64)}&rows=test~fp-1`)
    expect(ok.rowsOwner).toBe('a'.repeat(64))
    expect(ok.rows).toEqual([{ dimension: 'test', value: 'fp-1' }])
    expect(parseDrillParams('?rows=by~heatmap-test_run&rows=test~fp-1').rowsOwner).toBe('heatmap-test_run')
  })

  it('ownedRows hands the selection to its owner only, the same array each time', () => {
    const parsed = parseDrillParams('?rows=by~heatmap-test_run&rows=test~fp-1')
    expect(ownedRows(parsed, 'heatmap-test_run')).toBe(parsed.rows)
    expect(ownedRows(parsed, 'scatter-suite')).toEqual([])
    expect(ownedRows(parsed, 'scatter-suite')).toBe(ownedRows({ rows: [], rowsOwner: null }, 'x'))
    expect(isRowsOwner('failures-drill')).toBe(true)
    expect(isRowsOwner('1abc')).toBe(false)
    expect(isRowsOwner(undefined)).toBe(false)
  })

  it('truncates at the first invalid level and says so, never throws', () => {
    for (const [query, kept] of [
      ['?drill=suite~a&drill=nope&drill=status~failed', 1],
      ['?drill=suite~a&drill=planet~x', 1],
      ['?drill=suite~a&drill=suite~b', 1],
      ['?drill=status~exploded', 0],
      ['?drill=suite~', 0],
      [`?drill=suite~${'x'.repeat(2001)}`, 0],
      ['?drill=suite~a&drill=status~failed&drill=test~t&drill=release~r&drill=day~2026-01-01', 4],
    ] as const) {
      const parsed = parseDrillParams(query)
      expect(parsed.path, query).toHaveLength(kept)
      expect(parsed.dropped, query).toEqual([DRILL_DROP_WORDS.invalid])
    }
    expect(parseDrillParams(null)).toEqual({ path: [], rows: [], rowsOwner: null, dropped: [] })
    expect(parseDrillParams(undefined).path).toEqual([])
  })

  it('drops the DEEPEST drill level while the encoded keys exceed the cap', () => {
    // 2,002 + 19 + 2,001 + 2,004 + 3 separators = 6,029 characters: one level over.
    const big = 'x'.repeat(1990)
    const query = writeDrillParams('', {
      path: [
        { dimension: 'suite', value: big },
        { dimension: 'status', value: 'failed' },
        { dimension: 'test', value: big },
        { dimension: 'release', value: big },
      ],
    }).toString()
    expect(query.length).toBeGreaterThan(MAX_DRILL_URL_CHARS)
    const parsed = parseDrillParams(query)
    expect(parsed.path.map((l) => l.dimension)).toEqual(['suite', 'status', 'test'])
    expect(parsed.dropped).toEqual([DRILL_DROP_WORDS.tooLong])
  })

  it('with both keys over the cap, the DRILL level goes first and the open panel stays', () => {
    const big = 'z'.repeat(1990)
    const query = writeDrillParams('', {
      path: [
        { dimension: 'suite', value: big },
        { dimension: 'test', value: big },
      ],
      rows: [
        { dimension: 'suite', value: big },
        { dimension: 'day', value: '2026-09-12' },
      ],
      rowsOwner: 'trends-heatmap',
    }).toString()
    const parsed = parseDrillParams(query)
    expect(parsed.path.map((l) => l.dimension)).toEqual(['suite'])
    expect(parsed.rows.map((l) => l.dimension)).toEqual(['suite', 'day'])
    expect(parsed.rowsOwner).toBe('trends-heatmap')
  })

  it('then drops rows selectors when the path alone cannot make it fit', () => {
    // Four ~1,980-character selectors + the owner entry (`rows=by~coverage-map`) are over the cap; three fit.
    const big = 'y'.repeat(1970)
    const query = writeDrillParams('', {
      rows: ['suite', 'test', 'release', 'environment'].map((dimension) => ({ dimension: dimension as 'suite', value: big })),
      rowsOwner: 'coverage-map',
    }).toString()
    const parsed = parseDrillParams(query)
    expect(parsed.rows.length).toBe(3)
    expect(parsed.rowsOwner).toBe('coverage-map')
    expect(parsed.dropped).toEqual([DRILL_DROP_WORDS.tooLong])
  })

  it('readDrillLevels reports whether it truncated', () => {
    expect(readDrillLevels(['suite~a'])).toEqual({ levels: [{ dimension: 'suite', value: 'a' }], truncated: false })
    expect(readDrillLevels(['suite~a', 'x'])).toEqual({ levels: [{ dimension: 'suite', value: 'a' }], truncated: true })
  })
})

describe('writeDrillParams', () => {
  it('replaces only drill and rows; every other key keeps value, order and repetition', () => {
    const base = 'suite=legacy&drill=suite~old&tab=x&rows=by~trends-heatmap&rows=day~1&tab=y&release=r1'
    const next = writeDrillParams(base, { path: [{ dimension: 'suite', value: 'new' }] })
    expect(next.getAll('drill')).toEqual(['suite~new'])
    expect(next.getAll('rows')).toEqual(['by~trends-heatmap', 'day~1'])
    expect(next.getAll('tab')).toEqual(['x', 'y'])
    expect(next.get('suite')).toBe('legacy')
    expect(next.get('release')).toBe('r1')
    expect(writeDrillParams(base, { rows: [] }).has('rows')).toBe(false)
  })

  it('writes the owner entry FIRST, and no selection without a valid owner', () => {
    const rows = [{ dimension: 'test' as const, value: 'fp-1' }]
    expect(writeDrillParams('', { rows, rowsOwner: 'scatter-suite' }).getAll('rows')).toEqual(['by~scatter-suite', 'test~fp-1'])
    expect(writeDrillParams('rows=by~x&rows=test~old', { rows }).has('rows')).toBe(false)
    expect(writeDrillParams('', { rows, rowsOwner: 'Not An Id' }).has('rows')).toBe(false)
  })
})

// ── The hook, on a real memory router ─────────────────────────────────────────

let router: ReturnType<typeof createMemoryRouter>
let drill!: DrillState

const expose = (state: DrillState) => {
  drill = state
}

function Host() {
  expose(useDrillPath())
  return null
}

function mount(initial: string) {
  router = createMemoryRouter([{ path: '/failures', element: <Host /> }], { initialEntries: [initial] })
  render(<RouterProvider router={router} />)
}

const search = () => new URLSearchParams(router.state.location.search)

describe('useDrillPath', () => {
  beforeEach(() => mount('/failures?suites=a&drill=suite~payments'))

  it('reads the path from the URL', () => {
    expect(drill.path).toEqual([{ dimension: 'suite', value: 'payments' }])
    expect(drill.rows).toEqual([])
    expect(drill.dropped).toEqual([])
  })

  it('a drill step PUSHES, so Back walks up a level; other keys are untouched', async () => {
    await act(async () => drill.drill([{ dimension: 'status', value: 'failed' }]))
    expect(search().getAll('drill')).toEqual(['suite~payments', 'status~failed'])
    expect(search().getAll('suites')).toEqual(['a'])
    expect(router.state.historyAction).toBe('PUSH')
    await act(async () => router.navigate(-1))
    expect(drill.path).toEqual([{ dimension: 'suite', value: 'payments' }])
  })

  it('a step that breaks a rule stops where the valid path stops', async () => {
    await act(async () => drill.drill([{ dimension: 'suite', value: 'other' }]))
    expect(search().getAll('drill')).toEqual(['suite~payments'])
  })

  it('truncate(0) is the root: no drill key at all, and the rows panel closes', async () => {
    await act(async () => drill.openRows('coverage-map', [{ dimension: 'suite', value: 'payments' }]))
    await act(async () => drill.truncate(0))
    expect(search().has('drill')).toBe(false)
    expect(search().has('rows')).toBe(false)
    expect(router.state.historyAction).toBe('PUSH')
  })

  it('opening the rows panel PUSHES (Back closes it); closing REPLACES', async () => {
    await act(async () =>
      drill.openRows('trends-heatmap', [
        { dimension: 'suite', value: 'payments' },
        { dimension: 'day', value: '2026-09-12' },
      ]),
    )
    expect(search().getAll('rows')).toEqual(['by~trends-heatmap', 'suite~payments', 'day~2026-09-12'])
    expect(router.state.historyAction).toBe('PUSH')
    expect(drill.rows).toHaveLength(2)
    expect(drill.rowsOwner).toBe('trends-heatmap')
    await act(async () => drill.closeRows())
    expect(search().has('rows')).toBe(false)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search().getAll('drill')).toEqual(['suite~payments'])
    expect(drill.rowsOwner).toBeNull()
  })

  it("Back and Forward close and reopen the SAME owner's panel", async () => {
    await act(async () => drill.openRows('scatter-suite', [{ dimension: 'test', value: 'fp-1' }]))
    await act(async () => router.navigate(-1))
    expect(drill.rows).toEqual([])
    expect(drill.rowsOwner).toBeNull()
    await act(async () => router.navigate(1))
    expect(ownedRows(drill, 'scatter-suite')).toEqual([{ dimension: 'test', value: 'fp-1' }])
    expect(ownedRows(drill, 'heatmap-test_run')).toEqual([])
  })

  it('an unrelated key changing hands back the SAME path object', async () => {
    const before = drill.path
    await act(async () => router.navigate('/failures?suites=b&drill=suite~payments'))
    expect(drill.path).toEqual(before)
  })
})

describe('useDrillPath on a bad URL', () => {
  it('opens at the valid prefix and names the drop', () => {
    mount('/failures?drill=suite~payments&drill=nonsense')
    expect(drill.path).toEqual([{ dimension: 'suite', value: 'payments' }])
    expect(drill.dropped).toEqual([DRILL_DROP_WORDS.invalid])
  })
})
