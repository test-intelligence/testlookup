import type { SuiteParityCutoverResult, SuiteParityHistory, SuiteParityQueued, SuiteParityRunDetail } from '@/types/suiteParity'
import { getData, postData } from './http'

/**
 * W25a HYG-04 phase 1: the suite-membership cutover comparisons (instance ADMIN only); W25d: the cutover itself.
 *
 * Every request path is written out in full (the SPA wire contract reads the quoted literals). The POSTs suppress
 * the shared toast: the card renders a 409 / 503 refusal as the server's sentence, inline.
 */
export const suiteParityService = {
  /** The comparisons of the last 14 UTC days, newest first, with the streak, `cutover_ready` and `cutover`. */
  history: () => getData<SuiteParityHistory>('/api/v1/admin/maintenance/suite-membership-parity'),

  /** One comparison: its counts per reason, the projects with differences and the sample rows. */
  detail: (id: string) =>
    getData<SuiteParityRunDetail>(`/api/v1/admin/maintenance/suite-membership-parity/${encodeURIComponent(id)}`),

  /** Queue a comparison now (202); 409 while one is running, 503 when it could not be queued. */
  runNow: () =>
    postData<SuiteParityQueued>('/api/v1/admin/maintenance/suite-membership-parity', {}, { suppressToast: true }),

  /**
   * Stop writing the old suite-membership tables (200 with the time and the streak). The server refuses with 409 and
   * a fixed sentence unless the comparisons say ready, while a comparison runs, and once already cut over.
   */
  cutOver: () =>
    postData<SuiteParityCutoverResult>('/api/v1/admin/maintenance/suite-membership-cutover', {}, { suppressToast: true }),
}
