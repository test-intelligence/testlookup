/**
 * Why a host withholds the release-decision override on a quick-look
 * (synthesized) decision: the API refuses an override without a stored
 * decision (404). One sentence for both hosts, the release gate and the run's
 * Analysis tab, so they cannot drift apart (D-0007).
 */
export const NO_STORED_DECISION = 'Run deep investigation first: there is no stored release decision to override yet.'
