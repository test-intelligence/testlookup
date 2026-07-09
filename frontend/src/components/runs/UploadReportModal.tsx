import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  Loader2,
  UploadCloud,
  X,
} from 'lucide-react'
import { clsx } from 'clsx'
import { zip as zipAsync, zipSync } from 'fflate'
import toast from 'react-hot-toast'
import {
  MAX_UPLOAD_BYTES,
  SUPPORTED_FORMATS,
  defaultBuildLabel,
  reportUploadService,
  type ReportFormat,
  type UploadStatus,
} from '@/services/reportUploadService'

interface UploadReportModalProps {
  /** Project the run will be ingested into (a concrete UUID, never "all"). */
  projectId: string
  onClose: () => void
  /** Called with the new run_id once the upload is accepted (202). */
  onSuccess: (runId: string) => void
}

type Phase = 'idle' | 'uploading' | 'processing' | 'success' | 'error'

const POLL_INTERVAL_MS = 1500
const MAX_POLLS = 40 // ~60s, then fall back to "still processing"

// Backend accepts XML (JUnit/TestNG/Robot/NUnit/xUnit) incl. .trx, JSON
// (Allure/Playwright/Cypress/Cucumber), or a .zip (an Allure results dir, or
// several reports zipped together — MRU-12).
const ACCEPT = '.xml,.json,.zip,.trx'

function isAcceptedFile(name: string): boolean {
  const lower = name.toLowerCase()
  return lower.endsWith('.xml') || lower.endsWith('.json') || lower.endsWith('.zip') || lower.endsWith('.trx')
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// Bundle files into a zip off the main thread (fflate's async zip uses a Web
// Worker). Falls back to the synchronous zip where Worker isn't available
// (e.g. jsdom in tests).
async function bundleZip(entries: Record<string, Uint8Array>): Promise<Uint8Array> {
  try {
    return await new Promise<Uint8Array>((resolve, reject) =>
      zipAsync(entries, (err, data) => (err ? reject(err) : resolve(data))),
    )
  } catch {
    return zipSync(entries)
  }
}

export default function UploadReportModal({
  projectId,
  onClose,
  onSuccess,
}: UploadReportModalProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [format, setFormat] = useState<ReportFormat>('auto')
  const [buildNumber, setBuildNumber] = useState('')
  const [branch, setBranch] = useState('')
  const [commitHash, setCommitHash] = useState('')
  const [releaseName, setReleaseName] = useState('')
  const [runAi, setRunAi] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [dragActive, setDragActive] = useState(false)

  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [errorMsg, setErrorMsg] = useState('')
  const [newRunId, setNewRunId] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [result, setResult] = useState<UploadStatus['result']>(null)

  const busy = phase === 'uploading' || phase === 'processing'

  // Poll the async parse/ingest status once the upload is accepted, so the user
  // sees a real outcome (parsed N tests / parse error) instead of a silent run.
  useEffect(() => {
    if (phase !== 'processing' || !taskId) return
    let active = true
    let attempts = 0
    let timer: ReturnType<typeof setTimeout>

    const tick = async () => {
      if (!active) return
      attempts += 1
      try {
        const st = await reportUploadService.getStatus(taskId)
        if (!active) return
        if (st.state === 'succeeded') {
          setResult(st.result ?? null)
          setPhase('success')
          toast.success('Report processed')
          return
        }
        if (st.state === 'failed') {
          setErrorMsg(st.error?.message || 'The report could not be processed.')
          setPhase('error')
          return
        }
      } catch (e) {
        // A 403 means access was lost (e.g. JWT expired mid-poll) — stop and
        // surface it rather than masking it as success. A 404 (record not yet
        // written / expired) or transient/5xx blip → keep polling to MAX_POLLS.
        const code = (e as { response?: { status?: number } })?.response?.status
        if (active && code === 403) {
          setErrorMsg('You no longer have access to this upload.')
          setPhase('error')
          return
        }
      }
      if (active && attempts < MAX_POLLS) {
        timer = setTimeout(tick, POLL_INTERVAL_MS)
      } else if (active) {
        // Took too long to confirm — the run is still processing server-side.
        // Render as a neutral "still processing" state (see body), not success.
        setResult(null)
        setPhase('success')
      }
    }
    timer = setTimeout(tick, POLL_INTERVAL_MS)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [phase, taskId])

  const rejectFiles = (msg: string) => {
    // Clear any prior selection so Upload disables and the user can't submit a
    // stale file while an error about a new one is shown.
    setFiles([])
    if (fileInputRef.current) fileInputRef.current.value = ''
    setErrorMsg(msg)
    setPhase('error')
  }

  const pickFiles = useCallback((picked: File[]) => {
    if (!picked.length) return
    const bad = picked.find((f) => !isAcceptedFile(f.name))
    if (bad) {
      rejectFiles(`Unsupported file type: ${bad.name}. Upload .xml (JUnit/TestNG/Robot/NUnit/xUnit), .trx, .json (Allure/Playwright/Cypress/Cucumber), or .zip files.`)
      return
    }
    // A .zip can only be uploaded on its own — bundling it with others would
    // nest a zip inside the client-side bundle, which the backend rejects.
    if (picked.length > 1 && picked.some((f) => f.name.toLowerCase().endsWith('.zip'))) {
      rejectFiles('Upload a .zip archive on its own, or select multiple .xml/.json files (not both).')
      return
    }
    // Each single file must fit the upload cap; the multi-file BUNDLE is checked
    // post-zip in handleSubmit (zip may compress well below the raw sum). The
    // raw-sum bound here is only an in-browser memory guard.
    const oversize = picked.find((f) => f.size > MAX_UPLOAD_BYTES)
    if (oversize) {
      rejectFiles(`${oversize.name} is ${humanSize(oversize.size)} — the limit is ${humanSize(MAX_UPLOAD_BYTES)}.`)
      return
    }
    const total = picked.reduce((n, f) => n + f.size, 0)
    if (total > MAX_UPLOAD_BYTES * 4) {
      rejectFiles(`Selection is too large to bundle in the browser (${humanSize(total)}).`)
      return
    }
    setFiles(picked)
    setErrorMsg('')
    setPhase('idle')
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragActive(false)
      if (busy) return
      pickFiles(Array.from(e.dataTransfer.files ?? []))
    },
    [busy, pickFiles],
  )

  const handleSubmit = useCallback(async () => {
    if (!files.length || busy) return
    setPhase('uploading')
    setProgress(0)
    setErrorMsg('')
    try {
      // Multiple files → bundle into one .zip client-side; the backend's
      // archive path (tier-2) parses each entry. A single file uploads as-is.
      let upload: File
      if (files.length === 1) {
        upload = files[0]
      } else {
        const entries: Record<string, Uint8Array> = {}
        const seen = new Set<string>()
        for (const f of files) {
          let key = f.name
          for (let i = 2; seen.has(key); i++) key = `${i}-${f.name}` // de-dup names
          seen.add(key)
          entries[key] = new Uint8Array(await f.arrayBuffer())
        }
        upload = new File([await bundleZip(entries) as BlobPart], 'reports-bundle.zip', { type: 'application/zip' })
      }
      // The server caps the COMPRESSED upload; check the actual bundle size here
      // (the raw-sum pre-check can't predict the zip size).
      if (upload.size > MAX_UPLOAD_BYTES) {
        setErrorMsg(`The zipped bundle is ${humanSize(upload.size)} — the limit is ${humanSize(MAX_UPLOAD_BYTES)}.`)
        setPhase('error')
        return
      }
      const res = await reportUploadService.upload({
        projectId,
        file: upload,
        buildNumber,
        format,
        branch,
        commitHash,
        releaseName,
        runAi,
        onProgress: setProgress,
      })
      setNewRunId(res.run_id)
      setTaskId(res.task_id)
      // Move to processing; the poll effect resolves it to success/error.
      setPhase('processing')
    } catch (err: unknown) {
      // The axios interceptor already toasts 4xx/5xx, but surface a precise
      // inline message too (e.g. a 503 for a disabled Cypress/Playwright flag).
      const anyErr = err as { response?: { data?: { detail?: unknown } }; message?: string }
      // FastAPI 422 returns `detail` as an array of objects, not a string —
      // only use it when it's actually a string so we never render [object Object].
      const detail = anyErr?.response?.data?.detail
      setErrorMsg(
        (typeof detail === 'string' && detail) ||
          anyErr?.message ||
          'Upload failed. Check the file and try again.',
      )
      setPhase('error')
    }
  }, [files, busy, projectId, buildNumber, format, branch, commitHash, releaseName, runAi])

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
                JUnit · TestNG · Allure · Playwright · Cypress · Robot · Cucumber · NUnit · TRX · xUnit
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
          {phase === 'processing' ? (
            <div className="flex flex-col items-center text-center gap-3 py-6">
              <Loader2 className="h-8 w-8 text-[var(--color-text-muted)] animate-spin" />
              <div>
                <p className="text-sm font-medium text-[var(--color-text)]">Processing report…</p>
                <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm">
                  Parsing and ingesting the test results.
                </p>
              </div>
            </div>
          ) : phase === 'success' ? (
            <div className="flex flex-col items-center text-center gap-3 py-4">
              {result ? (
                <CheckCircle2 className="h-10 w-10 text-emerald-400" />
              ) : (
                <Clock className="h-10 w-10 text-[var(--color-text-muted)]" />
              )}
              <div>
                {result ? (
                  <>
                    <p className="text-sm font-medium text-[var(--color-text)]">
                      Processed {result.total ?? 0} test{(result.total ?? 0) === 1 ? '' : 's'}
                    </p>
                    <p className="text-xs text-[var(--color-text-muted)] mt-1">
                      {result.passed ?? 0} passed · {result.failed ?? 0} failed
                      {result.broken ? ` · ${result.broken} broken` : ''}
                      {result.skipped ? ` · ${result.skipped} skipped` : ''}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium text-[var(--color-text)]">Upload accepted</p>
                    <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm">
                      The report is still being processed in the background. The run will
                      populate on its detail page shortly.
                    </p>
                  </>
                )}
              </div>
            </div>
          ) : (
            <>
              {/* Dropzone */}
              <div
                role="button"
                tabIndex={0}
                onClick={() => !busy && fileInputRef.current?.click()}
                onKeyDown={(e) => {
                  if ((e.key === 'Enter' || e.key === ' ') && !busy) {
                    e.preventDefault() // Space would otherwise scroll the modal body
                    fileInputRef.current?.click()
                  }
                }}
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
                  multiple
                  className="hidden"
                  onChange={(e) => pickFiles(Array.from(e.target.files ?? []))}
                />
                {files.length === 1 ? (
                  <div className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                    <FileText className="h-4 w-4 text-[var(--color-text-muted)]" />
                    <span className="font-medium">{files[0].name}</span>
                    <span className="text-[var(--color-text-muted)]">({humanSize(files[0].size)})</span>
                  </div>
                ) : files.length > 1 ? (
                  <div className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                    <FileText className="h-4 w-4 text-[var(--color-text-muted)]" />
                    <span className="font-medium">{files.length} files selected</span>
                    <span className="text-[var(--color-text-muted)]">
                      ({humanSize(files.reduce((n, f) => n + f.size, 0))}, zipped on upload)
                    </span>
                  </div>
                ) : (
                  <>
                    <UploadCloud className="h-7 w-7 text-[var(--color-text-muted)]" />
                    <p className="text-sm text-[var(--color-text)]">Drop report file(s) or click to browse</p>
                    <p className="text-xs text-[var(--color-text-muted)]">.xml, .trx, .json, or .zip · multiple allowed · up to {humanSize(MAX_UPLOAD_BYTES)}</p>
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

              {/* Skip AI toggle (MRU-8) */}
              <label className="flex items-center gap-2 text-sm text-[var(--color-text)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={!runAi}
                  disabled={busy}
                  onChange={(e) => setRunAi(!e.target.checked)}
                  className="h-3.5 w-3.5 accent-[var(--color-accent)]"
                />
                Skip AI analysis
                <span className="text-xs text-[var(--color-text-muted)]">(faster — run it later from the run page)</span>
              </label>

              {/* Supported formats & how to export (MRU-16) */}
              <details className="text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer hover:text-[var(--color-text)] select-none">
                  Supported formats &amp; how to export
                </summary>
                <ul className="mt-2 space-y-1 pl-1 leading-relaxed">
                  <li><b>JUnit / TestNG</b> — <code>.xml</code> (Maven Surefire, Gradle, pytest <code>--junitxml</code>, TestNG <code>testng-results.xml</code>)</li>
                  <li><b>Allure</b> — a single <code>*-result.json</code>, or a <code>.zip</code> of the <code>allure-results/</code> folder</li>
                  <li><b>Playwright</b> — <code>--reporter=json</code> output</li>
                  <li><b>Cypress</b> — Mochawesome merged <code>.json</code></li>
                  <li><b>pytest</b> — <code>--junitxml</code> (XML), or <code>pytest --json-report</code> (JSON)</li>
                  <li><b>Robot Framework</b> — the <code>output.xml</code> result file</li>
                  <li><b>Cucumber</b> — <code>--format json</code> output (cucumber-jvm/js, behave, SpecFlow)</li>
                  <li><b>NUnit</b> — <code>nunit3-console</code> result XML, or <code>dotnet test --logger:nunit</code></li>
                  <li><b>TRX</b> — <code>dotnet test --logger trx</code> (MSTest / vstest <code>.trx</code>)</li>
                  <li><b>xUnit.net</b> — <code>xunit.runner</code> XML, or <code>dotnet test --logger:xunit</code></li>
                  <li><b>Multiple files</b> — select several (zipped automatically) or upload one <code>.zip</code></li>
                </ul>
              </details>

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
          ) : phase === 'processing' ? (
            // Run continues server-side if the user closes mid-processing.
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              Close
            </button>
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
                disabled={!files.length || busy}
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
