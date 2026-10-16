import { describe, expect, it } from 'vitest'
import { noteInputsFor, noteShowsForSuite, parseTimelineNotes, type TimelineNote } from './timelineNotes'

function note(over: Partial<TimelineNote> = {}): TimelineNote {
  return {
    id: 'n1',
    project_id: 'p1',
    starts_on: '2026-09-10',
    ends_on: '2026-09-12',
    text: 'release freeze',
    suite_name: null,
    created_by_name: 'Ada',
    can_edit: true,
    created_at: '2026-09-10T08:00:00Z',
    updated_at: null,
    ...over,
  }
}

const list = (items: unknown[], extra: Record<string, unknown> = {}) => ({
  items,
  total: items.length,
  truncated: false,
  start: '2026-09-05',
  end: '2026-09-18',
  ...extra,
})

describe('parseTimelineNotes (TRND-04, the §0 defensive guard)', () => {
  it.each<[string, unknown]>([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['items that are not a list', { items: 'n1' }],
    ['a list body', [note()]],
  ])('%s → null (no notes drawn, no crash)', (_name, raw) => {
    expect(parseTimelineNotes(raw)).toBeNull()
  })

  it('a valid answer is returned', () => {
    expect(parseTimelineNotes(list([note()]))).toEqual(list([note()]))
  })

  it('drops a malformed item and keeps the rest', () => {
    const parsed = parseTimelineNotes(
      list([
        note(),
        { ...note({ id: 'n2' }), starts_on: 'yesterday' },
        { ...note({ id: 'n3' }), text: 42 },
        null,
        'n4',
        note({ id: 'n5', suite_name: 'checkout' }),
      ], { total: 6 }),
    )
    expect(parsed?.items.map((n) => n.id)).toEqual(['n1', 'n5'])
    // `total` is the server's COUNT, not the kept items.
    expect(parsed?.total).toBe(6)
  })

  it('a reversed range reads as its first day; a wrong-typed total falls back to the items', () => {
    const parsed = parseTimelineNotes({ items: [note({ starts_on: '2026-09-12', ends_on: '2026-09-10' })], total: 'x' })
    expect(parsed?.items[0]).toMatchObject({ starts_on: '2026-09-12', ends_on: '2026-09-12' })
    expect(parsed?.total).toBe(1)
    expect(parsed?.truncated).toBe(false)
  })

  it('hostile text stays a string (it is rendered as text, never parsed)', () => {
    const parsed = parseTimelineNotes(list([note({ text: '<img src=x onerror=alert(1)>' }), note({ id: '__proto__', text: '__proto__' })]))
    expect(parsed?.items.map((n) => n.text)).toEqual(['<img src=x onerror=alert(1)>', '__proto__'])
  })
})

describe('the suite rule: a suite note shows only under that suite', () => {
  it('a project-wide note always shows', () => {
    expect(noteShowsForSuite(note(), null)).toBe(true)
    expect(noteShowsForSuite(note(), 'Checkout')).toBe(true)
  })

  it('a suite note shows only while the page is filtered to that suite, ignoring case and space', () => {
    const suiteNote = note({ suite_name: 'Checkout' })
    expect(noteShowsForSuite(suiteNote, null)).toBe(false)
    expect(noteShowsForSuite(suiteNote, ' checkout ')).toBe(true)
    expect(noteShowsForSuite(suiteNote, 'payments')).toBe(false)
  })

  it('noteInputsFor maps the shown notes to chart inputs and guards a non-list', () => {
    const notes = [note(), note({ id: 'n2', suite_name: 'payments', text: 'pay' })]
    expect(noteInputsFor(notes, null)).toEqual([
      { id: 'n1', from: '2026-09-10', to: '2026-09-12', label: 'release freeze', author: 'Ada' },
    ])
    expect(noteInputsFor(notes, 'Payments').map((n) => n.id)).toEqual(['n1', 'n2'])
    expect(noteInputsFor(undefined, null)).toEqual([])
    expect(noteInputsFor({} as unknown as TimelineNote[], null)).toEqual([])
  })
})
