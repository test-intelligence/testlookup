/**
 * VIZ-607: the reader's background exports of the Summary Report.
 *
 * A large report (or one the reader sends to the background) is rendered by a
 * worker. This panel lists the reader's exports that have not expired, polls
 * while any is queued or generating, and says so the moment one finishes: a
 * toast with a Download button when it is ready, the reason and Retry when it
 * failed. Files are downloaded through the API, which re-checks project access.
 */
import { useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import toast from 'react-hot-toast'
import { Download, RotateCcw } from 'lucide-react'
import { summaryReportService, type ReportExport } from '@/services/summaryReportService'
import { downloadBlob } from '@/utils/download'
import {
  STATUS_LABEL,
  formatBytes,
  isActive,
  newlyFinished,
  scopeSummary,
} from './reportExportsModel'

/** How often the list is re-read while an export is still working. */
export const EXPORT_POLL_MS = 3_000

const STATUS_COLOUR: Record<ReportExport['status'], string> = {
  queued: 'var(--color-text-muted)',
  running: 'var(--color-accent)',
  completed: 'var(--status-passed)',
  failed: 'var(--status-failed)',
}

export async function downloadReportExport(exp: ReportExport): Promise<void> {
  try {
    const blob = await summaryReportService.downloadExport(exp.id)
    downloadBlob(blob, exp.filename ?? `summary-export.${exp.format}`)
  } catch (err: unknown) {
    toast.error((err as Error).message || 'The export could not be downloaded')
  }
}

export interface ReportExportsPanelProps {
  projectId: string
  /** Bumped by the page when it queues an export, so the list re-reads at once. */
  refreshToken: number
}

export default function ReportExportsPanel({ projectId, refreshToken }: ReportExportsPanelProps) {
  const { data, mutate } = useSWR(
    ['summary-report-exports', projectId],
    () => summaryReportService.listExports(projectId),
    {
      refreshInterval: (latest?: ReportExport[]) => (latest?.some(isActive) ? EXPORT_POLL_MS : 0),
      revalidateOnFocus: false,
    },
  )
  const [retrying, setRetrying] = useState<string | null>(null)

  useEffect(() => {
    if (refreshToken > 0) void mutate()
  }, [refreshToken, mutate])

  // Tell the reader when an export they are waiting for finishes.
  const previous = useRef<ReportExport[] | undefined>(undefined)
  useEffect(() => {
    if (!data) return
    const { completed, failed } = newlyFinished(previous.current, data)
    previous.current = data
    for (const exp of completed) {
      toast.success(
        (t) => (
          <span className="inline-flex items-center gap-2">
            Your {scopeSummary(exp)} export is ready.
            <button
              type="button"
              className="underline font-medium"
              onClick={() => {
                toast.dismiss(t.id)
                void downloadReportExport(exp)
              }}
            >
              Download
            </button>
          </span>
        ),
        { id: `report-export-${exp.id}`, duration: 15_000 },
      )
    }
    for (const exp of failed) {
      toast.error(`Your ${scopeSummary(exp)} export failed: ${exp.error ?? 'no reason was recorded'}`, {
        id: `report-export-${exp.id}`,
      })
    }
  }, [data])

  const handleRetry = async (exp: ReportExport) => {
    setRetrying(exp.id)
    try {
      await summaryReportService.retryExport(exp.id)
      await mutate()
    } catch (err: unknown) {
      toast.error((err as Error).message || 'The export could not be retried')
    } finally {
      setRetrying(null)
    }
  }

  if (!data || data.length === 0) return null

  return (
    <section
      data-report-exports=""
      className="mb-4 rounded-lg border px-4 py-3"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
    >
      <h2 className="text-[13px] font-semibold mb-2 text-[var(--color-text)]">Background exports</h2>
      <ul className="divide-y" style={{ borderColor: 'var(--color-border)' }}>
        {data.map(exp => (
          <li key={exp.id} data-export-status={exp.status} className="flex items-center gap-3 py-1.5 text-[12.5px]">
            <span className="font-medium min-w-[5.5rem]" style={{ color: STATUS_COLOUR[exp.status] }}>
              {STATUS_LABEL[exp.status]}
            </span>
            <span className="flex-1 text-[var(--color-text)]">
              {scopeSummary(exp)}
              {exp.status === 'failed' && exp.error ? (
                <span className="block text-[11.5px] text-[var(--color-text-muted)]">{exp.error}</span>
              ) : null}
            </span>
            <span className="text-[11.5px] text-[var(--color-text-muted)] tabular-nums">
              {new Date(exp.requested_at).toLocaleString()}
              {exp.size_bytes !== null ? ` · ${formatBytes(exp.size_bytes)}` : ''}
            </span>
            {exp.status === 'completed' ? (
              <button
                type="button"
                onClick={() => void downloadReportExport(exp)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[12px]"
                style={{ borderColor: 'var(--color-border)', color: 'var(--color-accent)' }}
              >
                <Download className="h-3.5 w-3.5" />
                Download
              </button>
            ) : exp.retryable ? (
              <button
                type="button"
                onClick={() => void handleRetry(exp)}
                disabled={retrying === exp.id}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[12px] disabled:opacity-50"
                style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-muted)' }}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Retry
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
