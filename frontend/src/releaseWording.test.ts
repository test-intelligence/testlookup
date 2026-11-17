// @vitest-environment node
/**
 * The product says "release", not "ship" (owner review, 2026-11-16).
 *
 * TestLookup's own words are release words: the release gate, the release
 * decision, GO / NO-GO. Yet Home's banner read "No-Go · ship blocked by
 * unresolved failures", the Releases page counted "Ready to ship", and the
 * in-app guide said a flag "ships off". The public website moved to
 * "Know when your release is ready." first and quoted the app's old label
 * until the app changed.
 *
 * This keeps the word out of what people read in the app:
 *  - every string literal, template text and JSX text in a non-test source
 *    file under src/ (comments and identifiers are not shown, so they are not
 *    read), apart from `shipped` as a status VALUE a release can arrive with
 *    from an API or a Jira sync;
 *  - every page of the in-app guide (src/content/guide).
 * It also states the replacement labels, so a revert fails here and not only
 * in the component tests.
 */
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import { releaseHeadline, releaseHealthFacts } from '@/components/releases/health'
import type { DerivedRelease } from '@/components/releases/types'

const SHIP = /\bship(?:s|ped|ping|pable)?\b/i

/** Status values a release can carry in from outside: data, never copy. */
const DATA_VALUES = new Set(['shipped'])

const SOURCES = import.meta.glob(
  ['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.ts', '!/src/**/*.test.tsx', '!/src/**/*.d.ts', '!/src/**/__fixtures__/**'],
  { query: '?raw', import: 'default', eager: true },
) as Record<string, string>

const GUIDE = import.meta.glob('/src/content/guide/**/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** The text a file can put on screen: its string literals, template text and JSX text. */
function shownText(path: string, source: string): string[] {
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, kind)
  const out: string[] = []
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      out.push(node.text)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return out
}

function shipWords(path: string, source: string): string[] {
  return shownText(path, source).filter((text) => SHIP.test(text) && !DATA_VALUES.has(text.trim()))
}

describe('the app says "release", not "ship"', () => {
  it('reads strings, template text and JSX text, and skips comments and data values', () => {
    // Proves the scan itself: each planted copy is caught, each non-copy is not.
    const planted = [
      "const label = 'Ready to ship'",
      'const h = `${name} is ready to ship`',
      'const b = `${a} · ship blocked ${c}`',
      'export const X = () => <p>The CLI ships in the repo</p>',
    ].join('\n')
    expect(shipWords('/src/Planted.tsx', planted)).toEqual([
      'Ready to ship',
      ' is ready to ship',
      ' · ship blocked ',
      'The CLI ships in the repo',
    ])
    const notCopy = [
      '// the listener ships in the SDK',
      '/** what ships next */',
      "const s = new Set(['released', 'shipped'])",
      'function ShippingThisWeek() { return null }',
    ].join('\n')
    expect(shipWords('/src/Planted.tsx', notCopy)).toEqual([])
  })

  it('no source file under src/ puts "ship" on screen', () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(500)
    const found = Object.entries(SOURCES).flatMap(([path, source]) =>
      shipWords(path, source).map((text) => `${path}: ${JSON.stringify(text)}`),
    )
    expect(found).toEqual([])
  })

  it('no page of the in-app guide says "ship"', () => {
    expect(Object.keys(GUIDE).length).toBeGreaterThan(10)
    const found = Object.entries(GUIDE).flatMap(([path, text]) =>
      text.split('\n').flatMap((line, i) => (SHIP.test(line) ? [`${path}:${i + 1}: ${line.trim().slice(0, 120)}`] : [])),
    )
    expect(found).toEqual([])
  })

  it('the Releases page counts "Ready to release", and a GO release "is ready to release"', () => {
    const go = {
      id: 'r-1',
      name: 'Checkout 2.4',
      stage: 'in_progress',
      blockerCount: 0,
      blockers: [],
      dueAt: null,
      gate: { decision: 'go', measured: false, passRate: null },
      source: {},
    } as unknown as DerivedRelease
    const labels = releaseHealthFacts(null, [go], [go]).map((fact) => fact.label)
    expect(labels).toContain('Ready to release')
    expect(labels).not.toContain('Ready to ship')
    expect(releaseHeadline(go)).toBe('Checkout 2.4 is ready to release')
  })
})
