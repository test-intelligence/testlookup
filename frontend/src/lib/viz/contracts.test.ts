// @vitest-environment node
/**
 * The frontend half of "one set of fixtures, two validators".
 *
 * Every file under `contracts/viz/fixtures/` (repo root) is run through the
 * guards in `contracts.ts`: `valid/` must be accepted, `invalid/` must be
 * rejected FOR THE REASON THE FIXTURE NAMES — an error that starts with its
 * `violates` rule id. Rejecting a fixture for some other reason would let the
 * named rule rot unnoticed, so "not ok" alone is not enough.
 *
 * Fixtures are loaded with `import.meta.glob(..., '?raw')`, not `node:fs`.
 * This tsconfig carries `types: ["vite/client"]` and no `@types/node`, and
 * `npm run build` is `tsc && vite build` over all of `src/` — a `node:fs`
 * import here passes vitest and then fails the build (TS2307). It is the same
 * reason `routeScope.ratchet.test.ts` and `App.routeTargets.test.ts` read
 * sources through Vite. `?raw` keeps the bytes as written, so the hostile
 * strings are parsed by `JSON.parse` here and by nothing else.
 *
 * FAIL CLOSED. A glob that matches nothing yields `{}` — silently, with the
 * run still green. So the first block asserts the directory was found at all,
 * and that each of the five folders has fixtures on both sides. Nothing skips.
 */
import { describe, expect, it } from 'vitest'
import {
  CONTRACT_KINDS,
  LOCAL_RULE_IDS,
  VIZ_LIMITS,
  validateChartSeries,
  validateContract,
  validateDrillPath,
  validateEnvelopeMeta,
  validateScope,
  validateWidgetConfig,
  type ContractKind,
  type ValidationResult,
} from './contracts'

// frontend/src/lib/viz → four levels up is the repo root.
const FIXTURE_SOURCES = import.meta.glob('../../../../contracts/viz/fixtures/*/*/*.json', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const README = Object.values(
  import.meta.glob('../../../../contracts/viz/README.md', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>,
)[0]

interface Fixture {
  kind: string
  verdict: string
  name: string
  description: string
  violates?: string
  payload: unknown
}

const FIXTURES: Fixture[] = Object.entries(FIXTURE_SOURCES)
  .map(([file, raw]) => {
    const [kind, verdict, name] = file.split('/').slice(-3)
    const parsed = JSON.parse(raw) as Omit<Fixture, 'kind' | 'verdict' | 'name'>
    return { ...parsed, kind, verdict, name }
  })
  .sort((a, b) => `${a.kind}/${a.verdict}/${a.name}`.localeCompare(`${b.kind}/${b.verdict}/${b.name}`))

const of = (verdict: 'valid' | 'invalid') =>
  FIXTURES.filter((f) => f.verdict === verdict).map((f) => [`${f.kind}/${f.name}`, f] as const)

const EXPECTED_KINDS = ['chart_series', 'drill_path', 'envelope', 'report_metrics', 'scope', 'widget_config']

/** The rule id an error string starts with. */
const ruleOf = (error: string) => error.slice(0, error.indexOf(':'))

const errorsOf = (result: { ok: boolean; errors?: string[] }) => result.errors ?? []

describe('contracts/viz fixtures are present (fail closed)', () => {
  it('finds the contracts directory from this file', () => {
    expect(README, 'contracts/viz/README.md was not found four levels up').toBeTypeOf('string')
    expect(README).toContain('Visualization contracts')
    expect(FIXTURES.length, 'the fixture glob matched nothing').toBeGreaterThan(0)
  })

  it('covers exactly the six contracts the guards dispatch on', () => {
    expect([...new Set(FIXTURES.map((f) => f.kind))].sort()).toEqual(EXPECTED_KINDS)
    expect([...CONTRACT_KINDS].sort()).toEqual(EXPECTED_KINDS)
  })

  it.each(EXPECTED_KINDS)('%s has at least one valid and one invalid fixture', (kind) => {
    const mine = FIXTURES.filter((f) => f.kind === kind)
    expect(mine.filter((f) => f.verdict === 'valid').length).toBeGreaterThan(0)
    expect(mine.filter((f) => f.verdict === 'invalid').length).toBeGreaterThan(0)
  })

  it('files every fixture under valid/ or invalid/, with a payload', () => {
    for (const f of FIXTURES) {
      expect(['valid', 'invalid'], `${f.kind}/${f.verdict}/${f.name}`).toContain(f.verdict)
      expect(f, `${f.kind}/${f.name} has no payload`).toHaveProperty('payload')
      expect(f.description, `${f.kind}/${f.name} has no description`).toBeTypeOf('string')
    }
  })

  it('names a README rule on every invalid fixture', () => {
    for (const [label, f] of of('invalid')) {
      expect(f.violates, `${label} has no "violates"`).toBeTypeOf('string')
      expect(README, `${label} violates "${f.violates}", which the README never names`).toContain(
        `\`${f.violates}\``,
      )
    }
  })

  it('has an invalid fixture for every rule id the README tables name', () => {
    // The README's own completeness rule. Rule ids are the back-ticked
    // snake_case tokens in a table's "Rule id(s)" column; field names share
    // that spelling, so the column is located per table rather than guessed.
    //
    // Tables are read wherever they sit — including the change-rule-8 table,
    // which is INDENTED under its list item (hence `trim`) — and in C3, where
    // the column is headed "Rule ids" and holds several ids per row.
    const named = new Set<string>()
    let ruleColumn = -1
    for (const rawLine of README.split('\n')) {
      const line = rawLine.trim()
      if (!line.startsWith('|')) {
        ruleColumn = -1
        continue
      }
      // Split on UNESCAPED pipes: the C3 shapes contain `"time"\|"category"`.
      const cells = line.split(/(?<!\\)\|/).slice(1, -1)
      const header = cells.findIndex((cell) => /^\s*Rule ids?\s*$/.test(cell))
      if (header !== -1) {
        ruleColumn = header
        continue
      }
      if (ruleColumn === -1) continue
      // Parenthesised notes quote field names and values, not rule ids:
      // "`status_vocab` (when `value_type` is `status`, …)".
      const ruleCell = (cells[ruleColumn] ?? '').replace(/\([^)]*\)/g, '')
      for (const m of ruleCell.matchAll(/`([a-z][a-z_]+)`/g)) named.add(m[1])
    }
    // Fail closed: a parser that silently drops a table must not pass.
    expect(named.size, `parsed too few rule ids: ${[...named].join(', ')}`).toBeGreaterThanOrEqual(30)
    const mustName = [
      // change rule 8 (indented table)
      'well_formed_string',
      'nesting_depth',
      'forbidden_key',
      // C1
      'window_order',
      'unique_release',
      // C2, incl. the multi-id totals row
      'utc_instant',
      'integer_count',
      'safe_integer',
      'totals_subset',
      'measured_reason',
      // C3 "Rule ids" column
      'kind_enum',
      'point_cap',
      'cell_cap',
      'node_cap',
      'unique_node_id',
      'tree_acyclic',
      'status_vocab',
      // C4, C5
      'unique_instance_id',
      'depth_cap',
    ]
    expect(mustName.filter((rule) => !named.has(rule))).toEqual([])
    // Rule ids the README names are not "local" ones.
    expect(LOCAL_RULE_IDS.filter((rule) => named.has(rule) && rule !== 'required_field')).toEqual(
      [],
    )

    const covered = new Set(of('invalid').map(([, f]) => f.violates))
    expect([...named].filter((rule) => !covered.has(rule))).toEqual([])
  })
})

describe('valid fixtures are accepted', () => {
  it.each(of('valid'))('%s', (_label, f) => {
    const result = validateContract(f.kind as ContractKind, f.payload)
    expect(errorsOf(result)).toEqual([])
    expect(result.ok).toBe(true)
  })
})

describe('invalid fixtures are rejected for the reason they name', () => {
  it.each(of('invalid'))('%s', (_label, f) => {
    const result = validateContract(f.kind as ContractKind, f.payload)
    expect(result.ok, `${f.kind}/${f.name} was accepted`).toBe(false)
    expect(errorsOf(result).length, `${f.kind}/${f.name} was rejected with no errors`).toBeGreaterThan(0)
    // Change rule 4: each fixture breaks exactly one rule, so EVERY error must
    // carry that rule id — an unrelated rejection is a drift, not a pass.
    for (const error of errorsOf(result)) {
      expect(error.startsWith(`${f.violates}: `), `${f.kind}/${f.name}: "${error}"`).toBe(true)
    }
    const rules = [...new Set(errorsOf(result).map(ruleOf))]
    expect(
      rules,
      `${f.kind}/${f.name} must fail with "${f.violates}" only; got: ${errorsOf(result).join(' | ')}`,
    ).toEqual([f.violates])
  })

  it('starts every error with a rule id', () => {
    const ruleIds = new Set<string>([
      ...LOCAL_RULE_IDS,
      ...of('invalid').map(([, f]) => f.violates ?? ''),
    ])
    for (const [label, f] of of('invalid')) {
      for (const error of errorsOf(validateContract(f.kind as ContractKind, f.payload))) {
        expect(error, label).toMatch(/^[a-z_]+: \S/)
        expect([...ruleIds], `${label}: "${error}"`).toContain(ruleOf(error))
      }
    }
  })
})

describe('untrusted text and unknown keys survive validation', () => {
  const hostile = FIXTURES.filter((f) => f.verdict === 'valid' && f.name.includes('hostile'))

  it('has hostile fixtures to check', () => {
    // One per contract that carries free text: scope, chart_series, drill_path.
    expect(hostile.map((f) => f.kind)).toEqual(
      expect.arrayContaining(['chart_series', 'drill_path', 'scope']),
    )
  })

  it.each(hostile.map((f) => [`${f.kind}/${f.name}`, f] as const))(
    '%s comes back identical',
    (_label, f) => {
      const before = JSON.stringify(f.payload)
      const result = validateContract(f.kind as ContractKind, f.payload)
      expect(result.ok).toBe(true)
      if (!result.ok) return
      expect(JSON.stringify(result.value)).toBe(before)
      // …and the markup is really in there, so "identical" is not vacuous.
      expect(before).toMatch(/<(img|script)\b/)
    },
  )

  it('does not mutate a payload it rejects either', () => {
    for (const [, f] of of('invalid')) {
      const before = JSON.stringify(f.payload)
      validateContract(f.kind as ContractKind, f.payload)
      expect(JSON.stringify(f.payload)).toBe(before)
    }
  })

  it('round-trips a widget config with keys this build does not know', () => {
    const stored = {
      page: 'trends',
      version: 2,
      layoutDensity: 'compact',
      futureBlock: { nested: [1, { deep: '<b>kept</b>' }] },
      instances: [
        {
          instanceId: 'a',
          templateId: 'pass_rate_trend',
          chartType: 'line',
          colorOverride: 'series-3',
          annotations: [{ at: '2026-09-01', text: '<i>release</i>' }],
        },
        { instanceId: 'b', templateId: 'daily_breakdown', pinned: true },
      ],
    }
    const wire = JSON.stringify(stored)
    const result = validateWidgetConfig(JSON.parse(wire))
    expect(result.ok).toBe(true)
    if (!result.ok) return

    expect(JSON.stringify(result.value)).toBe(wire)
    expect(result.value.layoutDensity).toBe('compact')
    expect(result.value.futureBlock).toEqual(stored.futureBlock)
    expect(result.value.instances[0].colorOverride).toBe('series-3')
    expect(result.value.instances[0].annotations).toEqual(stored.instances[0].annotations)
    expect(result.value.instances[1].pinned).toBe(true)
  })

  it('tolerates unknown fields on every contract (additive)', () => {
    for (const [label, f] of of('valid')) {
      const extended = { ...(f.payload as Record<string, unknown>), added_in_v2: { any: ['thing'] } }
      const result = validateContract(f.kind as ContractKind, extended)
      expect(errorsOf(result), label).toEqual([])
    }
  })
})

describe('guards never throw', () => {
  const deep: unknown[] = []
  let cursor: unknown[] = deep
  for (let i = 0; i < 5_000; i++) {
    const next: unknown[] = []
    cursor.push({ nodes: next, series: next, instances: next, path: next, window: next })
    cursor = next
  }
  const cyclic: Record<string, unknown> = { kind: 'tree' }
  cyclic.nodes = [cyclic]
  cyclic.self = cyclic

  const garbage: [string, unknown][] = [
    ['null', null],
    ['undefined', undefined],
    ['a number', 42],
    ['NaN', Number.NaN],
    ['a boolean', true],
    ['a string', 'x'],
    ['an empty array', []],
    ['an empty object', {}],
    ['a function', () => undefined],
    ['a symbol', Symbol('s')],
    ['a bigint', 10n],
    ['a Date', new Date(0)],
    ['a Map', new Map([['kind', 'series']])],
    ['deeply nested junk', deep],
    [
      'deeply nested junk under every list key',
      { kind: 'tree', nodes: deep, series: deep, cells: deep, edges: deep, instances: deep, path: deep },
    ],
    ['a self-referencing object', cyclic],
    ['arrays where objects belong', { window: [], scope: [], totals: [], nodes: {}, path: 'x' }],
    ['objects where arrays belong', { release_ids: {}, suite_names: 7, instances: 'x', path: {} }],
    [
      'every known key set to junk',
      Object.fromEntries(
        [
          'project_id', 'release_ids', 'suite_names', 'window', 'schema_version', 'scope',
          'totals', 'ignored_filters', 'kind', 'series', 'cells', 'nodes', 'edges', 'page',
          'version', 'instances', 'path',
        ].map((key) => [key, [[{ [key]: null }], 1.5, true]]),
      ),
    ],
    [
      'a getter that throws',
      Object.defineProperty({}, 'kind', {
        enumerable: true,
        get() {
          throw new Error('boom')
        },
      }),
    ],
  ]

  const guards = [
    validateScope,
    validateEnvelopeMeta,
    validateChartSeries,
    validateWidgetConfig,
    validateDrillPath,
  ]

  it.each(garbage)('on %s', (_label, input) => {
    for (const guard of guards) {
      const result = guard(input)
      expect(result.ok).toBe(false)
      expect(errorsOf(result).length).toBeGreaterThan(0)
      for (const error of errorsOf(result)) expect(error).toMatch(/^[a-z_]+: /)
    }
    for (const kind of [...CONTRACT_KINDS, 'toString', 'nope'] as ContractKind[]) {
      expect(validateContract(kind, input).ok).toBe(false)
    }
  })

  it('reports a thrown getter as a finding', () => {
    const [, hostile] = garbage[garbage.length - 1]
    expect(errorsOf(validateChartSeries(hostile))).toEqual(['unexpected_error: boom'])
  })

  it('survives a thrown value whose `instanceof` check itself throws', () => {
    // `err instanceof Error` runs the thrown Proxy's getPrototypeOf trap.
    const thrown = new Proxy(
      {},
      {
        getPrototypeOf() {
          throw new Error('trap')
        },
      },
    )
    expect(() => thrown instanceof Error, 'the Proxy is not hostile enough').toThrow('trap')
    const input = Object.defineProperty({}, 'kind', {
      enumerable: true,
      get() {
        throw thrown
      },
    })
    for (const guard of guards) {
      expect(errorsOf(guard(input))).toEqual(['unexpected_error: the input could not be read'])
    }
    for (const kind of CONTRACT_KINDS) {
      expect(errorsOf(validateContract(kind, input))).toEqual([
        'unexpected_error: the input could not be read',
      ])
    }
  })

  it('survives a Proxy payload whose traps throw', () => {
    const input = new Proxy(
      {},
      {
        ownKeys() {
          throw new Error('no keys for you')
        },
        getOwnPropertyDescriptor() {
          throw new Error('no descriptor either')
        },
      },
    )
    for (const guard of guards) {
      const result = guard(input)
      expect(result.ok).toBe(false)
      expect(errorsOf(result)).toHaveLength(1)
      expect(errorsOf(result)[0]).toMatch(/^unexpected_error: /)
    }
  })

  it('rejects a contract name it does not know, including inherited keys', () => {
    expect(errorsOf(validateContract('toString' as ContractKind, {}))[0]).toMatch(/^unknown_contract: /)
  })

  it('bounds its work and its error list on an oversized payload', () => {
    const huge = {
      project_id: '11111111-1111-4111-8111-111111111111',
      release_ids: Array.from({ length: 200_000 }, (_, i) => `not-a-uuid-${i}`),
      suite_names: Array.from({ length: 200_000 }, () => ''),
      window: { days: 30 },
    }
    const result = validateScope(huge)
    const rules = errorsOf(result).map(ruleOf)
    expect(rules).toContain('release_cap')
    expect(rules).toContain('suite_cap')
    expect(errorsOf(result).length).toBeLessThanOrEqual(50)
  })
})

describe('numbers are numbers and counts are integers', () => {
  const scope = (window: unknown) => ({
    project_id: null,
    release_ids: [],
    suite_names: [],
    window,
  })
  const point = (y: unknown, n: unknown) => ({
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [{ key: 'a', label: 'a', points: [{ x: '2026-09-17', y, n }] }],
  })

  it.each([
    ['1.5 days', 1.5],
    ['true days', true],
    ['"30" days', '30'],
    ['0 days', 0],
    ['366 days', 366],
    ['-1 days', -1],
  ])('window rejects %s', (_label, days) => {
    expect(errorsOf(validateScope(scope({ days }))).map(ruleOf)).toEqual(['window_days_range'])
  })

  it('window accepts the edges 1 and 365', () => {
    expect(validateScope(scope({ days: 1 })).ok).toBe(true)
    expect(validateScope(scope({ days: VIZ_LIMITS.windowDaysMax })).ok).toBe(true)
  })

  it('a fractional n is integer_count, a negative one non_negative, a non-number invalid_type', () => {
    const nRules = (n: unknown) => errorsOf(validateChartSeries(point(1, n))).map(ruleOf)
    expect(nRules(1.5)).toEqual(['integer_count'])
    expect(nRules(-0.5)).toEqual(['integer_count'])
    expect(nRules(true)).toEqual(['invalid_type'])
    expect(nRules('3')).toEqual(['invalid_type'])
    expect(nRules(Number.NaN)).toEqual(['invalid_type'])
    expect(nRules(-2)).toEqual(['non_negative'])
  })

  it('a count stops at 2^53 - 1 (safe_integer)', () => {
    const nRules = (n: unknown) => errorsOf(validateChartSeries(point(1, n))).map(ruleOf)
    expect(nRules(Number.MAX_SAFE_INTEGER)).toEqual([])
    expect(nRules(2 ** 53)).toEqual(['safe_integer'])
    expect(nRules(1e300)).toEqual(['safe_integer'])
  })

  it('y is a number or null — never a boolean, a string, NaN or Infinity', () => {
    expect(validateChartSeries(point(96.4, 1)).ok).toBe(true)
    expect(validateChartSeries(point(null, 0)).ok).toBe(true)
    for (const y of [true, '96.4', Number.NaN, Number.POSITIVE_INFINITY, undefined]) {
      expect(validateChartSeries(point(y, 1)).ok, String(y)).toBe(false)
    }
  })
})

describe('window forms', () => {
  const scope = (window: unknown) => ({
    project_id: null,
    release_ids: [],
    suite_names: [],
    window,
  })
  const rules = (window: unknown) => errorsOf(validateScope(scope(window))).map(ruleOf)

  it('needs exactly one form', () => {
    expect(rules({})).toEqual(['window_one_form'])
    expect(rules({ from: '2026-09-01' })).toEqual(['window_one_form'])
    expect(rules({ to: '2026-09-01' })).toEqual(['window_one_form'])
    expect(rules({ days: 30, to: '2026-09-01' })).toEqual(['window_one_form'])
    expect(rules('30d')).toEqual(['invalid_type'])
  })

  it('treats an explicit null as "not supplied"', () => {
    expect(rules({ days: 30, from: null, to: null })).toEqual([])
    expect(rules({ days: null, from: '2026-08-01', to: '2026-08-31' })).toEqual([])
  })

  it('accepts a same-day range and a span of exactly 366 days', () => {
    expect(rules({ from: '2026-09-01', to: '2026-09-01' })).toEqual([])
    expect(rules({ from: '2024-01-01', to: '2025-01-01' })).toEqual([]) // leap year: 366
  })

  it('rejects a span over 366 days', () => {
    expect(rules({ from: '2024-01-01', to: '2025-01-02' })).toEqual(['window_order'])
  })

  it('takes years 0001–0099 literally (Date.UTC would read them as 19xx)', () => {
    expect(rules({ from: '0001-01-01', to: '0001-01-01' })).toEqual([])
    expect(rules({ from: '0099-12-31', to: '0099-12-31' })).toEqual([])
    expect(rules({ from: '0004-02-29', to: '0004-03-01' })).toEqual([]) // year 4 is a leap year
    expect(rules({ from: '0005-02-29', to: '0005-03-01' })).toEqual(['window_order'])
    // Across the 0099 → 0100 boundary: 1 day. Read as 1999 → 0100 it is ~694 000.
    expect(rules({ from: '0099-12-31', to: '0100-01-01' })).toEqual([])
    expect(rules({ from: '0000-01-01', to: '0000-01-02' })).toEqual(['window_order'])
    expect(rules({ from: '9999-12-31', to: '9999-12-31' })).toEqual([])
  })

  it.each([
    ['not a date', 'yesterday'],
    ['a timestamp', '2026-09-01T00:00:00Z'],
    ['an impossible day', '2026-02-30'],
    ['a short year', '26-09-01'],
    ['a number', 20260901],
  ])('rejects %s as a bound', (_label, bad) => {
    expect(rules({ from: bad, to: '2026-09-10' })).toEqual(['window_order'])
    expect(rules({ from: '2026-01-01', to: bad })).toEqual(['window_order'])
  })
})

describe('scope identifiers', () => {
  const scope = (over: Record<string, unknown>) => ({
    project_id: '11111111-1111-4111-8111-111111111111',
    release_ids: [],
    suite_names: [],
    window: { days: 30 },
    ...over,
  })
  const rules = (over: Record<string, unknown>) => errorsOf(validateScope(scope(over))).map(ruleOf)

  it('accepts only a UUID or the sentinel as a release id', () => {
    expect(rules({ release_ids: ['unattributed'] })).toEqual([])
    expect(rules({ release_ids: ['UNATTRIBUTED'] })).toEqual(['release_id_format'])
    expect(rules({ release_ids: [''] })).toEqual(['release_id_format'])
    expect(rules({ release_ids: [7] })).toEqual(['release_id_format'])
  })

  it('does not accept the release sentinel as a project id', () => {
    expect(rules({ project_id: 'unattributed' })).toEqual(['project_id_format'])
    expect(rules({ project_id: 7 })).toEqual(['project_id_format'])
  })

  it('treats two spellings of one UUID as the same release', () => {
    const id = 'abcdefab-1111-4111-8111-abcdefabcdef'
    expect(rules({ release_ids: [id, id.toUpperCase()] })).toEqual(['unique_release'])
  })

  it('compares suite names exactly — no trimming, no case folding', () => {
    expect(rules({ suite_names: ['Payments', 'payments', ' payments'] })).toEqual([])
  })

  it('counts characters, not UTF-16 units, against the suite-name cap', () => {
    expect(rules({ suite_names: ['😀'.repeat(VIZ_LIMITS.suiteNameLength)] })).toEqual([])
    expect(rules({ suite_names: ['😀'.repeat(VIZ_LIMITS.suiteNameLength + 1)] })).toEqual([
      'suite_name_length',
    ])
  })

  it('reports every missing field', () => {
    expect(errorsOf(validateScope({})).map(ruleOf)).toEqual([
      'required_field',
      'required_field',
      'required_field',
      'required_field',
    ])
  })
})

describe('caps sit exactly where the README puts them', () => {
  const uuid = (i: number) => `22222222-2222-4222-8222-${String(i).padStart(12, '0')}`
  const many = <T>(n: number, make: (i: number) => T) => Array.from({ length: n }, (_, i) => make(i))

  const CAPS: [string, number, (n: number) => ValidationResult<unknown>][] = [
    [
      'release_cap',
      VIZ_LIMITS.releases,
      (n: number) =>
        validateScope({
          project_id: uuid(0),
          release_ids: many(n, uuid),
          suite_names: [],
          window: { days: 30 },
        }),
    ],
    [
      'suite_cap',
      VIZ_LIMITS.suites,
      (n: number) =>
        validateScope({
          project_id: null,
          release_ids: [],
          suite_names: many(n, (i) => `suite-${i}`),
          window: { days: 30 },
        }),
    ],
    [
      'series_cap',
      VIZ_LIMITS.series,
      (n: number) =>
        validateChartSeries({
          kind: 'series',
          dimensions: [],
          x_type: 'category',
          series: many(n, (i) => ({ key: `k${i}`, label: `k${i}`, points: [] })),
        }),
    ],
    [
      'point_cap',
      VIZ_LIMITS.pointsPerSeries,
      (n: number) =>
        validateChartSeries({
          kind: 'series',
          dimensions: [],
          x_type: 'time',
          series: [{ key: 'a', label: 'a', points: many(n, (i) => ({ x: `d${i}`, y: i, n: 1 })) }],
        }),
    ],
    [
      'cell_cap',
      VIZ_LIMITS.matrixCells,
      (n: number) =>
        validateChartSeries({
          kind: 'matrix',
          value_type: 'count',
          x_labels: ['x'],
          y_labels: ['y'],
          cells: many(n, () => ({ x: 0, y: 0, value: 1, n: 1 })),
        }),
    ],
    [
      'node_cap',
      VIZ_LIMITS.treeNodes,
      (n: number) =>
        validateChartSeries({
          kind: 'tree',
          nodes: many(n, (i) => ({
            id: `n${i}`,
            parent_id: i === 0 ? null : `n${i - 1}`,
            label: 'l',
            value: 1,
            measure: null,
          })),
        }),
    ],
    [
      'node_cap',
      VIZ_LIMITS.graphNodes,
      (n: number) =>
        validateChartSeries({
          kind: 'graph',
          nodes: many(n, (i) => ({ id: `g${i}`, label: 'l', size: 1 })),
          edges: [],
        }),
    ],
    [
      'instance_cap',
      VIZ_LIMITS.widgetInstances,
      (n: number) =>
        validateWidgetConfig({
          page: 'trends',
          version: 2,
          instances: many(n, (i) => ({ instanceId: `i${i}`, templateId: 't' })),
        }),
    ],
    [
      'depth_cap',
      VIZ_LIMITS.drillDepth,
      (n: number) =>
        validateDrillPath({
          path: many(n, (i) => ({
            dimension: ['project', 'release', 'suite', 'branch', 'test'][i],
            value: 'v',
          })),
        }),
    ],
  ]

  it.each(CAPS)('%s: %i is accepted, one more is not', (rule, cap, build) => {
    expect(errorsOf(build(cap))).toEqual([])
    expect(errorsOf(build(cap + 1)).map(ruleOf)).toEqual([rule])
  })

  it('caps a widget title at 120 characters and a drill value at 2 000', () => {
    const config = (title: string) => ({
      page: 'p',
      version: 2,
      instances: [{ instanceId: 'a', templateId: 't', title }],
    })
    expect(validateWidgetConfig(config('x'.repeat(120))).ok).toBe(true)
    expect(errorsOf(validateWidgetConfig(config('x'.repeat(121)))).map(ruleOf)).toEqual([
      'title_length',
    ])
    const drill = (value: string) => ({ path: [{ dimension: 'test', value }] })
    expect(validateDrillPath(drill('x'.repeat(2000))).ok).toBe(true)
    expect(errorsOf(validateDrillPath(drill('x'.repeat(2001)))).map(ruleOf)).toEqual(['value_length'])
  })
})

describe('chart series structure', () => {
  const rules = (payload: unknown) => errorsOf(validateChartSeries(payload)).map(ruleOf)
  const node = (id: string, parent_id: string | null) => ({
    id,
    parent_id,
    label: id,
    value: 1,
    measure: null,
  })

  it('finds a cycle that hangs off a valid root', () => {
    // A root exists, so only the walk can see this one.
    expect(
      rules({ kind: 'tree', nodes: [node('root', null), node('a', 'b'), node('b', 'c'), node('c', 'a')] }),
    ).toEqual(['tree_acyclic'])
  })

  it('finds a node that is its own parent', () => {
    expect(rules({ kind: 'tree', nodes: [node('root', null), node('a', 'a')] })).toEqual([
      'tree_acyclic',
    ])
  })

  it('survives a 500-deep chain, in either order', () => {
    const chain = Array.from({ length: 500 }, (_, i) => node(`n${i}`, i === 0 ? null : `n${i - 1}`))
    expect(rules({ kind: 'tree', nodes: chain })).toEqual([])
    expect(rules({ kind: 'tree', nodes: chain.slice().reverse() })).toEqual([])
  })

  it('accepts an empty tree as "no data" but wants a root once there is a node', () => {
    // The fixture pins the first half; this pins the boundary right beside it.
    expect(rules({ kind: 'tree', nodes: [] })).toEqual([])
    // Two findings (no root, and the cycle itself), one rule.
    const rootless = rules({ kind: 'tree', nodes: [node('a', 'b'), node('b', 'a')] })
    expect(rootless).toHaveLength(2)
    expect([...new Set(rootless)]).toEqual(['tree_acyclic'])
  })

  it('rejects duplicate node ids in trees and graphs', () => {
    expect(rules({ kind: 'tree', nodes: [node('a', null), node('a', null)] })).toEqual([
      'unique_node_id',
    ])
    expect(
      rules({
        kind: 'graph',
        nodes: [
          { id: 'g', label: 'x', size: 1 },
          { id: 'g', label: 'y', size: 1 },
        ],
        edges: [],
      }),
    ).toEqual(['unique_node_id'])
  })

  it('checks both ends of an edge and both ends of the weight range', () => {
    const graph = (edge: unknown) => ({
      kind: 'graph',
      nodes: [
        { id: 'a', label: 'a', size: 0 },
        { id: 'b', label: 'b', size: 2.5 },
      ],
      edges: [edge],
    })
    expect(rules(graph({ source: 'a', target: 'b', weight: 0 }))).toEqual([])
    expect(rules(graph({ source: 'a', target: 'b', weight: 1 }))).toEqual([])
    expect(rules(graph({ source: 'a', target: 'b', weight: -0.01 }))).toEqual(['weight_range'])
    expect(rules(graph({ source: 'zz', target: 'b', weight: 0.5 }))).toEqual(['edge_endpoints'])
    expect(rules(graph({ source: 'a', target: 7, weight: 0.5 }))).toEqual(['edge_endpoints'])
    expect(rules(graph({ source: 'a', target: 'b', weight: true }))).toEqual(['invalid_type'])
  })

  it('checks matrix indexes on both axes and at both ends', () => {
    const matrix = (cell: unknown, value_type = 'rate') => ({
      kind: 'matrix',
      value_type,
      x_labels: ['d1', 'd2'],
      y_labels: ['s1'],
      cells: [cell],
    })
    expect(rules(matrix({ x: 1, y: 0, value: 50, n: 2 }))).toEqual([])
    expect(rules(matrix({ x: 2, y: 0, value: 50, n: 2 }))).toEqual(['cell_index_range'])
    expect(rules(matrix({ x: 0, y: 1, value: 50, n: 2 }))).toEqual(['cell_index_range'])
    // x / y are counts (change rule 3): a malformed index is reported as that,
    // and only a well-formed one is checked against the labels.
    expect(rules(matrix({ x: -1, y: 0, value: 50, n: 2 }))).toEqual(['non_negative'])
    expect(rules(matrix({ x: 0.5, y: 0, value: 50, n: 2 }))).toEqual(['integer_count'])
    expect(rules(matrix({ x: 2 ** 53, y: 0, value: 50, n: 2 }))).toEqual(['safe_integer'])
    expect(rules(matrix({ x: '0', y: 0, value: 50, n: 2 }))).toEqual(['invalid_type'])
    // A status matrix takes the vocabulary or null — not a number.
    expect(rules(matrix({ x: 0, y: 0, value: null, n: 0 }, 'status'))).toEqual([])
    expect(rules(matrix({ x: 0, y: 0, value: 1, n: 1 }, 'status'))).toEqual(['status_vocab'])
    // A count matrix holds counts.
    expect(rules(matrix({ x: 0, y: 0, value: 1.5, n: 1 }, 'count'))).toEqual(['integer_count'])
    expect(rules(matrix({ x: 0, y: 0, value: -1, n: 1 }, 'count'))).toEqual(['non_negative'])
    expect(rules(matrix({ x: 0, y: 0, value: 2 ** 53, n: 1 }, 'count'))).toEqual(['safe_integer'])
    expect(rules(matrix({ x: 0, y: 0, value: true, n: 1 }, 'count'))).toEqual(['invalid_type'])
    expect(rules(matrix({ x: 0, y: 0, value: null, n: 0 }, 'count'))).toEqual([])
    // A rate is any finite number; only a count must be whole.
    expect(rules(matrix({ x: 0, y: 0, value: 1.5, n: 1 }, 'rate'))).toEqual([])
    expect(rules(matrix({ x: 0, y: 0, value: 'passed', n: 1 }, 'rate'))).toEqual(['invalid_type'])
  })

  it('requires the closed-set fields and rejects null for them', () => {
    expect(rules({ kind: 'series', dimensions: [], x_type: null, series: [] })).toEqual([
      'invalid_value',
    ])
    expect(rules({ kind: 'series', dimensions: [], series: [] })).toEqual(['required_field'])
    expect(rules({ kind: 'matrix', value_type: 'heat', x_labels: [], y_labels: [], cells: [] })).toEqual([
      'invalid_value',
    ])
    expect(rules({ kind: null })).toEqual(['kind_enum'])
    expect(rules({})).toEqual(['kind_enum'])
  })

  it('rejects negative tree values and graph sizes', () => {
    expect(rules({ kind: 'tree', nodes: [{ ...node('a', null), value: -1 }] })).toEqual([
      'non_negative',
    ])
    expect(
      rules({ kind: 'graph', nodes: [{ id: 'a', label: 'a', size: -1 }], edges: [] }),
    ).toEqual(['non_negative'])
  })
})

describe('envelope cross-field rules', () => {
  const base = FIXTURES.find((f) => f.kind === 'envelope' && f.name === 'filtered.json')
  const meta = (over: Record<string, unknown>) => ({
    ...(base?.payload as Record<string, unknown>),
    ...over,
  })
  const rules = (over: Record<string, unknown>) =>
    errorsOf(validateEnvelopeMeta(meta(over))).map(ruleOf)

  it('starts from a fixture that exists', () => {
    expect(base, 'envelope/valid/filtered.json is gone').toBeDefined()
    expect(rules({})).toEqual([])
  })

  it('checks both subset pairs, and allows matched === total', () => {
    const totals = { matched_runs: 1, total_runs: 1, matched_executions: 5, total_executions: 5 }
    expect(rules({ totals })).toEqual([])
    expect(rules({ totals: { ...totals, matched_executions: 6 } })).toEqual(['totals_subset'])
    expect(rules({ totals: { ...totals, matched_runs: 1.5 } })).toEqual(['integer_count'])
    expect(rules({ totals: { ...totals, total_runs: true } })).toEqual(['invalid_type'])
    expect(rules({ totals: { ...totals, total_runs: Number.MAX_SAFE_INTEGER } })).toEqual([])
    expect(rules({ totals: { ...totals, total_runs: 2 ** 53 } })).toEqual(['safe_integer'])
  })

  it('wants a reason only when measured is false, and a total only when truncated', () => {
    expect(rules({ measured: false, reason: '' })).toEqual(['measured_reason'])
    expect(rules({ measured: false, reason: 'no runs' })).toEqual([])
    expect(rules({ measured: true, reason: null })).toEqual([])
    expect(rules({ truncated: true, truncated_total: 0 })).toEqual([])
    expect(rules({ truncated: true, truncated_total: -1 })).toEqual(['non_negative'])
    expect(rules({ truncated: false, truncated_total: null })).toEqual([])
  })

  it('every top-level field is required, though a nullable one may be null', () => {
    for (const key of Object.keys(base?.payload as Record<string, unknown>)) {
      expect(rules({ [key]: undefined }), key).toContain('required_field')
    }
  })

  it('pins the closed sets and formats that carry no README rule id', () => {
    expect(rules({ pass_rate_basis: 'runs' })).toEqual(['invalid_value'])
    expect(rules({ pass_rate_basis: 'unique_tests' })).toEqual([])
    expect(rules({ partial_day: '19 Sep' })).toEqual(['invalid_value'])
    expect(rules({ partial_day: '0001-01-01' })).toEqual([])
    expect(rules({ partial_day: '0000-12-31' })).toEqual(['invalid_value'])
    expect(rules({ schema_version: 0 })).toEqual(['invalid_value'])
    expect(rules({ schema_version: 1.5 })).toEqual(['integer_count'])
  })

  it.each([
    ['a space separator', '2026-09-19 10:42:07Z'],
    ['no seconds', '2026-09-19T10:42Z'],
    ['+0000', '2026-09-19T10:42:07+0000'],
    ['-00:00', '2026-09-19T10:42:07-00:00'],
    ['a non-UTC offset', '2026-09-19T10:42:07+02:00'],
    ['a lower-case z', '2026-09-19T10:42:07z'],
    ['no zone', '2026-09-19T10:42:07'],
    ['7 fraction digits', '2026-09-19T10:42:07.1234567Z'],
    ['an empty fraction', '2026-09-19T10:42:07.Z'],
    ['basic format', '20260919T104207Z'],
    ['hour 24', '2026-09-19T24:00:00Z'],
    ['minute 60', '2026-09-19T10:60:00Z'],
    ['second 60 (no leap seconds)', '2026-09-19T10:42:60Z'],
    ['February 30', '2026-02-30T10:42:07Z'],
    ['September 31', '2026-09-31T10:42:07Z'],
    ['February 29 of a common year', '2025-02-29T00:00:00Z'],
    ['month 13', '2026-13-01T00:00:00Z'],
    ['year 0000', '0000-01-01T00:00:00Z'],
    ['a number', 1_758_278_527],
    ['null', null],
  ])('utc_instant rejects %s', (_label, bad) => {
    expect(rules({ generated_at: bad })).toEqual(['utc_instant'])
    expect(rules({ as_of: bad })).toEqual(['utc_instant'])
  })

  it.each([
    ['Z', '2026-09-19T10:42:07Z'],
    ['+00:00', '2026-09-19T10:42:07+00:00'],
    ['1 fraction digit', '2026-09-19T10:42:07.1Z'],
    ['6 fraction digits', '2026-09-19T10:42:07.123456+00:00'],
    ['the last instant of a day', '2026-09-19T23:59:59.999999Z'],
    ['February 29 of a leap year', '2024-02-29T00:00:00Z'],
    ['an ancient year', '0050-01-01T00:00:00Z'],
  ])('utc_instant accepts %s', (_label, good) => {
    expect(rules({ generated_at: good, as_of: good })).toEqual([])
  })

  it('reads a reason as blank only when every character is in the change-rule-7 set', () => {
    const chars = (...codes: number[]) => String.fromCharCode(...codes)
    const notMeasured = (reason: string) => rules({ measured: false, reason })
    // Every member of the set, alone and all together, is blank.
    const members = [
      0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x20, 0xa0, 0x1680, 0x2000, 0x2005, 0x200a, 0x2028, 0x2029,
      0x202f, 0x205f, 0x3000, 0xfeff,
    ]
    for (const code of members) {
      expect(notMeasured(chars(code)), code.toString(16)).toEqual(['measured_reason'])
    }
    expect(notMeasured(chars(...members))).toEqual(['measured_reason'])
    // Neighbours of the set, and look-alikes outside it, are not whitespace.
    for (const code of [0x08, 0x0e, 0x1c, 0x1f, 0x85, 0x180e, 0x200b, 0x2060, 0x1fff, 0x200c]) {
      expect(notMeasured(chars(code)), code.toString(16)).toEqual([])
    }
  })

  it('does not borrow its whitespace set from String.prototype.trim', () => {
    // No code point tells the pinned set from `trim()` in today's engines, so
    // simulate one that differs: before Unicode 6.3, engines trimmed U+180E
    // (MONGOLIAN VOWEL SEPARATOR), which the pinned set does not contain.
    const mvs = String.fromCharCode(0x180e)
    const realTrim = String.prototype.trim
    const trimAlsoMvs = function (this: string) {
      return realTrim.call(this).split(mvs).join('')
    }
    String.prototype.trim = trimAlsoMvs
    try {
      expect(mvs.trim()).toBe('') // the simulation is in effect
      expect(rules({ measured: false, reason: mvs })).toEqual([])
    } finally {
      String.prototype.trim = realTrim
    }
  })
})

describe('widget config and drill path details', () => {
  const config = (instance: Record<string, unknown>) => ({
    page: 'trends',
    version: 2,
    instances: [{ instanceId: 'a', templateId: 't', ...instance }],
  })
  const rules = (instance: Record<string, unknown>) =>
    errorsOf(validateWidgetConfig(config(instance))).map(ruleOf)

  it('lets an optional key be absent, but never null (change rule 6)', () => {
    expect(rules({})).toEqual([])
    expect(rules({ title: undefined })).toEqual([])
    expect(rules({ title: null })).toEqual(['invalid_type'])
    expect(rules({ metricVariant: null })).toEqual(['invalid_type'])
    expect(rules({ filters: null })).toEqual(['invalid_type'])
    expect(rules({ groupBy: null })).toEqual(['invalid_type'])
    // A closed set does not contain null either.
    expect(rules({ chartType: null })).toEqual(['chart_type_enum'])
    expect(rules({ topN: null })).toEqual(['top_n_enum'])
    expect(rules({ scale: null })).toEqual(['invalid_value'])
  })

  it('takes groupBy from the dimension enum', () => {
    expect(rules({ groupBy: [] })).toEqual([])
    expect(rules({ groupBy: ['day', 'colour'] })).toEqual(['dimension_enum'])
  })

  it('pins topN to the four values — 10 is fine, "10" and 7 are not', () => {
    expect(rules({ topN: 10 })).toEqual([])
    expect(rules({ topN: '10' })).toEqual(['top_n_enum'])
    expect(rules({ topN: 7 })).toEqual(['top_n_enum'])
  })

  it('pins scale, stack and bucket', () => {
    expect(rules({ scale: 'log', stack: 'percent', bucket: 'day' })).toEqual([])
    expect(rules({ scale: 'sqrt' })).toEqual(['invalid_value'])
    expect(rules({ stack: 'stream' })).toEqual(['invalid_value'])
    expect(rules({ bucket: 'month' })).toEqual(['invalid_value'])
  })

  it('needs non-empty ids, and a whole-number version', () => {
    expect(rules({ instanceId: '' })).toEqual(['required_field'])
    expect(rules({ templateId: 7 })).toEqual(['required_field'])
    expect(errorsOf(validateWidgetConfig({ ...config({}), version: 1.5 })).map(ruleOf)).toEqual([
      'integer_count',
    ])
    expect(errorsOf(validateWidgetConfig({ ...config({}), version: '2' })).map(ruleOf)).toEqual([
      'invalid_type',
    ])
    expect(errorsOf(validateWidgetConfig({ ...config({}), version: 0 })).map(ruleOf)).toEqual([
      'invalid_value',
    ])
  })

  it('applies the status vocabulary only to a status level', () => {
    const drill = (dimension: string, value: unknown) =>
      errorsOf(validateDrillPath({ path: [{ dimension, value }] })).map(ruleOf)
    expect(drill('status', 'broken')).toEqual([])
    expect(drill('status', 'Passed')).toEqual(['status_vocab'])
    expect(drill('suite', 'flaky')).toEqual([])
    expect(drill('suite', 7)).toEqual(['invalid_type'])
  })
})

describe('payload-wide rules (change rule 8) apply to every contract, unknown keys included', () => {
  const validOf = (kind: string) => {
    const fixture = FIXTURES.find((f) => f.kind === kind && f.verdict === 'valid')
    if (!fixture) throw new Error(`no valid ${kind} fixture`)
    return fixture.payload as Record<string, unknown>
  }
  type Guard = (input: unknown) => ValidationResult<unknown>
  const guardsByKind: [ContractKind, Guard][] = [
    ['scope', validateScope],
    ['envelope', validateEnvelopeMeta],
    ['chart_series', validateChartSeries],
    ['widget_config', validateWidgetConfig],
    ['drill_path', validateDrillPath],
  ]
  /** The rule ids from both entry points — the kind's own guard and validateContract. */
  const rulesWith = (kind: ContractKind, guard: Guard, extra: unknown) => {
    const payload = { ...validOf(kind), added_in_v2: extra }
    const direct = errorsOf(guard(payload)).map(ruleOf)
    expect(errorsOf(validateContract(kind, payload)).map(ruleOf), kind).toEqual(direct)
    return direct
  }

  /** `k` nested containers, alternating object and array. */
  const nest = (k: number): unknown => {
    let value: unknown = {}
    for (let i = 1; i < k; i++) value = i % 2 === 0 ? { n: value } : [value]
    return value
  }
  const depthOf = (v: unknown): number =>
    typeof v === 'object' && v !== null
      ? 1 + Math.max(0, ...Object.values(v as object).map(depthOf))
      : 0

  const HIGH = String.fromCharCode(0xd83d)
  const LOW = String.fromCharCode(0xde00)
  const EMOJI = HIGH + LOW

  it('the helpers build what they claim', () => {
    expect(depthOf(nest(31))).toBe(31)
    expect(depthOf({ added_in_v2: nest(31) })).toBe(32)
    expect(EMOJI).toBe('😀')
    expect(EMOJI).toHaveLength(2)
  })

  it.each(guardsByKind)('%s: nesting_depth — 32 containers pass, 33 do not', (kind, guard) => {
    // The payload is container 1; `added_in_v2` holds the rest.
    expect(rulesWith(kind, guard, nest(31))).toEqual([])
    expect(rulesWith(kind, guard, nest(32))).toEqual(['nesting_depth'])
  })

  it('nesting_depth stops a 100 000-deep payload without recursing', () => {
    let deep: unknown = 'leaf'
    for (let i = 0; i < 100_000; i++) deep = [deep]
    for (const [, guard] of guardsByKind) {
      expect(errorsOf(guard(deep)).map(ruleOf)).toEqual(['nesting_depth'])
    }
  })

  it.each(guardsByKind)('%s: forbidden_key — own __proto__, constructor, prototype', (kind, guard) => {
    // JSON.parse makes `__proto__` an OWN data property; a literal would set the prototype.
    const proto: unknown = JSON.parse('{"deeper": [{"__proto__": {"isAdmin": true}}]}')
    expect(Object.keys((proto as { deeper: object[] }).deeper[0])).toEqual(['__proto__'])
    expect(rulesWith(kind, guard, proto)).toEqual(['forbidden_key'])
    expect(rulesWith(kind, guard, [{ constructor: 'x' }])).toEqual(['forbidden_key'])
    expect(rulesWith(kind, guard, { a: { prototype: null } })).toEqual(['forbidden_key'])
    // Inherited ones are not keys of the payload: every plain object inherits `constructor`.
    expect(rulesWith(kind, guard, { a: {}, b: Object.create({ prototype: 1 }) })).toEqual([])
    // A key that merely contains the word is fine.
    expect(rulesWith(kind, guard, { my_constructor: 1, __proto: 2 })).toEqual([])
  })

  it('forbidden_key is checked at the top level and inside widget filters too', () => {
    const top: unknown = JSON.parse('{"__proto__": 1, "page": "p", "version": 2, "instances": []}')
    expect(errorsOf(validateWidgetConfig(top)).map(ruleOf)).toEqual(['forbidden_key'])
    const inFilters = {
      page: 'p',
      version: 2,
      instances: [{ instanceId: 'a', templateId: 't', filters: { prototype: {} } }],
    }
    expect(errorsOf(validateWidgetConfig(inFilters)).map(ruleOf)).toEqual(['forbidden_key'])
    const hidden = Object.defineProperty({ page: 'p', version: 2, instances: [] }, 'constructor', {
      value: 1,
      enumerable: false,
    })
    expect(errorsOf(validateWidgetConfig(hidden)).map(ruleOf)).toEqual(['forbidden_key'])
  })

  it.each(guardsByKind)('%s: well_formed_string — values and keys', (kind, guard) => {
    expect(rulesWith(kind, guard, { text: `half ${HIGH}` })).toEqual(['well_formed_string'])
    expect(rulesWith(kind, guard, [`${LOW} half`])).toEqual(['well_formed_string'])
    expect(rulesWith(kind, guard, [LOW + HIGH])).toEqual(['well_formed_string']) // pair, wrong order
    expect(rulesWith(kind, guard, [HIGH + HIGH + LOW])).toEqual(['well_formed_string'])
    expect(rulesWith(kind, guard, { [`key ${HIGH}`]: 1 })).toEqual(['well_formed_string'])
    expect(rulesWith(kind, guard, { [LOW]: 1 })).toEqual(['well_formed_string'])
    // A surrogate PAIR is one well-formed character, as a value or a key.
    expect(rulesWith(kind, guard, { [EMOJI]: [`${EMOJI}${EMOJI} ok`] })).toEqual([])
  })

  it('well_formed_string covers the known string fields as well', () => {
    const scope = { project_id: null, release_ids: [], suite_names: [`a${HIGH}`], window: { days: 30 } }
    expect(errorsOf(validateScope(scope)).map(ruleOf)).toEqual(['well_formed_string'])
    const drill = { path: [{ dimension: 'test', value: LOW }] }
    expect(errorsOf(validateDrillPath(drill)).map(ruleOf)).toEqual(['well_formed_string'])
  })

  it('bounds the work on a payload of shared sub-objects', () => {
    // 4^20 paths through 20 distinct arrays — only an in-memory graph can do this.
    let shared: unknown = 'leaf'
    for (let i = 0; i < 20; i++) shared = [shared, shared, shared, shared]
    const started = Date.now()
    const result = validateWidgetConfig({ page: 'p', version: 2, instances: [], x: shared })
    expect(errorsOf(result).map(ruleOf)).toEqual(['payload_size'])
    expect(Date.now() - started).toBeLessThan(5_000)
  })

  it('payload_size: 1 000 000 values and keys pass, one more does not', () => {
    // A README rule stated in prose (no table row, no fixture): pin that it is named.
    expect(README).toContain('`payload_size`')
    expect(LOCAL_RULE_IDS as readonly string[]).not.toContain('payload_size')
    // payload (1) + 4 keys and their 4 values (8) + the items of `x`.
    const config = (items: number) => ({
      page: 'p',
      version: 2,
      instances: [],
      x: new Array<number>(items).fill(0),
    })
    expect(errorsOf(validateWidgetConfig(config(1_000_000 - 9)))).toEqual([])
    expect(errorsOf(validateWidgetConfig(config(1_000_000 - 8))).map(ruleOf)).toEqual([
      'payload_size',
    ])
  })

  it('reports only the payload-wide violation and reads no further', () => {
    const broken = { kind: 'nope', extra: { constructor: 1 } }
    expect(errorsOf(validateChartSeries(broken))).toHaveLength(1)
    expect(errorsOf(validateChartSeries(broken)).map(ruleOf)).toEqual(['forbidden_key'])
  })
})
