import { useState } from 'react'
import { Archive, Download, FileCheck, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useCompliancePacks } from '@/hooks/useCompliancePacks'
import { compliancePackService } from '@/services/compliancePackService'
import { usePermissions } from '@/hooks/usePermissions'

interface Props {
  releaseId: string
  releaseName?: string | null
}

/**
 * Release compliance export pack panel — Tier 1 item 4.
 *
 * QA Lead + generates a signed ZIP that reconstructs the full release
 * decision for regulated customers. The panel shows:
 *
 * * A generate button that triggers ``POST /releases/{id}/compliance-pack``.
 * * A history list of every pack ever generated for this release, with
 *   SHA-256 of the manifest + download button.
 *
 * Non-QA_LEAD users see the history but not the generate button.
 */
export default function CompliancePackPanel({ releaseId, releaseName }: Props) {
  const { canAccessManagement: canGenerate } = usePermissions()
  const { packs, isLoading, isError, refresh } = useCompliancePacks(releaseId)
  const [generating, setGenerating] = useState(false)
  const [notes, setNotes] = useState('')

  async function handleGenerate() {
    if (generating) return
    setGenerating(true)
    try {
      const pack = await compliancePackService.generate(releaseId, {
        notes: notes || undefined,
      })
      await refresh()
      setNotes('')
      toast.success(
        `Compliance pack generated (${(pack.bytes / 1024).toFixed(1)} KB)`,
      )
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      toast.error(msg || `Failed to generate: ${(err as Error).message}`)
    } finally {
      setGenerating(false)
    }
  }

  async function handleDownload(packId: string, generatedAt: string) {
    try {
      const fname = `testlookup-compliance-${releaseName || releaseId}-${generatedAt.slice(0, 10)}.zip`
      await compliancePackService.download(packId, fname)
    } catch (err) {
      toast.error(`Download failed: ${(err as Error).message}`)
    }
  }

  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 space-y-3">
      <header className="flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-[var(--color-accent)]" />
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Compliance Pack
        </h3>
        <span className="ml-auto text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
          Audit ZIP · SHA-256 manifest
        </span>
      </header>

      <p className="text-xs text-[var(--color-text-muted)]">
        Generate a signed ZIP that reconstructs the full release decision
        (run snapshot, release gate policy, AI decision trail, clusters,
        defects, and audit events). Each pack is retained for seven years
        and has a SHA-256 manifest for tamper detection.
      </p>

      {canGenerate && (
        <div className="flex items-start gap-2">
          <input
            type="text"
            placeholder="Optional notes for the audit trail…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={generating}
            className="flex-1 px-2 py-1.5 text-xs bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
          <button
            type="button"
            onClick={handleGenerate}
            disabled={generating}
            className="btn-primary text-xs flex items-center gap-1.5 whitespace-nowrap"
          >
            <Archive className="h-3 w-3" />
            {generating ? 'Generating…' : 'Generate pack'}
          </button>
        </div>
      )}

      {isLoading && <LoadingSpinner />}
      {isError && (
        <p className="text-xs text-[var(--status-failed)]">Failed to load compliance pack history.</p>
      )}

      {!isLoading && !isError && packs.length === 0 && (
        <p className="text-xs text-[var(--color-text-faint)]">
          No compliance packs generated yet for this release.
        </p>
      )}

      {packs.length > 0 && (
        <ul className="space-y-1.5">
          {packs.map((pack) => (
            <li
              key={pack.id}
              className="flex items-center gap-2 text-xs px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)]"
            >
              <FileCheck className="h-3 w-3 text-[var(--status-passed)] shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[var(--color-text)]">
                    {new Date(pack.generated_at).toLocaleString()}
                  </span>
                  <span className="text-[var(--color-text-faint)]">
                    {(pack.bytes / 1024).toFixed(1)} KB · {pack.file_count} files
                  </span>
                </div>
                <div
                  className="font-mono text-[10px] text-[var(--color-text-muted)] truncate"
                  title={pack.manifest_sha256}
                >
                  sha256: {pack.manifest_sha256}
                </div>
                {pack.notes && (
                  <div className="text-[var(--color-text-muted)] italic truncate">
                    {pack.notes}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => handleDownload(pack.id, pack.generated_at)}
                className="text-[var(--color-accent)] hover:underline flex items-center gap-0.5 shrink-0"
              >
                <Download className="h-3 w-3" /> ZIP
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
