/**
 * W03 AIH-05: the pure model behind the test History tab's "AI analysis across
 * runs" strip and its versions popover (contract W03 §9.8).
 *
 *  - `buildStripModel`: one chip per history point that HAS an AI analysis,
 *    oldest -> newest (the API sends the history newest first), and the change
 *    markers from `ai_changes`: category changes first (they are material), then
 *    root-cause rewordings, newest first within each group.
 *  - `versionLabel`: "v1 rules" / "v2 LLM" / "v3 human correction (anna)" /
 *    "v2 manual analysis" / "v2 bulk reclassify (anna)" / "v1 rules (backfilled)".
 *
 * Everything here returns plain strings: names, models and root causes are
 * untrusted text and the components render them as React text only. A missing
 * value reads "not reported", never 0 and never an invented value.
 */
import type {
  AITestVersion,
  TestHistoryAI,
  TestHistoryAIChange,
  TestHistoryAIPoint,
} from '@/types/aiTestHistory'

/** The engine word of an `analysis_mode` (contract §9.3: rules, ML, LLM, mixed). */
const ENGINE_WORDS: Record<string, string> = {
  rules: 'rules',
  ml: 'ML',
  llm: 'LLM',
  auto: 'auto',
  mixed: 'mixed',
  deterministic: 'deterministic',
}

/** "LLM" for `llm`, the raw mode for an unknown one, null when none was recorded. */
export function engineWord(mode: string | null | undefined): string | null {
  const raw = mode?.trim()
  if (!raw) return null
  return ENGINE_WORDS[raw.toLowerCase()] ?? raw
}

/** "LLM llama3.1" / "rules" / "engine not reported". */
export function engineText(mode: string | null | undefined, model: string | null | undefined): string {
  const text = [engineWord(mode), model?.trim() || null].filter(Boolean).join(' ')
  return text || 'engine not reported'
}

/** "85%" or "confidence not reported". */
export function confidenceText(confidence: number | null | undefined): string {
  return confidence == null || !Number.isFinite(confidence) ? 'confidence not reported' : `${Math.round(confidence)}%`
}

/** The category as the API names it, or "uncategorized" for a null category. */
export function categoryText(category: string | null | undefined): string {
  return category?.trim() || 'uncategorized'
}

/**
 * The label of one stored version. `actor.name` is untrusted text (returned as a
 * plain string). An unknown trigger keeps its own word, so a new trigger is
 * shown rather than hidden.
 */
export function versionLabel(
  v: Pick<AITestVersion, 'version' | 'trigger' | 'analysis_mode'> & { actor?: AITestVersion['actor'] },
): string {
  const n = `v${v.version}`
  const engine = engineWord(v.analysis_mode)
  const name = v.actor?.name?.trim()
  const who = name ? ` (${name})` : ''
  switch (v.trigger) {
    case 'pipeline':
      return `${n} ${engine ?? 'pipeline'}`
    case 'human_correction':
      return `${n} human correction${who}`
    case 'manual_analyze':
      return `${n} manual analysis`
    case 'bulk_reclassify':
      return `${n} bulk reclassify${who}`
    case 'backfill':
      return engine ? `${n} ${engine} (backfilled)` : `${n} backfilled`
    case 'current':
      return 'current'
    default:
      return `${n} ${v.trigger || 'version'}`
  }
}

/** Oldest -> newest, joined: "v1 rules · v2 LLM · v3 human correction (anna)". */
export function versionSummary(versions: readonly AITestVersion[]): string {
  return [...versions]
    .sort((a, b) => a.version - b.version)
    .map((v) => versionLabel(v))
    .join(' · ')
}

/** The marker sentence of one change. */
export function changeText(change: TestHistoryAIChange): string {
  const label = change.run_label?.trim() || (change.run_id ? change.run_id.slice(0, 8) : 'a run')
  if (change.kinds.includes('category')) {
    const confidence =
      change.from_confidence != null && change.to_confidence != null
        ? ` (confidence ${change.from_confidence}% → ${change.to_confidence}%)`
        : ''
    return `AI changed its mind on ${label}: ${categoryText(change.from_category)} → ${categoryText(change.to_category)}${confidence}`
  }
  if (change.kinds.includes('root_cause')) return `AI reworded its root cause on ${label}`
  return `AI analysis changed on ${label}`
}

export interface StripChip {
  /** Stable React key. */
  key: string
  runId: string | null
  runLabel: string
  /** That point's own test id (the popover's `test_id`). */
  testId: string
  category: string | null
  confidence: number | null
  /** The chip's text: "PRODUCT_BUG 85%" / "uncategorized". */
  text: string
  /** `{run_label} - {category} {confidence} - {engine} {model}`. */
  title: string
  versionCount: number
  /** The point is the run the page is showing. */
  isCurrentRun: boolean
  /** `kinds` of the change emitted for this point ([] when none). */
  changeKinds: string[]
  point: TestHistoryAIPoint
  ai: TestHistoryAI
}

export interface StripChange {
  key: string
  runId: string | null
  runLabel: string
  kinds: string[]
  /** "category" when the category changed, else "root_cause" / "other". */
  tone: 'category' | 'root_cause' | 'other'
  text: string
}

export interface StripModel {
  chips: StripChip[]
  changes: StripChange[]
}

const changeKey = (runId: string | null, runLabel: string) => (runId ? `id:${runId}` : `label:${runLabel}`)

/**
 * The strip's chips and markers. `history` is the API's order (newest first);
 * a point without `ai` (a passed run, or an older server) is skipped, so a
 * history with no AI at all yields no chips (the panel then mounts nothing).
 */
export function buildStripModel({
  history,
  changes,
  runId,
}: {
  history: readonly TestHistoryAIPoint[]
  changes?: readonly TestHistoryAIChange[] | null
  runId?: string | null
}): StripModel {
  const kindsByPoint = new Map<string, string[]>()
  for (const change of changes ?? []) {
    kindsByPoint.set(changeKey(change.run_id, change.run_label), [...(change.kinds ?? [])])
  }

  const chips: StripChip[] = []
  const oldestFirst = [...history].reverse()
  oldestFirst.forEach((point, index) => {
    const ai = point.ai
    if (!ai) return
    const category = ai.category ?? null
    const confidence = ai.confidence ?? null
    const text = confidence == null ? categoryText(category) : `${categoryText(category)} ${Math.round(confidence)}%`
    const title = [
      point.run_label,
      `${categoryText(category)} ${confidenceText(confidence)}`,
      engineText(ai.analysis_mode, ai.model),
    ].join(' - ')
    chips.push({
      key: point.run_id ?? `ai-${index}`,
      runId: point.run_id,
      runLabel: point.run_label,
      testId: ai.test_id,
      category,
      confidence,
      text,
      title,
      versionCount: Number.isFinite(ai.version_count) ? ai.version_count : 0,
      isCurrentRun: Boolean(runId && point.run_id === runId),
      changeKinds: kindsByPoint.get(changeKey(point.run_id, point.run_label)) ?? [],
      point,
      ai,
    })
  })

  const ordered = [...(changes ?? [])].reverse() // newest first
  const toChange = (change: TestHistoryAIChange, i: number): StripChange => {
    const kinds = [...(change.kinds ?? [])]
    return {
      key: `${changeKey(change.run_id, change.run_label)}#${i}`,
      runId: change.run_id,
      runLabel: change.run_label,
      kinds,
      tone: kinds.includes('category') ? 'category' : kinds.includes('root_cause') ? 'root_cause' : 'other',
      text: changeText({ ...change, kinds }),
    }
  }
  const all = ordered.map(toChange)
  const rank = { category: 0, other: 1, root_cause: 2 } as const
  // Stable: Array.prototype.sort keeps newest-first within a tone.
  const sorted = [...all].sort((a, b) => rank[a.tone] - rank[b.tone])

  return { chips, changes: sorted }
}
