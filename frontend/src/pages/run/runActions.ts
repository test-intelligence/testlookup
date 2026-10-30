/**
 * The run's export actions, shared by the Run page's header menu and the
 * standalone Run Intelligence page (UX redesign P4). Each lazy-loads the
 * export service and says so when it fails; nothing else on failure.
 */
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import { readReportVersion as readReportVersionParam } from '@/components/ai/aiHistoryUrl'

export const PDF_EXPORT_FAILED = 'PDF export failed'
/** `aiHistoryExportErrorMessage`'s own fallback: the error carried no words of the server's. */
const AI_HISTORY_FALLBACK = 'AI history export failed'

/**
 * The executive PDF of the run's AI analysis. A refusal toasts ONCE, in the server's words: the request is
 * `suppressToast` (the interceptor would say "Request failed with status code 409" — it cannot read a Blob body)
 * and the 409 `report_pending_review` body is read by the existing `aiHistoryExportErrorMessage`, imported
 * DYNAMICALLY (W23c: a static import would break the six `vi.mock('@/services/aiHistoryService', …)` factories).
 */
export async function downloadRunPdf(runId: string): Promise<void> {
  try {
    const { downloadPdf } = await import('@/services/reportExportService')
    await downloadPdf(runId, 'executive')
  } catch (error) {
    toast.error(await pdfExportErrorMessage(error))
  }
}

async function pdfExportErrorMessage(error: unknown): Promise<string> {
  if (!isAxiosError(error)) return PDF_EXPORT_FAILED
  try {
    const { aiHistoryExportErrorMessage } = await import('@/services/aiHistoryService')
    const message = await aiHistoryExportErrorMessage(error)
    return message && message !== AI_HISTORY_FALLBACK ? message : PDF_EXPORT_FAILED
  } catch {
    return PDF_EXPORT_FAILED
  }
}

/** The run's evidence bundle (zip). */
export async function downloadRunEvidenceBundle(runId: string): Promise<void> {
  try {
    const { downloadEvidenceBundle } = await import('@/services/reportExportService')
    await downloadEvidenceBundle(runId)
  } catch {
    toast.error('Bundle export failed')
  }
}

/**
 * W03 AIH-06: every stored AI version of the run, the diffs between consecutive versions and the per-test
 * versions, as `run-<build>-ai-history.json`. Fetched through the authed client as a blob (never a bare link,
 * HON-04); the service toasts a failure itself, with the API's words (a 409: the report awaits human review).
 */
export async function downloadAIHistory(runId: string, buildNumber?: string | number | null): Promise<void> {
  try {
    const { aiHistoryService } = await import('@/services/aiHistoryService')
    await aiHistoryService.downloadExport(runId, buildNumber ?? null)
  } catch {
    toast.error('AI history export failed')
  }
}

/** The `?report_version=` a link or the version picker chose; null = the latest report. */
export function readReportVersion(params: URLSearchParams): number | null {
  return readReportVersionParam(params)
}
