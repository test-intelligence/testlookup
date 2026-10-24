/**
 * W11 GATE-02: what changed between two versions of a release-gate policy,
 * one line per changed setting — computed on the client over the two
 * documents the history already returned (no diff route).
 *
 * The settings' labels live in ONE table, `POLICY_FIELD_LABELS`, which the
 * editor (`pages/PolicyEditorPage.tsx`) also reads for its own field labels, so
 * the Compare list and the form cannot name one field two ways.
 */
import type { PolicyDocument, PolicyKindBudget, PolicyRule } from '@/services/policyService'

/** The editor's field labels and hints — the one source for both surfaces. */
export const POLICY_FIELD_LABELS = {
  thresholds: {
    go_threshold: { label: 'GO Threshold', hint: 'Composite below this → GO' },
    no_go_threshold: { label: 'NO_GO Threshold', hint: 'Composite at or above this → NO_GO' },
    pass_rate_minimum: { label: 'Pass Rate Minimum', hint: 'Target pass rate (%)' },
    pass_rate_hard_floor_factor: { label: 'Hard Floor Factor', hint: 'Factor × min pass rate → force NO_GO' },
  },
  /** The four other sections, named before the setting's key in a Compare line. */
  sections: {
    pass_rate_bands: 'Pass-rate band',
    hard_caps: 'Hard cap',
    dimension_weights: 'Dimension weight',
    kind_rules: 'Failure-kind budget',
  },
} as const

export type PolicySection =
  | 'thresholds'
  | 'pass_rate_bands'
  | 'hard_caps'
  | 'dimension_weights'
  | 'kind_rules'
  | 'rules'

export interface PolicyChange {
  section: PolicySection
  /** The setting (or the rule id). */
  key: string
  /** What the line names: `GO threshold`, `Pass-rate band yellow_min`, `Rule "Block on P0"`. */
  label: string
  /** The two values as shown (`20`, `0.30`), or null for an added / removed rule or budget. */
  before: string | null
  after: string | null
  /** The whole line: `GO threshold 20 → 25`, `Rule "Block on P0" added`. */
  text: string
}

/** The Compare list's word for a setting: the editor's label in sentence case (`GO Threshold` → `GO threshold`). */
export function sentenceLabel(label: string): string {
  // The first word stays; so does an acronym (`GO`, `NO_GO`); the rest lower-case.
  const acronym = /^[A-Z][A-Z0-9_]+$/
  return label
    .split(' ')
    .map((word, index) => (index === 0 || acronym.test(word) ? word : word.toLowerCase()))
    .join(' ')
}

function show(value: unknown, fixed?: number): string {
  if (value === null || value === undefined) return 'none'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return String(value)
    if (fixed !== undefined) return value.toFixed(fixed)
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
  }
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function valueChange(
  section: PolicySection,
  key: string,
  label: string,
  a: unknown,
  b: unknown,
  fixed?: number,
): PolicyChange | null {
  const before = show(a, fixed)
  const after = show(b, fixed)
  if (before === after) return null
  return { section, key, label, before, after, text: `${label} ${before} → ${after}` }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

/** Keys of both objects: `a`'s order first, then `b`'s new ones. */
function keysOf(a: Record<string, unknown>, b: Record<string, unknown>): string[] {
  return [...Object.keys(a), ...Object.keys(b).filter((key) => !(key in a))]
}

function thresholdChanges(a: PolicyDocument, b: PolicyDocument): PolicyChange[] {
  const before = record(a.thresholds)
  const after = record(b.thresholds)
  const labels: Record<string, { label: string }> = POLICY_FIELD_LABELS.thresholds
  return keysOf(before, after)
    .map((key) => valueChange('thresholds', key, labels[key] ? sentenceLabel(labels[key].label) : `Threshold ${key}`, before[key], after[key]))
    .filter((change): change is PolicyChange => change !== null)
}

function keyedChanges(
  section: 'pass_rate_bands' | 'hard_caps' | 'dimension_weights',
  a: unknown,
  b: unknown,
  fixed?: number,
): PolicyChange[] {
  const before = record(a)
  const after = record(b)
  const prefix = POLICY_FIELD_LABELS.sections[section]
  return keysOf(before, after)
    .map((key) => valueChange(section, key, `${prefix} ${key}`, before[key], after[key], fixed))
    .filter((change): change is PolicyChange => change !== null)
}

const KIND_KEYS = ['infrastructure', 'test_code'] as const

function kindChanges(a: PolicyDocument, b: PolicyDocument): PolicyChange[] {
  const before = record(a.kind_rules)
  const after = record(b.kind_rules)
  const changes: PolicyChange[] = []
  const enabled = valueChange('kind_rules', 'enabled', 'Failure-kind weighting', Boolean(before.enabled), Boolean(after.enabled))
  if (enabled) changes.push(enabled)
  const prefix = POLICY_FIELD_LABELS.sections.kind_rules
  for (const kind of KIND_KEYS) {
    const was = (before[kind] ?? null) as PolicyKindBudget | null
    const now = (after[kind] ?? null) as PolicyKindBudget | null
    const label = `${prefix} ${kind}`
    if (!was && !now) continue
    if (!was || !now) {
      const word = now ? 'added' : 'removed'
      changes.push({ section: 'kind_rules', key: kind, label, before: null, after: null, text: `${label} ${word}` })
      continue
    }
    const budget = keysOf(record(was), record(now))
      .map((key) => valueChange('kind_rules', `${kind}.${key}`, `${label} ${key}`, record(was)[key], record(now)[key]))
      .filter((change): change is PolicyChange => change !== null)
    changes.push(...budget)
  }
  return changes
}

function ruleChanges(a: PolicyDocument, b: PolicyDocument): PolicyChange[] {
  const before: PolicyRule[] = Array.isArray(a.rules) ? a.rules : []
  const after: PolicyRule[] = Array.isArray(b.rules) ? b.rules : []
  const afterById = new Map(after.map((rule) => [rule.id, rule]))
  const beforeIds = new Set(before.map((rule) => rule.id))
  const changes: PolicyChange[] = []
  const line = (rule: PolicyRule, text: string, valueBefore: string | null = null, valueAfter: string | null = null) => {
    const label = `Rule "${rule.name}"`
    changes.push({ section: 'rules', key: rule.id, label, before: valueBefore, after: valueAfter, text: `${label} ${text}` })
  }
  for (const rule of before) {
    const next = afterById.get(rule.id)
    if (!next) {
      line(rule, 'removed')
      continue
    }
    if (next.name !== rule.name) line(rule, `renamed to "${next.name}"`, rule.name, next.name)
    if (Boolean(next.enabled) !== Boolean(rule.enabled)) {
      const was = rule.enabled ? 'enabled' : 'disabled'
      const now = next.enabled ? 'enabled' : 'disabled'
      line(next, `${was} → ${now}`, was, now)
    }
    const was = record(rule.params)
    const now = record(next.params)
    const params = keysOf(was, now)
      .filter((key) => show(was[key]) !== show(now[key]))
      .map((key) => `${key} ${show(was[key])} → ${show(now[key])}`)
    if (params.length > 0) line(next, `params changed (${params.join(', ')})`)
  }
  for (const rule of after) {
    if (!beforeIds.has(rule.id)) line(rule, 'added')
  }
  return changes
}

/**
 * Every setting that differs from `a` (the older version) to `b` (the newer):
 * thresholds, pass-rate bands, hard caps, dimension weights, failure-kind
 * budgets and the rules (matched by id). `[]` when the documents agree.
 */
export function diffPolicyDocuments(a: PolicyDocument, b: PolicyDocument): PolicyChange[] {
  return [
    ...thresholdChanges(a, b),
    ...keyedChanges('pass_rate_bands', a.pass_rate_bands, b.pass_rate_bands),
    ...keyedChanges('hard_caps', a.hard_caps, b.hard_caps),
    ...keyedChanges('dimension_weights', a.dimension_weights, b.dimension_weights, 2),
    ...kindChanges(a, b),
    ...ruleChanges(a, b),
  ]
}

export const NO_DIFFERENCES = 'No settings differ.'
