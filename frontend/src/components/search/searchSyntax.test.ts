import { describe, expect, it } from 'vitest'
import { readSourceFile } from '@/test/readSourceFile'
import { SEARCH_SYNTAX, SYNTAX_ENTITY_LABELS, appliesToLabel } from './searchSyntax'

// W12 SRCH-04: the Syntax help's table. The server's FILTER_KEYS order and
// FILTER_SUPPORT sets are pinned against this file by
// backend/tests/test_search_syntax_frontend_parity.py; this pins the shape the
// page renders.
describe('searchSyntax', () => {
  it('lists the eight keys in the server\'s order, each with an example that uses it', () => {
    expect(SEARCH_SYNTAX.map((row) => row.key)).toEqual(['status', 'suite', 'branch', 'env', 'type', 'since', 'release', 'owner'])
    for (const row of SEARCH_SYNTAX) expect(row.example.startsWith(`${row.key}:`), row.key).toBe(true)
  })

  it('names only the six entity types', () => {
    const known = new Set(Object.keys(SYNTAX_ENTITY_LABELS))
    for (const row of SEARCH_SYNTAX) for (const entity of row.appliesTo) expect(known.has(entity), `${row.key}:${entity}`).toBe(true)
  })

  it('says which kinds apply a key', () => {
    const by = Object.fromEntries(SEARCH_SYNTAX.map((row) => [row.key, appliesToLabel(row)]))
    expect(by.status).toBe('Tests, Runs')
    expect(by.owner).toBe('Tests, Defects')
    expect(by.since).toBe('Every kind')
    expect(by.release).toBe('Tests, Runs, Suites, Flaky, Releases')
  })

  it('keeps one entry per line, the shape the parity test reads', async () => {
    const source = await readSourceFile('src/components/search/searchSyntax.ts')
    const lines = source.split('\n').filter((line) => /^\s*\{ key: '/.test(line))
    expect(lines).toHaveLength(8)
    for (const line of lines) expect(line).toMatch(/^\s*\{ key: '[a-z]+', example: '[^']+', appliesTo: \[('[a-z_]+'(, )?)+\] \},$/)
  })
})
