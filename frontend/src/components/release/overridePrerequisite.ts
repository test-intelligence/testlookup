/**
 * Why a host withholds the release-decision override on a quick-look
 * (synthesized) decision: the API refuses an override without a stored
 * decision (404). One sentence for both hosts, the release gate and the run's
 * Analysis tab, so they cannot drift apart (D-0007).
 */
export const NO_STORED_DECISION = 'Run deep investigation first: there is no stored release decision to override yet.'

/**
 * D-129 / D-03: "Run deep investigation first" is a dead end when deep investigation is turned off in
 * Settings › AI, so the sentence then says that instead. (Rules mode no longer refuses deep
 * investigation, so it reads the ordinary sentence.)
 */
export const NO_STORED_DECISION_DEEP_OFF =
  'There is no stored release decision to override, and deep investigation, which stores one, is turned off in Settings › AI.'

/** The sentence for a quick-look decision, following the same deep-investigation availability as its CTAs. */
export function noStoredDecisionReason(deep: { available: boolean }): string {
  return deep.available ? NO_STORED_DECISION : NO_STORED_DECISION_DEEP_OFF
}
