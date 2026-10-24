import { describe, expect, it } from 'vitest'
import {
  defaultSaveName, isSavedSearch, normalizeQuery, readSavedSearch, sameSearch, savedSearchFilters, validSaveName,
} from './savedSearchesModel'

describe('savedSearchesModel (W12 SRCH-03)', () => {
  it('stores exactly kind, q, scope and mode (the server refuses any other key)', () => {
    expect(savedSearchFilters('  checkout  ', 'tests', 'hybrid')).toEqual({ kind: 'search', q: 'checkout', scope: 'tests', mode: 'hybrid' })
  })

  it('prefills the name with the query (80 characters); one character reads "Search: x"', () => {
    expect(defaultSaveName('checkout flow')).toBe('checkout flow')
    expect(defaultSaveName('x')).toBe('Search: x')
    expect(defaultSaveName('a'.repeat(120))).toHaveLength(80)
  })

  it('names are 2-255 characters', () => {
    expect(validSaveName('a')).toBe(false)
    expect(validSaveName(' ab ')).toBe(true)
    expect(validSaveName('a'.repeat(255))).toBe(true)
    expect(validSaveName('a'.repeat(256))).toBe(false)
  })

  it('the same search: the same normalised query, scope and mode', () => {
    expect(normalizeQuery('  Checkout   Flow ')).toBe('checkout flow')
    expect(sameSearch({ q: 'Checkout  Flow', scope: 'all', mode: 'keyword' }, { q: 'checkout flow', scope: 'all', mode: 'keyword' })).toBe(true)
    expect(sameSearch({ q: 'checkout', scope: 'all', mode: 'keyword' }, { q: 'checkout', scope: 'tests', mode: 'keyword' })).toBe(false)
    expect(sameSearch({ q: 'checkout', scope: 'tests', mode: 'keyword' }, { q: 'checkout', scope: 'tests', mode: 'hybrid' })).toBe(false)
  })

  it('reads a saved row back without trusting it', () => {
    expect(isSavedSearch({ filters: { kind: 'search' } })).toBe(true)
    expect(isSavedSearch({ filters: { kind: 'report_view' } })).toBe(false)
    expect(readSavedSearch({ filters: { kind: 'search', q: ' login ', scope: 'tests', mode: 'semantic' } }))
      .toEqual({ q: 'login', scope: 'tests', mode: 'semantic' })
    // An unknown scope / mode reads as All / Keyword; a semantic mode on another scope moves to Tests.
    expect(readSavedSearch({ filters: { kind: 'search', q: 'x', scope: 'galaxy', mode: 'vector' } })).toEqual({ q: 'x', scope: 'all', mode: 'keyword' })
    expect(readSavedSearch({ filters: { kind: 'search', q: 'x', scope: 'runs', mode: 'hybrid' } })).toEqual({ q: 'x', scope: 'tests', mode: 'hybrid' })
    expect(readSavedSearch({ filters: { kind: 'search', q: '', scope: 'all', mode: 'keyword' } })).toBeNull()
    expect(readSavedSearch({ filters: { kind: 'search', q: 'a'.repeat(501), scope: 'all', mode: 'keyword' } })).toBeNull()
    expect(readSavedSearch({ filters: { kind: 'report_view', q: 'x' } })).toBeNull()
  })
})
