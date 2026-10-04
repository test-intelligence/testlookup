/**
 * VIZ-607: the pure part of the background-exports panel — what each export
 * says, and which ones just finished (the moment the reader is told).
 */
import type { ReportExport } from '@/services/summaryReportService'

/** Still working: the list keeps polling while any export is. */
export function isActive(exp: Pick<ReportExport, 'status'>): boolean {
  return exp.status === 'queued' || exp.status === 'running'
}

export const STATUS_LABEL: Record<ReportExport['status'], string> = {
  queued: 'Queued',
  running: 'Generating…',
  completed: 'Ready',
  failed: 'Failed',
}

export const FORMAT_LABEL: Record<ReportExport['format'], string> = {
  pdf: 'PDF',
  xlsx: 'Excel',
}

/** The exports that were not finished on the last poll and are now, one
 *  list per outcome. Nothing on the first load: those are not news. */
export function newlyFinished(
  previous: readonly ReportExport[] | undefined,
  next: readonly ReportExport[],
): { completed: ReportExport[]; failed: ReportExport[] } {
  if (!previous) return { completed: [], failed: [] }
  const before = new Map(previous.map(e => [e.id, e.status]))
  const changed = next.filter(e => {
    const was = before.get(e.id)
    return was !== undefined && isActive({ status: was }) && !isActive(e)
  })
  return {
    completed: changed.filter(e => e.status === 'completed'),
    failed: changed.filter(e => e.status === 'failed'),
  }
}

/** "PDF · last 30 days · latest run per suite · 2 suites" */
export function scopeSummary(exp: ReportExport): string {
  const p = exp.params ?? {}
  const parts = [FORMAT_LABEL[exp.format]]
  if (typeof p.days === 'number') parts.push(p.days === 1 ? 'last 24 h' : `last ${p.days} days`)
  parts.push(p.mode === 'latest' ? 'latest run per suite' : 'all runs')
  const releases = Array.isArray(p.release_ids) ? p.release_ids.length : 0
  const suites = Array.isArray(p.suite_names) ? p.suite_names.length : 0
  if (releases) parts.push(releases === 1 ? '1 release' : `${releases} releases`)
  if (suites) parts.push(suites === 1 ? '1 suite' : `${suites} suites`)
  return parts.join(' · ')
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
