/**
 * W13 TCL-01: the Cases tab's filters as ONE state, its URL form, the list
 * read's params, the saved-view form and the four presets. Pure: no React, no
 * fetching (G-D's page and G-E's Views bar both read it).
 *
 * URL keys (the Cases tab only; the page writes them with `replace`):
 * `status`, `priority`, `type` (CSV lists), `owner`, `automation`,
 * `cases_suite` (the Suites tab's `suite` deep link keeps its own meaning),
 * `q`, `ai` (`1`), `requirement`. Every other key (`tab`, `case` …) is kept as
 * it is. Unknown values are dropped on read, so they are never sent.
 *
 * `owner: 'me'` is the READER: a shared "My drafts" view shows each reader
 * their own drafts.
 */
import type { CaseViewFilters } from '@/types/test-library'
import { TEST_CASE_LIFECYCLE_STATES } from '@/types/test-management'

export interface CaseFilterState {
  statuses: string[]
  priorities: string[]
  types: string[]
  /** `me` or a user id; null = anyone. */
  owner: string | null
  /** `automated` | `not_automated`; null = either. */
  automation: string | null
  suite: string | null
  q: string
  aiGenerated: boolean
  /** A requirement key (normalised by the server); set from a link or a gap, never from the bar. */
  requirement: string | null
}

export const EMPTY_CASE_FILTERS: CaseFilterState = {
  statuses: [],
  priorities: [],
  types: [],
  owner: null,
  automation: null,
  suite: null,
  q: '',
  aiGenerated: false,
  requirement: null,
}

/** The URL key of each filter. */
export const CASE_FILTER_URL_KEYS = {
  statuses: 'status',
  priorities: 'priority',
  types: 'type',
  owner: 'owner',
  automation: 'automation',
  suite: 'cases_suite',
  q: 'q',
  aiGenerated: 'ai',
  requirement: 'requirement',
} as const satisfies Record<keyof CaseFilterState, string>

/** = the server's `CASE_FILTER_MAX_VALUES`. */
export const CASE_FILTER_MAX_VALUES = 20
export const OWNER_ME = 'me'
/** The server's `CASE_AUTOMATION_FILTERS`. */
export const CASE_AUTOMATION_FILTERS = ['automated', 'not_automated'] as const
/** The nine lifecycle states (the Status filter's options). */
export const CASE_FILTER_STATUSES: readonly string[] = TEST_CASE_LIFECYCLE_STATES
/** = the server's `CASE_PRIORITIES`. */
export const CASE_FILTER_PRIORITIES = ['critical', 'high', 'medium', 'low'] as const
/** The nine types the Type filter offers (today's chip). */
export const CASE_TYPE_OPTIONS = [
  { value: 'functional', label: 'Functional' },
  { value: 'integration', label: 'Integration' },
  { value: 'e2e', label: 'E2E' },
  { value: 'regression', label: 'Regression' },
  { value: 'smoke', label: 'Smoke' },
  { value: 'performance', label: 'Performance' },
  { value: 'security', label: 'Security' },
  { value: 'usability', label: 'Usability' },
  { value: 'api', label: 'API' },
] as const
export const CASE_FILTER_TYPES: readonly string[] = CASE_TYPE_OPTIONS.map((option) => option.value)

const SUITE_MAX = 500
const REQUIREMENT_MAX = 200
const Q_MAX = 200
const USER_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export type CasePresetId = 'my_drafts' | 'my_p0_p1' | 'unautomated' | 'ai_drafts'
export type CasePresetFilters = Omit<CaseViewFilters, 'kind'>

/** = the server's `CASE_PRESETS` (pinned by `backend/tests/test_case_presets_frontend_parity.py`). */
export const CASE_PRESETS: Record<CasePresetId, CasePresetFilters> = {
  my_drafts: { owner: 'me', statuses: ['draft'] },
  my_p0_p1: { owner: 'me', priorities: ['critical', 'high'] },
  unautomated: { automation: 'not_automated' },
  ai_drafts: { statuses: ['draft'], ai_generated: true },
}

/** = the server's `CASE_PRESET_LABELS`. */
export const CASE_PRESET_LABELS: Record<CasePresetId, string> = {
  my_drafts: 'My drafts',
  my_p0_p1: 'My P0/P1',
  unautomated: 'Unautomated',
  ai_drafts: 'AI drafts',
}

/** The Views row's order. */
export const CASE_PRESET_IDS: readonly CasePresetId[] = ['my_drafts', 'my_p0_p1', 'unautomated', 'ai_drafts']

// ── Value rules (one per field; the URL and a saved view read through them) ──

/** Known values only, trimmed, de-duplicated (order kept), at most 20. */
function knownList(values: readonly unknown[], allowed: readonly string[]): string[] {
  const out: string[] = []
  for (const raw of values) {
    if (typeof raw !== 'string') continue
    const value = raw.trim()
    if (allowed.includes(value) && !out.includes(value)) out.push(value)
    if (out.length === CASE_FILTER_MAX_VALUES) break
  }
  return out
}

function ownerValue(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const value = raw.trim()
  if (value === OWNER_ME) return OWNER_ME
  return USER_ID_RE.test(value) ? value.toLowerCase() : null
}

function automationValue(raw: unknown): string | null {
  return typeof raw === 'string' && (CASE_AUTOMATION_FILTERS as readonly string[]).includes(raw) ? raw : null
}

function boundedText(raw: unknown, max: number): string | null {
  if (typeof raw !== 'string') return null
  const value = raw.trim()
  return value.length >= 1 && value.length <= max ? value : null
}

function splitCsv(params: URLSearchParams, key: string): string[] {
  return params.getAll(key).flatMap((value) => value.split(','))
}

// ── URL ↔ state ──────────────────────────────────────────────────────────────

/** The filters a URL names; unknown values dropped, every other key ignored. */
export function readCaseFilters(params: URLSearchParams): CaseFilterState {
  const k = CASE_FILTER_URL_KEYS
  const ai = params.get(k.aiGenerated)
  return {
    statuses: knownList(splitCsv(params, k.statuses), CASE_FILTER_STATUSES),
    priorities: knownList(splitCsv(params, k.priorities), CASE_FILTER_PRIORITIES),
    types: knownList(splitCsv(params, k.types), CASE_FILTER_TYPES),
    owner: ownerValue(params.get(k.owner)),
    automation: automationValue(params.get(k.automation)),
    suite: boundedText(params.get(k.suite), SUITE_MAX),
    q: (params.get(k.q) ?? '').slice(0, Q_MAX),
    aiGenerated: ai === '1' || ai === 'true',
    requirement: boundedText(params.get(k.requirement), REQUIREMENT_MAX),
  }
}

/**
 * A NEW URLSearchParams: every non-filter key kept as it was (`tab`, `case`,
 * `suite` …), the filter keys rewritten from `state` (CSV lists; an empty
 * value writes no key).
 */
export function writeCaseFilters(params: URLSearchParams, state: CaseFilterState): URLSearchParams {
  const k = CASE_FILTER_URL_KEYS
  const next = new URLSearchParams(params)
  for (const key of Object.values(k)) next.delete(key)
  if (state.statuses.length) next.set(k.statuses, state.statuses.join(','))
  if (state.priorities.length) next.set(k.priorities, state.priorities.join(','))
  if (state.types.length) next.set(k.types, state.types.join(','))
  if (state.owner) next.set(k.owner, state.owner)
  if (state.automation) next.set(k.automation, state.automation)
  if (state.suite) next.set(k.suite, state.suite)
  if (state.q) next.set(k.q, state.q)
  if (state.aiGenerated) next.set(k.aiGenerated, '1')
  if (state.requirement) next.set(k.requirement, state.requirement)
  return next
}

/**
 * `GET /test-management/cases` params. Lists are ARRAYS (Axios repeats the
 * key: `status=draft&status=review_requested`), one value included; the
 * server's names (`test_type`, `search`, `suite_name`, `ai_generated`);
 * `include_archived` when Archived is among the statuses (today's rule).
 */
export function toListParams(
  state: CaseFilterState,
  { page, size, includeAutomation }: { page: number; size: number; includeAutomation: boolean },
): Record<string, unknown> {
  const params: Record<string, unknown> = { page, size }
  if (state.statuses.length) params.status = [...state.statuses]
  if (state.types.length) params.test_type = [...state.types]
  if (state.priorities.length) params.priority = [...state.priorities]
  const q = state.q.trim()
  if (q) params.search = q
  if (state.owner) params.owner = state.owner
  if (state.automation) params.automation = state.automation
  if (state.suite) params.suite_name = state.suite
  if (state.requirement) params.requirement = state.requirement
  if (includeAutomation) params.include_automation = true
  if (state.aiGenerated) params.ai_generated = true
  if (state.statuses.includes('archived')) params.include_archived = true
  return params
}

// ── Saved view ↔ state ──────────────────────────────────────────────────────

/** A saved case view's `filters` (`kind: 'test_case_view'`): only the set values. */
export function viewFilters(state: CaseFilterState): CaseViewFilters {
  const filters: CaseViewFilters = { kind: 'test_case_view' }
  if (state.statuses.length) filters.statuses = [...state.statuses]
  if (state.priorities.length) filters.priorities = [...state.priorities]
  if (state.types.length) filters.types = [...state.types]
  if (state.owner) filters.owner = state.owner
  if (state.automation) filters.automation = state.automation
  if (state.suite) filters.suite = state.suite
  const q = state.q.trim()
  if (q) filters.q = q
  if (state.aiGenerated) filters.ai_generated = true
  if (state.requirement) filters.requirement = state.requirement
  return filters
}

function listOf(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/**
 * The state a stored view (or a preset) names. A view outlives the vocabulary
 * it was saved under and a shared one was written by someone else: every
 * value goes through the same rule as the URL, unknown ones dropped.
 */
export function filtersFromView(filters: Partial<CaseViewFilters> | Record<string, unknown> | null | undefined): CaseFilterState {
  const f = (filters ?? {}) as Record<string, unknown>
  return {
    statuses: knownList(listOf(f.statuses), CASE_FILTER_STATUSES),
    priorities: knownList(listOf(f.priorities), CASE_FILTER_PRIORITIES),
    types: knownList(listOf(f.types), CASE_FILTER_TYPES),
    owner: ownerValue(f.owner),
    automation: automationValue(f.automation),
    suite: boundedText(f.suite, SUITE_MAX),
    q: typeof f.q === 'string' ? f.q.trim().slice(0, Q_MAX) : '',
    aiGenerated: f.ai_generated === true,
    requirement: boundedText(f.requirement, REQUIREMENT_MAX),
  }
}

/** The state a preset applies (it REPLACES every filter key). */
export function presetFilters(id: CasePresetId): CaseFilterState {
  return filtersFromView(CASE_PRESETS[id])
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false
  const sorted = [...b].sort()
  return [...a].sort().every((value, index) => value === sorted[index])
}

/** Equal filters: lists compared as sets, `q` trimmed. */
export function sameFilters(a: CaseFilterState, b: CaseFilterState): boolean {
  return sameList(a.statuses, b.statuses)
    && sameList(a.priorities, b.priorities)
    && sameList(a.types, b.types)
    && a.owner === b.owner
    && a.automation === b.automation
    && a.suite === b.suite
    && a.q.trim() === b.q.trim()
    && a.aiGenerated === b.aiGenerated
    && a.requirement === b.requirement
}

/** The preset chip that is active: the one whose filters EQUAL the state, else null. */
export function activePreset(state: CaseFilterState): CasePresetId | null {
  return CASE_PRESET_IDS.find((id) => sameFilters(presetFilters(id), state)) ?? null
}

/** Any filter set at all (an empty state lists every case). */
export function hasCaseFilters(state: CaseFilterState): boolean {
  return !sameFilters(state, EMPTY_CASE_FILTERS)
}

/**
 * A stable string for the state (lists sorted): the bulk selection's scope and
 * SWR params can key on it, so a filter change clears a selection while a
 * re-render with equal filters does not.
 */
export function caseFiltersKey(state: CaseFilterState): string {
  return JSON.stringify([
    [...state.statuses].sort(), [...state.priorities].sort(), [...state.types].sort(), state.owner,
    state.automation, state.suite, state.q.trim(), state.aiGenerated, state.requirement,
  ])
}
