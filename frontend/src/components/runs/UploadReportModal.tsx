import { useCallback, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  UploadCloud,
  X,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import {
  MAX_UPLOAD_BYTES,
  SUPPORTED_FORMATS,
  defaultBuildLabel,
  reportUploadService,
  type ReportFormat,
} from '@/services/reportUploadService'

interface UploadReportModalProps {
  /** Project the run will be ingested into (a concrete UUID, never "all"). */
  projectId: string
  onClose: () => void
  /** Called with the new run_id once the upload is accepted (202). */
  onSuccess: (runId: string) => void
}

type Phase = 'idle' | 'uploading' | 'success' | 'error'

// Backend accepts XML (JUnit/TestNG) and JSON (Allure/Playwright/Cypress) as a
// single file. Allure zip/dir + multi-file are a later slice (PRD MRU-12/13).
const ACCEPT = '.xml,.json'

function isAcceptedFile(name: string): boolean {
  const lower = name.toLowerCase()
  return lower.endsWith('.xml') || lower.endsWith('.json')
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function UploadReportModal({
  projectId,
  onClose,
  onSuccess,
}: UploadReportModalProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [format, setFormat] = useState<ReportFormat>('auto')
  const [buildNumber, setBuildNumber] = useState('')
  const [branch, setBranch] = useState('')
  const [commitHash, setCommitHash] = useState('')
  const [releaseName, setReleaseName] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [dragActive, setDragActive] = useState(false)

  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [errorMsg, setErrorMsg] = useState('')
  const [newRunId, setNewRunId] = useState<string | null>(null)

  const busy = phase === 'uploading'

  const pickFile = useCallback((f: File | null) => {
    if (!f) return
    if (!isAcceptedFile(f.name)) {
      setErrorMsg('Unsupported file type. Upload a JUnit/TestNG .xml or an Allure/Playwright/Cypress .json file.')
      setPhase('error')
      return
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      setErrorMsg(`File is ${humanSize(f.size)} — the limit is ${humanSize(MAX_UPLOAD_BYTES)}.`)
      setPhase('error')
      return
    }
    setFile(f)
    setErrorMsg('')
    setPhase('idle')
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragActive(false)
      if (busy) return
      pickFile(e.dataTransfer.files?.[0] ?? null)
    },
    [busy, pickFile],
  )

  const handleSubmit = useCallback(async () => {
    if (!file || busy) return
    setPhase('uploading')
    setProgress(0)
    setErrorMsg('')
    try {
      const res = await reportUploadService.upload({
        projectId,
        file,
        buildNumber,
        format,
        branch,
        commitHash,
        releaseName,
        onProgress: setProgress,
      })
      setNewRunId(res.run_id)
      setPhase('success')
      toast.success('Report uploaded — processing the run')
    } catch (err: unknown) {
      // The axios interceptor already toasts 4xx/5xx, but surface a precise
      // inline message too (e.g. a 503 for a disabled Cypress/Playwright flag).
      const anyErr = err as { response?: { data?: { detail?: string } }; message?: string }
      setErrorMsg(
        anyErr?.response?.data?.detail ||
          anyErr?.message ||
          'Upload failed. Check the file and try again.',
      )
      setPhase('error')
    }
  }, [file, busy, projectId, buildNumber, format, branch, commitHash, releaseName])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60 backdrop-blur-sm">
      <div className="relative w-full max-w-xl mx-4 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-2xl shadow-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-3">
            <UploadCloud className="h-5 w-5 text-[var(--color-text)]" />
            <div>
              <h2 className="text-base font-semibold text-[var(--color-text)]">Upload test report</h2>
              <p className="text-xs text-[var(--color-text-muted)]">
                JUnit · TestNG · Allure · Playwright · Cypress
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4 overflow-y-auto space-y-4">
          {phase === 'success' ? (
            <div className="flex flex-col items-center text-center gap-3 py-4">
              <CheckCircle2 className="h-10 w-10 text-emerald-400" />
              <div>
                <p className="text-sm font-medium text-[var(--color-text)]">Upload accepted</p>
                <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm">
                  The report is being parsed in the background. The run will populate
                  on its detail page within a few moments.
                </p>
              </div>
            </div>
          ) : (
            <>
              {/* Dropzone */}
              <div
                role="button"
                tabIndex={0}
                onClick={() => !busy && fileInputRef.current?.click()}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && !busy && fileInputRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); if (!busy) setDragActive(true) }}
                onDragLeave={() => setDragActive(false)}
                onDrop={onDrop}
                className={clsx(
                  'flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 cursor-pointer transition-colors',
                  dragActive
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5'
                    : 'border-[var(--color-border)] hover:border-[var(--color-text-muted)]',
                  busy && 'opacity-60 cursor-not-allowed',
                )}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPT}
                  className="hidden"
                  onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
                />
                {file ? (
                  <div className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                    <FileText className="h-4 w-4 text-[var(--color-text-muted)]" />
                    <span className="font-medium">{file.name}</span>
                    <span className="text-[var(--color-text-muted)]">({humanSize(file.size)})</span>
                  </div>
                ) : (
                  <>
                    <UploadCloud className="h-7 w-7 text-[var(--color-text-muted)]" />
                    <p className="text-sm text-[var(--color-text)]">Drop a report file or click to browse</p>
                    <p className="text-xs text-[var(--color-text-muted)]">.xml or .json · up to {humanSize(MAX_UPLOAD_BYTES)}</p>
                  </>
                )}
              </div>

              {/* Format */}
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">Format</label>
                <select
                  value={format}
                  disabled={busy}
                  onChange={(e) => setFormat(e.target.value as ReportFormat)}
                  className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:border-neutral-500 outline-none"
                >
                  {SUPPORTED_FORMATS.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </select>
              </div>

              {/* Advanced metadata */}
              <button
                type="button"
                onClick={() => setShowAdvanced((v) => !v)}
                className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                {showAdvanced ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                Optional metadata
              </button>
              {showAdvanced && (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Build label" placeholder={defaultBuildLabel()} value={buildNumber} onChange={setBuildNumber} disabled={busy} />
                  <Field label="Release" placeholder="e.g. v2.1.0" value={releaseName} onChange={setReleaseName} disabled={busy} />
                  <Field label="Branch" placeholder="e.g. main" value={branch} onChange={setBranch} disabled={busy} />
                  <Field label="Commit" placeholder="sha" value={commitHash} onChange={setCommitHash} disabled={busy} />
                </div>
              )}

              {/* Progress */}
              {busy && (
                <div className="space-y-1">
                  <div className="h-1.5 w-full bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
                    <div className="h-full bg-[var(--color-accent)] transition-all" style={{ width: `${progress}%` }} />
                  </div>
                  <p className="text-xs text-[var(--color-text-muted)] text-right">{progress}%</p>
                </div>
              )}

              {/* Error */}
              {phase === 'error' && errorMsg && (
                <div className="flex items-start gap-2 text-xs text-red-300 bg-red-900/20 border border-red-700/40 rounded-lg px-3 py-2">
                  <AlertTriangle className="h-4 w-4 shrink-0 mt-px" />
                  <span>{errorMsg}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-[var(--color-border)] shrink-0">
          {phase === 'success' ? (
            <>
              <button
                onClick={onClose}
                className="px-4 py-2 rounded-lg text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              >
                Close
              </button>
              <button
                onClick={() => newRunId && onSuccess(newRunId)}
                className="px-5 py-2 rounded-lg text-sm font-medium text-white bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)]"
              >
                View run
              </button>
            </>
          ) : (
            <>
              <button
                onClick={onClose}
                disabled={busy}
                className="px-4 py-2 rounded-lg text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={!file || busy}
                className="inline-flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-medium text-white bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                {busy ? 'Uploading…' : 'Upload'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Field({
  label,
  placeholder,
  value,
  onChange,
  disabled,
}: {
  label: string
  placeholder?: string
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">{label}</label>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:border-neutral-500 outline-none disabled:opacity-50"
      />
    </div>
  )
}
