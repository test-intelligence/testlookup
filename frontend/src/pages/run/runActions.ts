/**
 * The run's export actions, shared by the Run page's header menu and the
 * standalone Run Intelligence page (UX redesign P4). Each lazy-loads the
 * export service and says so when it fails; nothing else on failure.
 */
import toast from 'react-hot-toast'

/** The executive PDF of the run's AI analysis. */
export async function downloadRunPdf(runId: string): Promise<void> {
  try {
    const { downloadPdf } = await import('@/services/reportExportService')
    await downloadPdf(runId, 'executive')
  } catch {
    toast.error('PDF export failed')
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

/** The `?report_version=` a link or the version picker chose; null = the latest report. */
export function readReportVersion(params: URLSearchParams): number | null {
  const requested = Number(params.get('report_version'))
  return Number.isInteger(requested) && requested > 0 ? requested : null
}
