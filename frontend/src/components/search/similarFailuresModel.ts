/**
 * W12 SRCH-01 — the Similar failures tab's copy. Pure.
 *
 * `GET /search/similar/{id}` answers `semantic_status` (`ok` | `not_configured`
 * | `unavailable`); an empty list means something different under each, so
 * each gets its own sentence. A missing status (an older server) reads `ok`.
 */

export const SIMILAR_LIMIT = 5

export const SIMILAR_EMPTY_TEXT = {
  ok: "No similar failures in this project's semantic index.",
  not_configured: 'Similar failures need the semantic index: no ChromaDB server answers here.',
  unavailable: 'The semantic index answered with an error. Try again later.',
} as const

export const SIMILAR_LOAD_ERROR = 'Could not load similar failures.'

/** The sentence an empty answer shows for its `semantic_status`. */
export function emptyStateText(status: string | null | undefined): string {
  if (status === 'not_configured') return SIMILAR_EMPTY_TEXT.not_configured
  if (status === 'unavailable') return SIMILAR_EMPTY_TEXT.unavailable
  return SIMILAR_EMPTY_TEXT.ok
}

/** Only these results have the tab. */
export function hasSimilarFailures(status: string | null | undefined): boolean {
  const upper = String(status ?? '').toUpperCase()
  return upper === 'FAILED' || upper === 'BROKEN'
}
