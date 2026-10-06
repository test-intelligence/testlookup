/**
 * What a suite NAME may be (contract C1, `suite_name_length`), with no store
 * attached: the cross-filter checks a name by these rules (P2).
 */

/** Longest suite name the contract accepts (C1, `suite_name_length`). */
export const SUITE_NAME_MAX = 500

/** A suite name the contract accepts: 1–500 code points, not only whitespace. */
export function isValidSuiteName(name: unknown): name is string {
  if (typeof name !== 'string') return false
  const length = [...name].length
  return length >= 1 && length <= SUITE_NAME_MAX && name.trim() !== ''
}
