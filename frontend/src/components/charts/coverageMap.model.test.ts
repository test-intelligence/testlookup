import { describe, expect, it } from 'vitest'
import type { TreeChart, TreeNode, TreeNodeStats } from '@/lib/viz/contracts'
import { tooltipText } from './tooltip'
import {
  answersLevel,
  binOf,
  childIntents,
  childKey,
  childKind,
  childMark,
  CLASS_KEY_SEPARATOR,
  COLOR_BINS,
  COLOR_BY_OPTIONS,
  colorByLabel,
  coverageDescription,
  COVERAGE_DROP_WORDS,
  COVERAGE_MAP_CAPTION,
  coverageEmptyText,
  crumbLabel,
  drillSearch,
  drillTarget,
  GAP_WORDS,
  isColorBy,
  lastRunText,
  learnLabels,
  levelFromPath,
  levelPath,
  levelRequest,
  levelRootId,
  levelView,
  measureOf,
  nodeDisplayName,
  NODE_LABEL_MAX,
  nodeTooltip,
  OTHER_NOTE,
  OTHER_RATE,
  otherNote,
  TOP_LEVEL,
  type CoverageChild,
  type CoverageLevel,
} from './coverageMap.model'

const SEP = CLASS_KEY_SEPARATOR
const HOSTILE = '<img src=x onerror="window.__xss=1">'

const seen = (over: Partial<TreeNodeStats> = {}): TreeNodeStats => ({
  test_count: 10,
  executions: 40,
  pass_rate: 92.5,
  flaky_count: 1,
  flaky_share: 0.1,
  last_executed_at: '2026-09-30T08:00:00Z',
  staleness_days: 2,
  recency: 'seen',
  ...over,
})

const idle = (recency: 'seen' | 'unknown' | 'never'): TreeNodeStats =>
  recency === 'seen'
    ? seen({ executions: 0, pass_rate: null, flaky_count: 0, flaky_share: 0, staleness_days: 45, last_executed_at: '2026-08-18T00:00:00Z' })
    : seen({ executions: 0, pass_rate: null, flaky_count: 0, flaky_share: 0, staleness_days: null, last_executed_at: null, recency })

const node = (id: string, parent: string | null, label: string, stats?: TreeNodeStats): TreeNode => ({
  id,
  parent_id: parent,
  label,
  value: stats?.test_count ?? 1,
  measure: stats?.pass_rate ?? null,
  ...(stats ? { stats } : {}),
})

const LEVEL2: CoverageLevel = { depth: 2, suite: 'pay~ments', classKey: null }
const LEVEL3: CoverageLevel = { depth: 3, suite: 'pay~ments', classKey: `a${SEP}b` }

describe('levels from the drill path', () => {
  it('reads suite then class, and stops at the first level the map does not own, saying so (R1B-6)', () => {
    expect(levelFromPath([])).toEqual({ level: TOP_LEVEL, used: 0, dropped: [] })
    expect(levelFromPath([{ dimension: 'suite', value: 'payments' }])).toEqual({
      level: { depth: 2, suite: 'payments', classKey: null },
      used: 1,
      dropped: [],
    })
    expect(
      levelFromPath([
        { dimension: 'suite', value: 'payments' },
        { dimension: 'class', value: 'tests/api/test_pay.py' },
      ]),
    ).toEqual({ level: { depth: 3, suite: 'payments', classKey: 'tests/api/test_pay.py' }, used: 2, dropped: [] })
    // A third level: the map has none (a test opens its rows), so it is cut, and said.
    expect(
      levelFromPath([
        { dimension: 'suite', value: 'payments' },
        { dimension: 'class', value: 'tests/api/test_pay.py' },
        { dimension: 'test', value: 'fp' },
      ]),
    ).toEqual({
      level: { depth: 3, suite: 'payments', classKey: 'tests/api/test_pay.py' },
      used: 2,
      dropped: [COVERAGE_DROP_WORDS.unsupported],
    })
    // Another host's dimension, a class with no suite above it, a status after a suite: cut there, and said.
    expect(levelFromPath([{ dimension: 'status', value: 'failed' }])).toEqual({
      level: TOP_LEVEL,
      used: 0,
      dropped: [COVERAGE_DROP_WORDS.unsupported],
    })
    expect(levelFromPath([{ dimension: 'class', value: 'X' }])).toEqual({
      level: TOP_LEVEL,
      used: 0,
      dropped: [COVERAGE_DROP_WORDS.unsupported],
    })
    expect(levelFromPath([{ dimension: 'suite', value: 's' }, { dimension: 'status', value: 'failed' }])).toEqual({
      level: { depth: 2, suite: 's', classKey: null },
      used: 1,
      dropped: [COVERAGE_DROP_WORDS.unsupported],
    })
  })

  it("cuts a suite the page's suite filter does not allow, as the Failures ladder does (R1B-6)", () => {
    const path = [
      { dimension: 'suite' as const, value: 'payments' },
      { dimension: 'class' as const, value: 'k' },
    ]
    // The filter holds suite NAMES; the map's key is the name trimmed and lower-cased.
    expect(levelFromPath(path, ' Payments ').used).toBe(2)
    expect(levelFromPath(path, ['Auth', 'PAYMENTS']).used).toBe(2)
    expect(levelFromPath(path, 'Auth')).toEqual({ level: TOP_LEVEL, used: 0, dropped: [COVERAGE_DROP_WORDS.outOfScope] })
    expect(levelFromPath(path, ['Auth', 'Search'])).toEqual({
      level: TOP_LEVEL,
      used: 0,
      dropped: [COVERAGE_DROP_WORDS.outOfScope],
    })
    // No filter (null, '', []): every suite is in scope.
    for (const none of [null, '', []]) expect(levelFromPath(path, none).used).toBe(2)
  })

  it('round-trips a level through its path, and asks the endpoint for exactly that level', () => {
    for (const level of [TOP_LEVEL, LEVEL2, LEVEL3]) expect(levelFromPath(levelPath(level)).level).toEqual(level)
    expect(levelRequest(TOP_LEVEL)).toEqual({ depth: 1 })
    expect(levelRequest(LEVEL2)).toEqual({ depth: 2, suite: 'pay~ments' })
    expect(levelRequest(LEVEL3)).toEqual({ depth: 3, suite: 'pay~ments', class_key: `a${SEP}b` })
  })
})

describe('an empty level (F-14)', () => {
  it('says what this level has none of, never that filters matched nothing', () => {
    expect(coverageEmptyText(TOP_LEVEL)).toBe('No suites to map in this window.')
    expect(coverageEmptyText(LEVEL2)).toBe('No classes or files to map in this suite in this window.')
    expect(coverageEmptyText(LEVEL3)).toBe('No tests to map in this class or file in this window.')
    for (const level of [TOP_LEVEL, LEVEL2, LEVEL3]) expect(coverageEmptyText(level)).not.toMatch(/filter/i)
  })
})

describe('which level a response answers', () => {
  it('names each level by the root id built from its own keys', () => {
    expect(levelRootId(TOP_LEVEL)).toBe('all')
    expect(levelRootId(LEVEL2)).toBe('s:pay~ments')
    expect(levelRootId(LEVEL3)).toBe(`c:pay~ments${SEP}a${SEP}b`)
  })

  it('tells this level\'s response from the previous level\'s still on screen', () => {
    const top: TreeChart = { kind: 'tree', nodes: [node('all', null, 'All suites'), node('s:pay~ments', 'all', 'Payments')] }
    expect(answersLevel(top, TOP_LEVEL)).toBe(true)
    expect(answersLevel(top, LEVEL2)).toBe(false)
    const suite: TreeChart = { kind: 'tree', nodes: [node('s:pay~ments', null, 'Payments')] }
    expect(answersLevel(suite, LEVEL2)).toBe(true)
    expect(answersLevel(suite, { ...LEVEL2, suite: 'other' })).toBe(false)
    expect(answersLevel(suite, TOP_LEVEL)).toBe(false)
    // An empty level has no root to tell by.
    expect(answersLevel({ kind: 'tree', nodes: [] }, LEVEL3)).toBe(true)
  })
})

describe('keys are the id with the KNOWN prefix stripped (BE2 R7), never split', () => {
  it('suite level: s:<key>', () => {
    expect(childKey(TOP_LEVEL, 's:payments')).toBe('payments')
    expect(childKey(TOP_LEVEL, `s:a${SEP}b:c`)).toBe(`a${SEP}b:c`)
    expect(childKey(TOP_LEVEL, 'c:x')).toBeNull()
    expect(childKey(TOP_LEVEL, 's:')).toBeNull()
  })

  it('class level: c:<the suite asked for>␟<key>, even when the class key holds the separator', () => {
    expect(childKey(LEVEL2, `c:pay~ments${SEP}tests/a.py`)).toBe('tests/a.py')
    // A class key that itself contains the separator: stripping keeps it whole; splitting would not.
    expect(childKey(LEVEL2, `c:pay~ments${SEP}x${SEP}y`)).toBe(`x${SEP}y`)
    // Another suite's class: not this level's child.
    expect(childKey(LEVEL2, `c:other${SEP}tests/a.py`)).toBeNull()
    expect(childKey(LEVEL2, 'c:pay~ments')).toBeNull()
  })

  it('test level: t:<fingerprint>; the Other node has no key', () => {
    expect(childKey(LEVEL3, 't:fp-1')).toBe('fp-1')
    expect(childKey(LEVEL3, 'other:c:x')).toBeNull()
    expect(childKind(LEVEL3, 'other:c:x')).toBe('other')
    expect(childKind(LEVEL3, 't:fp-1')).toBe('test')
    expect(childKind(LEVEL2, 'c:x')).toBe('class')
    expect(childKind(TOP_LEVEL, 's:x')).toBe('suite')
  })
})

describe('levelView', () => {
  const tree: TreeChart = {
    kind: 'tree',
    nodes: [
      node('all', null, 'All suites', seen({ test_count: 13 })),
      node('s:payments', 'all', 'Payments', seen()),
      node('s:auth', 'all', HOSTILE, seen({ test_count: 2 })),
      node('other:all', 'all', 'Other (3)', seen({ test_count: 1, pass_rate: null })),
    ],
  }

  it('is the root and its children in the server order, each with its kind and key', () => {
    const view = levelView(tree, TOP_LEVEL)
    expect(view.root?.id).toBe('all')
    expect(view.children.map((c) => [c.kind, c.key])).toEqual([
      ['suite', 'payments'],
      ['suite', 'auth'],
      ['other', null],
    ])
  })

  it('is empty without a tree, and without a root', () => {
    expect(levelView(null, TOP_LEVEL)).toEqual({ root: null, children: [] })
    expect(levelView({ kind: 'tree', nodes: [] }, TOP_LEVEL)).toEqual({ root: null, children: [] })
  })

  it('opens a suite and a class, shows a test its rows, and does nothing with Other', () => {
    const [payments, , other] = levelView(tree, TOP_LEVEL).children
    expect(drillTarget(payments)).toEqual({ dimension: 'suite', value: 'payments' })
    expect(childIntents(payments)).toEqual(['drill'])
    expect(drillTarget(other)).toBeNull()
    expect(childIntents(other)).toEqual([])
    const klass: CoverageChild = { node: node(`c:pay~ments${SEP}k`, 'x', 'k'), kind: 'class', key: 'k' }
    expect(drillTarget(klass)).toEqual({ dimension: 'class', value: 'k' })
    const test: CoverageChild = { node: node('t:fp', 'x', 'test_pay'), kind: 'test', key: 'fp' }
    expect(drillTarget(test)).toBeNull()
    expect(childIntents(test)).toEqual(['rows'])
    expect(childIntents({ ...test, key: null })).toEqual([])
  })

  it('makes a mark of the KEY, the drawn label, the pass rate and the executions, with the levels above as context', () => {
    const test: CoverageChild = { node: node('t:fp', 'x', HOSTILE, seen()), kind: 'test', key: 'fp' }
    expect(childMark(test, LEVEL3)).toEqual({
      dimension: 'test',
      value: 'fp',
      label: HOSTILE,
      y: 92.5,
      n: 40,
      context: [
        { dimension: 'suite', value: 'pay~ments' },
        { dimension: 'class', value: `a${SEP}b` },
      ],
    })
    const suite = levelView(tree, TOP_LEVEL).children[0]
    expect(childMark(suite, TOP_LEVEL)).toEqual({ dimension: 'suite', value: 'payments', label: 'Payments', y: 92.5, n: 40 })
    // A never-run node's rate stays null, never 0.
    const never: CoverageChild = { node: node('s:n', 'all', 'n', idle('never')), kind: 'suite', key: 'n' }
    expect(childMark(never, TOP_LEVEL).y).toBeNull()
    const bare: CoverageChild = { node: node('s:b', 'all', 'b'), kind: 'suite', key: 'b' }
    expect(childMark(bare, TOP_LEVEL)).toMatchObject({ y: null, n: null })
    const other: CoverageChild = { node: node('other:all', 'all', 'Other (3)'), kind: 'other', key: null }
    expect(childMark(other, TOP_LEVEL)).toMatchObject({ dimension: 'suite', value: 'other:all' })
  })
})

describe('nodeDisplayName', () => {
  it('draws the last segment of a file path or a qualified class name', () => {
    expect(nodeDisplayName('tests/api/payments/test_pay.py', 'class')).toBe('test_pay.py')
    expect(nodeDisplayName('C:\\src\\Tests\\LoginTests.cs', 'class')).toBe('LoginTests.cs')
    expect(nodeDisplayName('com.acme.pay.PaymentTest', 'class')).toBe('PaymentTest')
    expect(nodeDisplayName('Payments.Api.Tests.LoginTests', 'class')).toBe('LoginTests')
    // A file name is kept whole; so is a suite or a test name that looks like a path.
    expect(nodeDisplayName('test_pay.py', 'class')).toBe('test_pay.py')
    expect(nodeDisplayName('a.b.lowercase', 'class')).toBe('a.b.lowercase')
    expect(nodeDisplayName('web/checkout', 'suite')).toBe('web/checkout')
  })

  it('cuts a long name in the MIDDLE to the cap, keeping its end, and leaves hostile text as text', () => {
    const long = `${'x'.repeat(80)}_end`
    const shown = nodeDisplayName(long, 'test')
    expect(shown.length).toBeLessThanOrEqual(NODE_LABEL_MAX)
    expect(shown.endsWith('_end')).toBe(true)
    expect(nodeDisplayName(HOSTILE, 'test')).toBe(HOSTILE)
    expect(nodeDisplayName('constructor', 'suite')).toBe('constructor')
  })
})

describe('colour: a fixed scale, gaps by name', () => {
  it('measures a seen node and names each gap distinctly (unknown is never "never run")', () => {
    expect(measureOf(seen(), 'pass_rate')).toEqual({ value: 92.5, gap: null })
    expect(measureOf(seen(), 'staleness')).toEqual({ value: 2, gap: null })
    expect(measureOf(seen(), 'flaky_share')).toEqual({ value: 0.1, gap: null })
    expect(measureOf(idle('seen'), 'pass_rate')).toEqual({ value: null, gap: 'not_run' })
    expect(measureOf(idle('unknown'), 'pass_rate')).toEqual({ value: null, gap: 'unknown' })
    expect(measureOf(idle('never'), 'pass_rate')).toEqual({ value: null, gap: 'never' })
    // An idle test seen weeks ago HAS a staleness: it is coloured, not hatched.
    expect(measureOf(idle('seen'), 'staleness')).toEqual({ value: 45, gap: null })
    expect(measureOf(idle('unknown'), 'staleness')).toEqual({ value: null, gap: 'unknown' })
    expect(measureOf(idle('never'), 'staleness')).toEqual({ value: null, gap: 'never' })
    expect(measureOf(undefined, 'staleness')).toEqual({ value: null, gap: 'unknown' })
  })

  it('does not call "nothing ran" a 0% flaky share', () => {
    expect(measureOf(idle('seen'), 'flaky_share')).toEqual({ value: null, gap: 'not_run' })
    expect(measureOf(idle('never'), 'flaky_share')).toEqual({ value: null, gap: 'never' })
    expect(measureOf(seen({ flaky_share: null }), 'flaky_share')).toEqual({ value: null, gap: 'not_run' })
    expect(measureOf(seen({ flaky_share: 0, flaky_count: 0 }), 'flaky_share')).toEqual({ value: 0, gap: null })
  })

  it('bins pass rate from the top (low is salient) on fixed edges', () => {
    expect([100, 90, 89.9, 75, 74.9, 50, 49.9, 25, 24.9, 0].map((v) => binOf(v, 'pass_rate'))).toEqual([
      0, 0, 1, 1, 2, 2, 3, 3, 4, 4,
    ])
    expect(binOf(-1, 'pass_rate')).toBe(4)
  })

  it('bins days since the last run and flaky share from the bottom (high is salient)', () => {
    expect([0, 1, 2, 7, 8, 30, 31, 90, 91, 400].map((v) => binOf(v, 'staleness'))).toEqual([0, 0, 1, 1, 2, 2, 3, 3, 4, 4])
    expect([0, 0.01, 0.05, 0.06, 0.15, 0.2, 0.3, 0.31, 1].map((v) => binOf(v, 'flaky_share'))).toEqual([
      0, 1, 1, 2, 2, 3, 3, 4, 4,
    ])
  })

  it('has five labelled bins per measure, and a label for each option', () => {
    for (const option of COLOR_BY_OPTIONS) {
      expect(COLOR_BINS[option.value]).toHaveLength(5)
      expect(colorByLabel(option.value)).toBe(option.label)
      expect(isColorBy(option.value)).toBe(true)
    }
    expect(isColorBy('constructor')).toBe(false)
    expect(colorByLabel('nope' as never)).toBe('nope')
  })
})

describe('tooltip and words', () => {
  const child = (stats: TreeNodeStats | undefined, kind: CoverageChild['kind'] = 'suite'): CoverageChild => ({
    node: node('s:x', 'all', HOSTILE, stats),
    kind,
    key: 'x',
  })

  it('titles the tooltip with the full label and states every figure', () => {
    const content = nodeTooltip(child(seen()))
    expect(content.title).toBe(HOSTILE)
    const text = tooltipText(content)
    expect(text).toContain('Level: Suite')
    expect(text).toContain('Tests: 10')
    expect(text).toContain('Pass rate: 92.5%')
    expect(text).toContain('Executions: 40')
    expect(text).toContain('Flaky tests: 1 (10.0%)')
    expect(text).toContain('Last run: 2026-09-30')
    expect(text).toContain('Days since last run: 2')
  })

  it('says why a node has no pass rate, and never "never run" for a lost record', () => {
    expect(tooltipText(nodeTooltip(child(idle('seen'))))).toContain(`Pass rate: ${GAP_WORDS.not_run}`)
    const unknown = tooltipText(nodeTooltip(child(idle('unknown'))))
    expect(unknown).toContain(`Pass rate: ${GAP_WORDS.unknown}`)
    expect(unknown).toContain(`Last run: ${GAP_WORDS.unknown}`)
    expect(unknown).not.toContain('Never run')
    expect(unknown).not.toContain('Days since last run')
    const never = tooltipText(nodeTooltip(child(idle('never'))))
    expect(never).toContain(`Last run: ${GAP_WORDS.never}`)
    // No executions: the flaky share is not stated as a percentage of nothing.
    expect(never).toContain('Flaky tests: 0')
    expect(never).not.toContain('Flaky tests: 0 (')
  })

  it('explains the Other node, and copes with a node without stats', () => {
    const other = tooltipText(nodeTooltip(child(seen({ pass_rate: null }), 'other')))
    expect(other).toContain(`Pass rate: ${OTHER_RATE}`)
    expect(other).toContain(OTHER_NOTE)
    expect(tooltipText(nodeTooltip(child(undefined)))).toBe(`${HOSTILE}. Level: Suite. Tests: 1`)
  })

  it('states the last run as the table does', () => {
    expect(lastRunText(seen())).toBe('2026-09-30')
    expect(lastRunText(idle('never'))).toBe('Never run')
    expect(lastRunText(idle('unknown'))).toBe('Last run unknown')
    expect(lastRunText(seen({ last_executed_at: null }))).toBe('Last run unknown')
  })

  it('describes the level in one sentence', () => {
    const view = { root: node('all', null, 'All suites'), children: [child(seen())] }
    expect(coverageDescription(view, TOP_LEVEL, 'pass_rate')).toBe(
      'Test coverage map: 1 suite in All suites, sized by test count, coloured by pass rate.',
    )
    expect(coverageDescription({ root: null, children: [child(seen()), child(seen())] }, LEVEL2, 'staleness')).toBe(
      'Test coverage map: 2 classes or files in All suites, sized by test count, coloured by days since last run.',
    )
  })

  it('says how much the Other node folds, when there is one', () => {
    const kept = [child(seen()), child(seen())]
    const folded = { node: node('other:all', 'all', 'Other (812)', seen()), kind: 'other' as const, key: null }
    expect(otherNote({ root: null, children: kept }, null, 1)).toBe('')
    expect(otherNote({ root: null, children: [...kept, folded] }, 814, 2)).toBe(
      'Showing the 2 largest of 814 classes or files; the rest are combined in Other (812).',
    )
    expect(otherNote({ root: null, children: [...kept, folded] }, null, 3)).toBe(
      'Showing the 2 largest tests; the rest are combined in Other (812).',
    )
  })

  it('words the caption exactly as the EPIC does', () => {
    expect(COVERAGE_MAP_CAPTION).toBe('Test execution coverage — not code coverage')
  })
})

describe('breadcrumb', () => {
  it('learns the labels of the children and of the level it is on', () => {
    const view = levelView(
      {
        kind: 'tree',
        nodes: [
          node(`c:pay~ments${SEP}k`, null, 'tests/k.py'),
          node('t:fp', `c:pay~ments${SEP}k`, 'test_one'),
        ],
      },
      { depth: 3, suite: 'pay~ments', classKey: 'k' },
    )
    expect(learnLabels(view, { depth: 3, suite: 'pay~ments', classKey: 'k' })).toEqual({ 'class~k': 'tests/k.py', 'test~fp': 'test_one' })
    const top = levelView(
      { kind: 'tree', nodes: [node('all', null, 'All suites'), node('s:payments', 'all', 'Payments')] },
      TOP_LEVEL,
    )
    expect(learnLabels(top, TOP_LEVEL)).toEqual({ 'suite~payments': 'Payments' })
  })

  it('names a level by what the map called it, else by its key; a hostile key is plain text', () => {
    const labels = { 'suite~payments': 'Payments' }
    expect(crumbLabel(labels, { dimension: 'suite', value: 'payments' })).toBe('Payments')
    expect(crumbLabel(labels, { dimension: 'suite', value: HOSTILE })).toBe(HOSTILE)
    expect(crumbLabel(labels, { dimension: 'class', value: '__none__' })).toBe('(ungrouped)')
    // A key named like an Object.prototype member is not found on the prototype.
    expect(crumbLabel({}, { dimension: 'suite', value: 'constructor' })).toBe('constructor')
    expect(crumbLabel({}, { dimension: '__proto__' as never, value: 'x' })).toBe('x')
  })

  it('writes the drill levels in the address, keeps every other key, and closes the rows panel', () => {
    const search = '?days=30&drill=suite~old&rows=test~fp&suites=a&suites=b'
    const out = new URLSearchParams(drillSearch(search, [{ dimension: 'suite', value: 'a~b&c' }]))
    expect(out.getAll('drill')).toEqual(['suite~a~b&c'])
    expect(out.getAll('rows')).toEqual([])
    expect(out.get('days')).toBe('30')
    expect(out.getAll('suites')).toEqual(['a', 'b'])
    expect(drillSearch('?drill=suite~x', [])).toBe('?')
    expect(drillSearch('', [{ dimension: 'suite', value: 's' }, { dimension: 'class', value: 'c' }])).toBe(
      '?drill=suite%7Es&drill=class%7Ec',
    )
  })
})
