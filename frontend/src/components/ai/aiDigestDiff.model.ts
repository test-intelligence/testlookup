/**
 * W03 AIH-02/03/04: the pure model of an AI digest diff (contract §3.1, §9.4) and the words of the AI history UI.
 *
 * `buildDiffModel(diff, {mode})` turns the server's `AIDigestDiff` into what the component renders: the sections in
 * their fixed order (provenance FIRST, so an engine change is read before the category churn it causes, then
 * verdict, categories, clusters, tests, actions), each omitted when it has nothing to say, grouped, sorted and
 * labelled for the mode. `versions` compares two analyses of one run ("Added / Removed / Resized"); `cross_run`
 * compares two runs ("New root causes / Resolved / Persisting", "New failing / Resolved / Changed").
 *
 * Everything here is data: names, labels and excerpts are AI or CI text and are rendered as React text by the
 * components, never as markup.
 */
import type {
  AIDiffClusterMatch,
  AIDiffMode,
  AIDiffTestChange,
  AIDiffTestRef,
  AIDigestDiff,
  AIVersionSummary,
  RunBrief,
} from '@/types/aiHistory'
import { formatDistanceStrict } from 'date-fns'
import { fromNow } from '@/utils/formatters'

export type DiffSectionId = 'provenance' | 'verdict' | 'categories' | 'clusters' | 'tests' | 'actions'

export const DIFF_SECTION_ORDER: readonly DiffSectionId[] = ['provenance', 'verdict', 'categories', 'clusters', 'tests', 'actions']

export interface DiffProvenanceModel {
  note: string
  changes: string[]
  engineOnly: boolean
}

export interface DiffVerdictModel {
  changed: boolean
  /** `worse` | `better` | `same` | `unknown` */
  direction: string
  text: string
  risk: string | null
}

export interface DiffCategoryRow {
  category: string
  from: number
  to: number
  delta: number
  text: string
}

export interface DiffClusterRow {
  key: string
  label: string
  size: number
  detail: string
  matchedBy: string | null
  jaccard: number | null
}

export interface DiffClusterGroup {
  id: 'added' | 'removed' | 'matched'
  label: string
  rows: DiffClusterRow[]
}

export interface DiffTestRow {
  key: string
  fingerprint: string
  name: string
  suite: string | null
  /** The test to open: the `to` side's for added / changed rows, the `from` side's for removed rows. */
  testId: string | null
  /** Which run `testId` belongs to. */
  side: 'to' | 'from'
  category: string | null
  detail: string
  material: boolean
}

export interface DiffTestGroup {
  id: 'added' | 'removed' | 'changed' | 'reworded'
  label: string
  rows: DiffTestRow[]
}

export interface DiffModel {
  mode: AIDiffMode
  identical: boolean
  /** No section has anything to say. */
  isEmpty: boolean
  emptyText: string
  /** The sections present, in the fixed order. */
  sections: DiffSectionId[]
  provenance: DiffProvenanceModel | null
  verdict: DiffVerdictModel | null
  categories: DiffCategoryRow[]
  clusters: DiffClusterGroup[]
  tests: { groups: DiffTestGroup[]; truncated: boolean; persistingUnchanged: number }
  actions: { added: string[]; removed: string[] }
}

const byText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)
const list = <T>(value: T[] | null | undefined): T[] => (Array.isArray(value) ? value : [])
const yesNo = (value: boolean | null | undefined) => (value ? 'yes' : 'no')
const word = (value: string | null | undefined, empty = 'none') => (value == null || value === '' ? empty : value)

/** `+3` / `-2` / `0`. */
export function signed(delta: number): string {
  return delta > 0 ? `+${delta}` : String(delta)
}

/** How an analysis engine reads in the UI: `rules`, `ML`, `LLM`, `mixed`, ... */
export function engineWord(mode: string | null | undefined): string {
  switch ((mode ?? '').toLowerCase()) {
    case 'llm':
      return 'LLM'
    case 'ml':
      return 'ML'
    case 'rules':
      return 'rules'
    case 'mixed':
      return 'mixed'
    case 'auto':
      return 'auto'
    case 'deterministic':
      return 'deterministic'
    case '':
      return 'unknown engine'
    default:
      return mode as string
  }
}

/** `LLM llama3.1`, `rules`, `LLM`: the engine and, when known, the model. */
export function engineLabel(version: Pick<AIVersionSummary, 'analysis_mode' | 'llm_model'>): string {
  const model = version.llm_model && version.llm_model.toLowerCase() !== 'none' ? version.llm_model : ''
  return [engineWord(version.analysis_mode), model].filter(Boolean).join(' ')
}

/** `about 2 hours ago`, or '' when the instant is missing or unreadable. */
export function whenLabel(instant: string | null | undefined): string {
  if (!instant) return ''
  const at = new Date(instant)
  return Number.isNaN(at.getTime()) ? '' : fromNow(at)
}

/** The picker's header and option text: `v3 · LLM llama3.1 · about 2 hours ago`. */
export function versionLabel(version: Pick<AIVersionSummary, 'version' | 'analysis_mode' | 'llm_model' | 'generated_at'>): string {
  return [`v${version.version}`, engineLabel(version), whenLabel(version.generated_at)].filter(Boolean).join(' · ')
}

/** A run as people name it: `#58` (its sequence), else its build, else the start of its id. */
export function runLabel(run: Pick<RunBrief, 'run_seq' | 'build_number' | 'run_id'> | null | undefined): string {
  if (!run) return 'the previous run'
  if (run.run_seq != null) return `#${run.run_seq}`
  if (run.build_number) return `#${run.build_number}`
  return `#${run.run_id.slice(0, 8)}`
}

/** The two sides of the open version diff (`AIAnalysisHistory`'s drawer). */
export interface DiffSides {
  fromId: string | null
  /** A version id or `live`. */
  to: string | null
  fromLabel: string
  toLabel: string
  /** Why there is nothing to request: a version that is gone, or nothing to compare. */
  problem: 'missing' | 'nothing' | null
}

/**
 * The open diff's sides, oldest -> newest whatever order the two were picked in; "Current" (no viewed version) and
 * `live` are the current data, always the newer side.
 */
export function diffSides(
  versions: readonly AIVersionSummary[],
  viewed: number | null,
  compare: number | 'live',
): DiffSides {
  const find = (n: number) => versions.find((v) => v.version === n) ?? null
  const none: DiffSides = { fromId: null, to: null, fromLabel: '', toLabel: '', problem: 'nothing' }
  const viewedVersion = viewed == null ? null : find(viewed)
  if (viewed != null && !viewedVersion) return { ...none, problem: 'missing' }
  if (compare === 'live') {
    if (!viewedVersion) return none
    return { fromId: viewedVersion.version_id, to: 'live', fromLabel: versionLabel(viewedVersion), toLabel: 'Current data', problem: null }
  }
  const other = find(compare)
  if (!other) return { ...none, problem: 'missing' }
  if (!viewedVersion) {
    return { fromId: other.version_id, to: 'live', fromLabel: versionLabel(other), toLabel: 'Current data', problem: null }
  }
  if (other.version === viewedVersion.version) return none
  const [older, newer] = other.version < viewedVersion.version ? [other, viewedVersion] : [viewedVersion, other]
  return { fromId: older.version_id, to: newer.version_id, fromLabel: versionLabel(older), toLabel: versionLabel(newer), problem: null }
}

/** `3 hours earlier` (between the two runs' finish), else `about 3 hours ago`, else ''. */
function gapLabel(baseline: Pick<RunBrief, 'finished_at'>, current: Pick<RunBrief, 'finished_at'> | null | undefined): string {
  const from = baseline.finished_at ? new Date(baseline.finished_at) : null
  const to = current?.finished_at ? new Date(current.finished_at) : null
  if (from && to && !Number.isNaN(from.getTime()) && !Number.isNaN(to.getTime()) && to > from) {
    return `${formatDistanceStrict(from, to)} earlier`
  }
  return whenLabel(baseline.finished_at)
}

/** The previous-run card's baseline chip: `Compared with #58 (Auth, main, 3 hours earlier)`. */
export function baselineChipText(
  baseline: Pick<RunBrief, 'run_seq' | 'build_number' | 'run_id' | 'primary_suite_name' | 'branch' | 'finished_at'>,
  current?: Pick<RunBrief, 'finished_at'> | null,
): string {
  const facts = [baseline.primary_suite_name, baseline.branch, gapLabel(baseline, current)].filter(Boolean)
  return `Compared with ${runLabel(baseline)}${facts.length ? ` (${facts.join(', ')})` : ''}`
}

/** The picker's empty state: no stored version of this run yet. */
export const FIRST_ANALYSIS_TEXT = 'First analysis of this run'

/** Why the previous-run card has nothing to compare, in words (contract §9.6 empty states). */
export function previousRunEmptyText(
  reason: string | null | undefined,
  context: { branch?: string | null; baseline?: Pick<RunBrief, 'run_seq' | 'build_number' | 'run_id'> | null; message?: string | null } = {},
): string {
  switch (reason) {
    case 'no_primary_suite':
    case 'run_lacks_suite':
      return 'This run has no suite attribution, so there is no previous run of the same suite to compare with.'
    case 'no_previous_run':
      return `No previous run on branch ${context.branch || 'no branch'} for this suite yet.`
    case 'baseline_not_analysed':
      return `${context.baseline ? runLabel(context.baseline) : 'The previous run'} has no AI analysis to compare.`
    case 'current_not_analysed':
      return 'This run has no AI analysis to compare yet.'
    default:
      return context.message || 'There is nothing to compare this run with.'
  }
}

function provenanceModel(diff: AIDigestDiff): DiffProvenanceModel | null {
  const p = diff.provenance
  if (!p?.changed) return null
  const changes: string[] = []
  if (p.engine) changes.push(`engine ${engineWord(p.engine.from)} -> ${engineWord(p.engine.to)}`)
  if (p.model) changes.push(`model ${word(p.model.from)} -> ${word(p.model.to)}`)
  if (p.fallback_used) changes.push(`fallback ${yesNo(p.fallback_used.from)} -> ${yesNo(p.fallback_used.to)}`)
  if (p.deterministic) changes.push(`deterministic ${yesNo(p.deterministic.from)} -> ${yesNo(p.deterministic.to)}`)
  // The server names an engine or model change in `note`; without one, say only what is known.
  const note =
    p.note ||
    'How these analyses were produced changed (see below): category changes may reflect that, not the product.'
  return { note, changes, engineOnly: !!p.engine_only }
}

function verdictModel(diff: AIDigestDiff): DiffVerdictModel | null {
  const v = diff.verdict
  if (!v) return null
  const riskMoved = v.risk_delta != null && v.risk_delta !== 0
  if (!v.changed && !riskMoved) return null
  const text = v.changed ? `Verdict ${word(v.from, 'not decided')} -> ${word(v.to, 'not decided')}` : `Verdict ${word(v.to, 'not decided')} (unchanged)`
  const risk =
    v.from_risk != null || v.to_risk != null
      ? `risk ${v.from_risk ?? 'not reported'} -> ${v.to_risk ?? 'not reported'}${v.risk_delta != null ? ` (${signed(v.risk_delta)})` : ''}`
      : null
  return { changed: v.changed, direction: v.direction || 'unknown', text, risk }
}

function categoryRows(diff: AIDigestDiff): DiffCategoryRow[] {
  return list(diff.categories?.changed)
    .filter((c) => c.delta !== 0)
    .map((c) => ({ category: c.category, from: c.from, to: c.to, delta: c.delta, text: `${c.category} ${signed(c.delta)}` }))
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || byText(a.category, b.category))
}

const tests = (n: number) => `${n} test${n === 1 ? '' : 's'}`

function matchedRow(m: AIDiffClusterMatch): DiffClusterRow {
  const sizes = m.resized ? `${m.size_from} -> ${tests(m.size_to)}` : tests(m.size_to)
  const renamed = m.label_from && m.label_to && m.label_from !== m.label_to ? ` · was "${m.label_from}"` : ''
  const how = m.matched_by === 'overlap' ? ` · matched by overlap${m.jaccard != null ? ` (jaccard ${m.jaccard.toFixed(2)})` : ''}` : ''
  return {
    key: `matched:${m.signature_from}:${m.signature_to}`,
    label: m.label_to || m.label_from || m.signature_to,
    size: m.size_to,
    detail: `${sizes}${renamed}${how}`,
    matchedBy: m.matched_by ?? null,
    jaccard: m.jaccard ?? null,
  }
}

function clusterGroups(diff: AIDigestDiff, mode: AIDiffMode): DiffClusterGroup[] {
  const cross = mode === 'cross_run'
  const bySize = (a: { size: number; signature: string }, b: { size: number; signature: string }) =>
    b.size - a.size || byText(a.signature, b.signature)
  const added = [...list(diff.clusters?.added)].sort(bySize).map((c) => ({
    key: `added:${c.signature}`, label: c.label || c.signature, size: c.size, detail: tests(c.size), matchedBy: null, jaccard: null,
  }))
  const removed = [...list(diff.clusters?.removed)].sort(bySize).map((c) => ({
    key: `removed:${c.signature}`, label: c.label || c.signature, size: c.size, detail: tests(c.size), matchedBy: null, jaccard: null,
  }))
  const matched = [...list(diff.clusters?.matched)]
    // Two analyses of one run: a cluster that kept its size is not a change. Two runs: it is a persisting cause.
    .filter((m) => cross || m.resized)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || byText(a.signature_to, b.signature_to))
    .map(matchedRow)
  const groups: DiffClusterGroup[] = [
    { id: 'added', label: cross ? 'New root causes' : 'Added', rows: added },
    { id: 'removed', label: cross ? 'Resolved' : 'Removed', rows: removed },
    { id: 'matched', label: cross ? 'Persisting' : 'Resized', rows: matched },
  ]
  return groups.filter((g) => g.rows.length > 0)
}

function refDetail(t: AIDiffTestRef): string {
  return `${word(t.category, 'UNCATEGORIZED')}${t.confidence != null ? ` (confidence ${t.confidence})` : ''}`
}

function refRow(t: AIDiffTestRef, group: 'added' | 'removed'): DiffTestRow {
  return {
    key: `${group}:${t.test_fingerprint}`,
    fingerprint: t.test_fingerprint,
    name: t.test_name || t.test_fingerprint,
    suite: t.suite_name ?? null,
    testId: t.test_id ?? null,
    side: group === 'removed' ? 'from' : 'to',
    category: t.category ?? null,
    detail: refDetail(t),
    material: true,
  }
}

function changeDetail(c: AIDiffTestChange): string {
  const parts: string[] = []
  const confidence = c.confidence ? `confidence ${c.confidence.from ?? '-'} -> ${c.confidence.to ?? '-'}` : ''
  if (c.category) {
    parts.push(`${word(c.category.from, 'UNCATEGORIZED')} -> ${word(c.category.to, 'UNCATEGORIZED')}${confidence ? ` (${confidence})` : ''}`)
  } else if (confidence) {
    parts.push(confidence)
  }
  if (c.flaky) parts.push(`flaky ${yesNo(c.flaky.from)} -> ${yesNo(c.flaky.to)}`)
  if (!c.material) return 'root cause reworded'
  if (c.root_cause_changed) parts.push('root cause reworded')
  return parts.join('; ')
}

function testGroups(diff: AIDigestDiff, mode: AIDiffMode): DiffTestGroup[] {
  const cross = mode === 'cross_run'
  const refOrder = (a: AIDiffTestRef, b: AIDiffTestRef) =>
    byText(a.category ?? '', b.category ?? '') ||
    (b.confidence ?? 0) - (a.confidence ?? 0) ||
    byText(a.test_name ?? '', b.test_name ?? '') ||
    byText(a.test_fingerprint, b.test_fingerprint)
  const changeOrder = (a: AIDiffTestChange, b: AIDiffTestChange) =>
    Number(!a.material) - Number(!b.material) ||
    Math.abs(b.confidence?.delta ?? 0) - Math.abs(a.confidence?.delta ?? 0) ||
    byText(a.test_name ?? '', b.test_name ?? '') ||
    byText(a.test_fingerprint, b.test_fingerprint)
  const changed = [...list(diff.tests?.changed)].sort(changeOrder)
  const toRow = (c: AIDiffTestChange): DiffTestRow => ({
    key: `changed:${c.test_fingerprint}`,
    fingerprint: c.test_fingerprint,
    name: c.test_name || c.test_fingerprint,
    suite: c.suite_name ?? null,
    testId: c.test_id ?? null,
    side: 'to',
    category: c.category?.to ?? null,
    detail: changeDetail(c),
    material: !!c.material,
  })
  const groups: DiffTestGroup[] = [
    { id: 'added', label: cross ? 'New failing' : 'Added', rows: [...list(diff.tests?.added)].sort(refOrder).map((t) => refRow(t, 'added')) },
    { id: 'removed', label: cross ? 'Resolved' : 'Removed', rows: [...list(diff.tests?.removed)].sort(refOrder).map((t) => refRow(t, 'removed')) },
    { id: 'changed', label: 'Changed', rows: changed.filter((c) => c.material).map(toRow) },
    { id: 'reworded', label: 'Reworded root cause', rows: changed.filter((c) => !c.material).map(toRow) },
  ]
  return groups.filter((g) => g.rows.length > 0)
}

/** The renderable model of one diff (pure; the component only draws it). */
export function buildDiffModel(diff: AIDigestDiff, options: { mode?: AIDiffMode } = {}): DiffModel {
  const mode: AIDiffMode = options.mode ?? (diff.mode === 'cross_run' ? 'cross_run' : 'versions')
  const provenance = provenanceModel(diff)
  const verdict = verdictModel(diff)
  const categories = categoryRows(diff)
  const clusters = clusterGroups(diff, mode)
  const testModel = {
    groups: testGroups(diff, mode),
    truncated: !!diff.tests?.truncated,
    persistingUnchanged: diff.tests?.persisting_unchanged ?? 0,
  }
  const actions = {
    added: [...list(diff.actions?.added)].sort(byText),
    removed: [...list(diff.actions?.removed)].sort(byText),
  }
  const present: Record<DiffSectionId, boolean> = {
    provenance: provenance != null,
    verdict: verdict != null,
    categories: categories.length > 0,
    clusters: clusters.length > 0,
    tests: testModel.groups.length > 0,
    actions: actions.added.length + actions.removed.length > 0,
  }
  const sections = DIFF_SECTION_ORDER.filter((id) => present[id])
  const identical = !!diff.identical
  return {
    mode,
    identical,
    isEmpty: sections.length === 0,
    emptyText: identical
      ? 'No differences: the two analyses are identical.'
      : 'No differences in the verdict, categories, root causes or tests.',
    sections,
    provenance,
    verdict,
    categories,
    clusters,
    tests: testModel,
    actions,
  }
}
