/**
 * The copy `ChartFrame` shows for each chart state (VIZ-107). One place, so the
 * frame, its tests and the gallery fixtures cannot drift apart. Zero-result copy
 * blames the filters, not the data; error copy never blames the reader for a
 * server fault.
 */
export const CHART_MESSAGES = {
  loading: 'Loading chart',
  neverHadData: 'No runs have been ingested for this project yet',
  ingestAction: 'Ingest test results',
  filteredEmpty: 'No data matches the current filters',
  clearFilters: 'Clear filters',
  notMeasured: 'Not measured',
  error: 'Could not load this chart',
  rateLimited: 'Waiting to retry',
  retry: 'Retry',
  forbidden: 'You do not have access to this project',
  sessionExpired: 'Your session has expired',
  signIn: 'Sign in',
  viewTable: 'View as table',
  hideTable: 'Hide table',
  /**
   * The full-screen button's NAME, which changes with the state (VIZ-608).
   * Not `aria-pressed`: the button does a different thing in each state, and
   * the spec names both actions; "Full screen, pressed" would leave a reader
   * to work out that pressing it again means leaving.
   */
  fullScreen: 'Full screen',
  exitFullScreen: 'Exit full screen',
} as const
