/**
 * W13 TCL-06 — requirement traceability "lite" (D10): the copy and the pure
 * helpers of the case's Requirements section and of the Requirement coverage
 * card (no React, no fetching).
 *
 * `GAP_REASON_TEXT` is the server's `REQUIREMENT_GAP_TEXT` and
 * `REQUIREMENT_KEY_INVALID` its sentence of the same name
 * (`backend/app/core/test_library_vocab.py`), pinned by `requirementsModel.test.ts`.
 * The stat is "Requirements verified" with BOTH numbers ("12 of 15"), never a
 * lone percentage (and never the old invented stat's words: §0.20).
 */
import type { RequirementCoverageSummary, RequirementGap, RequirementLink } from '@/types/test-library'
import { formatNumber } from '@/utils/formatters'

/** = the server's `REQUIREMENT_LINK_TYPES`. */
export const LINK_TYPES = ['verifies', 'partially_verifies', 'related'] as const

/** A link's type as its row shows it. */
export const LINK_TYPE_LABELS: Record<string, string> = {
  verifies: 'verifies',
  partially_verifies: 'partly verifies',
  related: 'related',
}

/** The Add form's Link type options (Verifies is the default). */
export const LINK_TYPE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: 'verifies', label: 'Verifies' },
  { value: 'partially_verifies', label: 'Partly verifies' },
  { value: 'related', label: 'Related' },
]

/** = the server's `REQUIREMENT_GAP_TEXT`. */
export const GAP_REASON_TEXT: Record<string, string> = {
  no_link: 'No case links it.',
  related_only: 'Only "related" links: no case verifies it.',
  partly_verified: 'Only partly verified.',
}

export const REQUIREMENT_KEY_MAX = 200
export const REQUIREMENT_TITLE_MAX = 500
/** = the server's `REQUIREMENT_KEY_INVALID`. */
export const REQUIREMENT_KEY_INVALID = 'A requirement key is 1 to 200 characters with no control characters.'
/** = the server's `RETIRED_STATES`: a retired case neither verifies nor takes a change. */
export const RETIRED_STATES: ReadonlySet<string> = new Set(['deprecated', 'archived'])

export const REQUIREMENTS_TITLE = 'Requirements'
export const REQUIREMENTS_EMPTY = 'No requirement is linked to this case yet.'
export const REQUIREMENTS_ERROR = 'Could not load the requirements.'
export const REQUIREMENTS_LOADING = 'Loading the requirements…'
export const ADD_TITLE = 'Add requirement'
export const ADD_TITLE_ID = 'tm-requirement-add-title'
export const ADD_FAILED = 'The requirement could not be added. Try again.'
export const REMOVE_FAILED = 'The requirement could not be removed. Try again.'

export const COVERAGE_TITLE = 'Requirement coverage'
export const VERIFIED_LABEL = 'Requirements verified'
export const UNMEASURED_TEXT = 'No requirement keys are known to this project yet: add one to a test case.'
export const TRUNCATED_TEXT = 'Counts are lower bounds: a source holds more than 5,000 keys.'
export const GAPS_ERROR = 'Could not load the gaps.'
export const GAPS_LOADING = 'Loading the gaps…'
export const SHOW_ALL_GAPS = 'Show all gaps'
export const HIDE_ALL_GAPS = 'Hide the gaps'
export const GAPS_PAGE_SIZE = 25

/** The trimmed key, or the server's sentence when it cannot be one (1-200 characters, no control character). */
export function keyProblem(raw: string): string | null {
  const key = raw.trim()
  // eslint-disable-next-line no-control-regex
  if (key.length < 1 || key.length > REQUIREMENT_KEY_MAX || /[\u0000-\u001f\u007f]/.test(raw)) return REQUIREMENT_KEY_INVALID
  return null
}

/** "added by Priya Shah"; nobody recorded "added by not recorded" (never blank). */
export function addedByLine(link: Pick<RequirementLink, 'created_by_name'>): string {
  const name = link.created_by_name?.trim()
  return name ? `added by ${name}` : 'added by not recorded'
}

export function linkTypeLabel(linkType: string): string {
  return LINK_TYPE_LABELS[linkType] ?? linkType.replace(/_/g, ' ')
}

/** The stat's value: "12 of 15" (both numbers, always). */
export function verifiedValue(summary: Pick<RequirementCoverageSummary, 'verified' | 'known'>): string {
  return `${formatNumber(summary.verified)} of ${formatNumber(summary.known)}`
}

/** "2 partly verified". */
export function partlyLine(summary: Pick<RequirementCoverageSummary, 'partly_verified'>): string {
  return `${formatNumber(summary.partly_verified)} partly verified`
}

/** A gap's reason sentence (the server's words); an unknown reason as written. */
export function gapReason(gap: Pick<RequirementGap, 'reason'>): string {
  return GAP_REASON_TEXT[gap.reason] ?? gap.reason
}

/** Where a key is known from: "a link", "a generation batch (claims 3 cases)", "a knowledge source". */
export function sourcesLine(gap: Pick<RequirementGap, 'sources' | 'batch_claimed_cases'>): string {
  const parts = gap.sources.map((source) => {
    if (source === 'link') return 'a link'
    if (source === 'generation_batch') {
      const claimed = gap.batch_claimed_cases ?? 0
      return claimed > 0 ? `a generation batch (claims ${formatNumber(claimed)} ${claimed === 1 ? 'case' : 'cases'})` : 'a generation batch'
    }
    if (source === 'knowledge_source') return 'a knowledge source'
    return source.replace(/_/g, ' ')
  })
  return parts.length ? `known from ${parts.join(', ')}` : ''
}

/** "4 approved or active cases link no requirement". */
export function casesWithoutLine(total: number): string {
  return `${formatNumber(total)} approved or active ${total === 1 ? 'case links' : 'cases link'} no requirement`
}

/** "25 of 40" — the page's span over the total (a count, not the page length). */
export function gapsPageLine(page: number, size: number, shown: number, total: number): string {
  if (total === 0) return '0 gaps'
  const from = (page - 1) * size + 1
  const to = from + shown - 1
  return `${formatNumber(from)}-${formatNumber(to)} of ${formatNumber(total)}`
}
