/**
 * The Trends page's reports (TRND-01) and its server-side trend summary
 * (TRND-06). One Axios base (`api` / `http`), like every service.
 *
 *   GET  /api/v1/reports/email-trends/availability  whether the relay saved in
 *        Settings › Notifications can deliver (QA lead+; asked only while the
 *        Email report dialog is open)
 *   POST /api/v1/reports/email-trends               one message to the chosen
 *        members and typed addresses (QA lead+)
 *   GET  /api/v1/reports/trends/pdf                 the page's window, suite
 *        and release as a PDF (any member)
 *   GET  /api/v1/analytics/trend-summary            the verdict, the confidence
 *        score, the variance and the run gap the page shows
 *
 * The email POST suppresses the global toast: the dialog shows the server's
 * own refusal (422 / 502 / 503 / 504) as text under the form.
 */
import { api } from './api'
import { getData, postData } from './http'
import { scopeParam, type ScopeValue } from '@/lib/scopeParams'
import type { EmailAvailability, EmailTrendsBody, EmailTrendsResult } from '@/types/trends'

export const TREND_SUMMARY_URL = '/api/v1/analytics/trend-summary'
export const EMAIL_AVAILABILITY_URL = '/api/v1/reports/email-trends/availability'
export const EMAIL_TRENDS_URL = '/api/v1/reports/email-trends'
export const TRENDS_PDF_URL = '/api/v1/reports/trends/pdf'

/** The release scope, OMITTED when absent (the `metricsService` rule). */
function releaseParam(release: ScopeValue | undefined): Record<string, string | string[]> {
  return scopeParam('release_id', release ?? null)
}

/** `GET /analytics/trend-summary` — `project_id` omitted in All Projects (the `getTrends` param shape). */
export function trendsSummaryFetch(
  projectId: string | null,
  days: number,
  suiteFilter: ScopeValue | undefined,
  release: ScopeValue | undefined,
): Promise<unknown> {
  return getData<unknown>(TREND_SUMMARY_URL, {
    params: {
      ...(projectId ? { project_id: projectId } : {}),
      days,
      ...scopeParam('suite_name', suiteFilter ?? null),
      ...releaseParam(release),
    },
  })
}

export function getEmailAvailability(): Promise<EmailAvailability> {
  return getData<EmailAvailability>(EMAIL_AVAILABILITY_URL, { suppressToast: true })
}

export function emailTrends(body: EmailTrendsBody): Promise<EmailTrendsResult> {
  return postData<EmailTrendsResult, EmailTrendsBody>(EMAIL_TRENDS_URL, body, { suppressToast: true })
}

export interface TrendsPdfInput {
  projectId: string
  days: number
  suite: ScopeValue | undefined
  release: ScopeValue | undefined
}

export async function downloadTrendsPdf({ projectId, days, suite, release }: TrendsPdfInput): Promise<Blob> {
  const response = await api.get(TRENDS_PDF_URL, {
    params: { project_id: projectId, days, ...scopeParam('suite_name', suite ?? null), ...releaseParam(release) },
    responseType: 'blob',
    // The page shows its own toast with the server's reason.
    suppressToast: true,
  })
  return response.data as Blob
}

/**
 * The file name the server sends (`trends-{slug}-{days}d.pdf`), computed with
 * the same slug rule as the Summary export (`report_export_service`): lower
 * case, runs of anything but `a-z0-9_-` become `_`, trimmed of `_`.
 */
export function trendsPdfFilename(projectName: string | null | undefined, days: number): string {
  const slug = (projectName || 'project').toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '')
  return `trends-${slug || 'project'}-${days}d.pdf`
}

/**
 * The server's reason from a failed request, as text. A `responseType: 'blob'`
 * error carries its JSON body as a Blob, so it is read before it is parsed.
 */
export async function refusalText(error: unknown, fallback: string): Promise<string> {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  let body: unknown = data
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      body = JSON.parse(await data.text())
    } catch {
      return fallback
    }
  }
  const detail = (body as { detail?: unknown } | null | undefined)?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string' ? (item as { msg: string }).msg : ''))
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}
