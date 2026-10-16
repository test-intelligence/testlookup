import { useState } from 'react'
import toast from 'react-hot-toast'

import { useSourceRoots } from '@/hooks/useSourceRoots'
import { extractErrorMessage } from '@/services/apiErrors'
import type { SourceRoot, SourceRootsPreview } from '@/types/sourceRoots'

/**
 * FAIL-08 "Java source roots" on the Ownership page.
 *
 * A JVM frame names a class and a file (`at com.acme.pay.CheckoutTest.pays(
 * CheckoutTest.java:42)`), never a path, so a CODEOWNERS / path rule could not
 * own a Java failure. A QA lead lists where the project's test sources live —
 * an optional dotted package prefix and a repo-relative root, in priority
 * order — tests the edited list on the project's recent failures ("12 of 15
 * recent Java failures map to a path; 9 match a path rule"), and saves. From
 * then on every locator (the coverage badge, the ingest-time assignment, the
 * Inbox "via CODEOWNERS" reason, the GitHub check annotations) places Java
 * frames through these roots. No default root: an empty list is exactly the
 * old behaviour.
 *
 * Everything from the server (roots, test names, paths) renders as React text.
 * Non-QA-leads see the table read-only (the PUT is QA_LEAD+ server-side).
 */
export default function SourceRootsSection({
  projectId,
  canEdit,
  onSaved,
}: {
  projectId: string
  canEdit: boolean
  /** Called after a successful save (the page refreshes its coverage badge). */
  onSaved?: () => void
}) {
  const { roots, maxRoots, isLoading, isError, save, preview } = useSourceRoots(projectId)
  // null = showing the saved list; an array = the reader's unsaved edit.
  const [edits, setEdits] = useState<SourceRoot[] | null>(null)
  const [prefix, setPrefix] = useState('')
  const [root, setRoot] = useState('')
  const [previewResult, setPreviewResult] = useState<SourceRootsPreview | null>(null)
  const [busy, setBusy] = useState<'preview' | 'save' | null>(null)

  const list = edits ?? roots
  const dirty = edits !== null
  const atCap = list.length >= maxRoots

  const change = (next: SourceRoot[]) => {
    setEdits(next)
    setPreviewResult(null) // a preview of another list would mislead
  }

  const add = () => {
    const nextRoot = root.trim()
    if (!nextRoot || atCap) return
    change([...list, { prefix: prefix.trim(), root: nextRoot }])
    setPrefix('')
    setRoot('')
  }

  const remove = (index: number) => change(list.filter((_, i) => i !== index))

  const failure = (err: unknown, fallback: string) => {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    toast.error(extractErrorMessage(detail, fallback))
  }

  const runPreview = async () => {
    setBusy('preview')
    try {
      setPreviewResult(await preview(list))
    } catch (err) {
      setPreviewResult(null)
      failure(err, 'Could not test the source roots')
    } finally {
      setBusy(null)
    }
  }

  const runSave = async () => {
    setBusy('save')
    try {
      await save(list)
      setEdits(null)
      toast.success('Java source roots saved')
      onSaved?.()
    } catch (err) {
      failure(err, 'Could not save the source roots')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section
      aria-label="Java source roots"
      data-source-roots=""
      className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-3"
    >
      <div>
        <h2 className="text-sm font-semibold text-[var(--color-text)] m-0">Java source roots</h2>
        <p className="text-xs text-[var(--color-text-muted)] mt-1 mb-0">
          Java stack traces name a class and a file, not a path. Tell TestLookup where your test sources live and Java
          failures are routed by path like any other.
        </p>
      </div>

      {isError && (
        <p className="text-xs text-[var(--status-failed)] m-0">Failed to load the source roots</p>
      )}

      {isLoading ? (
        <p className="text-xs text-[var(--color-text-muted)] m-0">Loading…</p>
      ) : (
        <table className="w-full text-xs" data-source-roots-table="">
          <thead>
            <tr className="text-left text-[var(--color-text-muted)]">
              <th className="font-medium py-1 pr-3 w-[40%]">Package prefix</th>
              <th className="font-medium py-1 pr-3">Source root</th>
              {canEdit && <th className="py-1 w-[80px]"><span className="sr-only">Remove</span></th>}
            </tr>
          </thead>
          <tbody>
            {list.length === 0 ? (
              <tr>
                <td colSpan={canEdit ? 3 : 2} className="py-1.5 text-[var(--color-text-muted)]">
                  No source roots: Java failures are not located.
                </td>
              </tr>
            ) : (
              list.map((entry, index) => (
                <tr key={`${entry.prefix}|${entry.root}|${index}`} data-source-root-row="" className="border-t border-[var(--color-border)]">
                  <td className="py-1.5 pr-3 font-mono text-[var(--color-text)] break-all">
                    {entry.prefix ? entry.prefix : <span className="font-sans text-[var(--color-text-muted)]">any package</span>}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[var(--color-text)] break-all">{entry.root}</td>
                  {canEdit && (
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        onClick={() => remove(index)}
                        aria-label={`Remove source root ${entry.root}`}
                        className="px-2 py-0.5 rounded text-xs bg-[var(--status-failed-bg)]/30 text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/40"
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      )}

      {canEdit && (
        <>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs text-[var(--color-text-muted)]">
              Package prefix (optional)
              <input
                value={prefix}
                onChange={e => setPrefix(e.target.value)}
                placeholder="com.acme.pay"
                maxLength={255}
                className="w-[200px] bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-2.5 py-1.5 text-xs font-mono"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--color-text-muted)]">
              Source root
              <input
                value={root}
                onChange={e => setRoot(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
                placeholder="src/test/java"
                maxLength={500}
                className="w-[260px] bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-2.5 py-1.5 text-xs font-mono"
              />
            </label>
            <button
              type="button"
              onClick={add}
              disabled={!root.trim() || atCap}
              title={atCap ? `At most ${maxRoots} source roots.` : undefined}
              className="px-3 py-1.5 rounded text-xs border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
            >
              Add
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={runPreview}
              disabled={busy !== null}
              className="px-3 py-1.5 rounded text-xs border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
            >
              {busy === 'preview' ? 'Testing…' : 'Test on recent failures'}
            </button>
            <button
              type="button"
              onClick={runSave}
              disabled={!dirty || busy !== null}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50"
            >
              {busy === 'save' ? 'Saving…' : 'Save'}
            </button>
            {dirty && <span className="text-[11px] text-[var(--color-text-muted)]">Unsaved changes</span>}
          </div>

          {previewResult && (
            <div data-source-roots-preview="" className="text-xs text-[var(--color-text-secondary)] space-y-1">
              <p className="m-0">
                {previewResult.java_frames === 0
                  ? 'No recent failure has a Java stack trace.'
                  : `${previewResult.located} of ${previewResult.java_frames} recent Java failures map to a path; ${previewResult.matched} match a path rule (last ${previewResult.lookback_days} days).`}
              </p>
              {previewResult.examples.length > 0 && (
                <ul className="m-0 pl-4 list-disc text-[11px] text-[var(--color-text-muted)]">
                  {previewResult.examples.map((ex, i) => (
                    <li key={`${ex.path}|${i}`} className="font-mono break-all">{`${ex.test_name} → ${ex.path}`}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
