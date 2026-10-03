import { describe, expect, it, vi } from 'vitest'
import {
  availableIntents,
  DEFAULT_MARK_INTENTS,
  intentLabel,
  keyboardIntent,
  markSelectors,
  MARK_INTENTS,
  pointerIntent,
  type ChartMark,
  type MarkIntent,
} from './marks'

const payments: ChartMark = { dimension: 'suite', value: 'suite-key-1', label: 'payments', y: 42, n: 50 }
const hostile: ChartMark = { dimension: 'suite', value: '__proto__', label: '<img src=x onerror=alert(1)>', y: null, n: null }

describe('availableIntents', () => {
  it('offers nothing without a handler, whatever markIntents says: absent props are today’s chart', () => {
    expect(availableIntents(payments, {})).toEqual([])
    expect(availableIntents(payments, { markIntents: () => ['rows', 'filter'] })).toEqual([])
  })

  it('defaults to a drill when the host passes a handler and no list', () => {
    expect(availableIntents(payments, { onMarkActivate: vi.fn() })).toEqual(['drill'])
    expect(DEFAULT_MARK_INTENTS).toEqual(['drill'])
  })

  it('keeps the host’s order, drops duplicates and unknown intents', () => {
    const markIntents = () => ['rows', 'drill', 'rows', 'explode' as MarkIntent, 'filter'] as const
    expect(availableIntents(payments, { onMarkActivate: vi.fn(), markIntents })).toEqual(['rows', 'drill', 'filter'])
  })

  it('asks the host per mark', () => {
    const markIntents = vi.fn((mark: ChartMark) => (mark.dimension === 'test' ? (['rows'] as const) : (['drill', 'rows'] as const)))
    expect(availableIntents({ ...payments, dimension: 'test' }, { onMarkActivate: vi.fn(), markIntents })).toEqual(['rows'])
    expect(availableIntents(payments, { onMarkActivate: vi.fn(), markIntents })).toEqual(['drill', 'rows'])
    expect(markIntents).toHaveBeenCalledTimes(2)
  })

  it('lists the three intents in the host-facing order', () => {
    expect(MARK_INTENTS).toEqual(['drill', 'rows', 'filter'])
  })
})

describe('pointerIntent (OD-2)', () => {
  const all = ['drill', 'rows', 'filter'] as const

  it('a plain click is the first intent on offer', () => {
    expect(pointerIntent(all)).toBe('drill')
    expect(pointerIntent(['rows', 'filter'])).toBe('rows')
  })

  it('Shift, Ctrl or Cmd asks for filter when it is offered', () => {
    expect(pointerIntent(all, { shiftKey: true })).toBe('filter')
    expect(pointerIntent(all, { ctrlKey: true })).toBe('filter')
    expect(pointerIntent(all, { metaKey: true })).toBe('filter')
  })

  it('a modified click where filter is not offered is a plain click, not nothing', () => {
    expect(pointerIntent(['drill', 'rows'], { shiftKey: true })).toBe('drill')
  })

  it('a touch tap only selects: the readout buttons offer the intents', () => {
    expect(pointerIntent(all, { pointerType: 'touch' })).toBeNull()
    expect(pointerIntent(all, { pointerType: 'mouse' })).toBe('drill')
    expect(pointerIntent(all, { pointerType: 'pen' })).toBe('drill')
  })

  it('nothing on offer is nothing to do', () => {
    expect(pointerIntent([])).toBeNull()
    expect(pointerIntent([], { shiftKey: true })).toBeNull()
  })
})

describe('keyboardIntent', () => {
  it('Enter is the first on offer; Shift+Enter filters when it can', () => {
    expect(keyboardIntent(['drill', 'rows', 'filter'])).toBe('drill')
    expect(keyboardIntent(['drill', 'rows', 'filter'], { shiftKey: true })).toBe('filter')
    expect(keyboardIntent(['rows'], { shiftKey: true })).toBe('rows')
    expect(keyboardIntent([])).toBeNull()
  })
})

describe('intentLabel', () => {
  it('names the mark in the drill button, as text', () => {
    expect(intentLabel('drill', payments)).toBe('Drill into payments')
    expect(intentLabel('rows', payments)).toBe('View rows')
    expect(intentLabel('filter', payments)).toBe('Filter page by this')
  })

  it('carries a hostile label through verbatim (React renders it as text)', () => {
    expect(intentLabel('drill', hostile)).toBe('Drill into <img src=x onerror=alert(1)>')
  })
})

describe('markSelectors', () => {
  it('is the mark’s own level, then its context, by KEY not label', () => {
    const segment: ChartMark = { ...payments, context: [{ dimension: 'status', value: 'failed' }] }
    expect(markSelectors(segment)).toEqual([
      { dimension: 'suite', value: 'suite-key-1' },
      { dimension: 'status', value: 'failed' },
    ])
    expect(markSelectors(hostile)).toEqual([{ dimension: 'suite', value: '__proto__' }])
  })

  it('copies the context: a caller pushing onto the result cannot change the mark', () => {
    const context = [{ dimension: 'status' as const, value: 'failed' }]
    const levels = markSelectors({ ...payments, context })
    levels[1].value = 'passed'
    expect(context[0].value).toBe('failed')
  })
})
