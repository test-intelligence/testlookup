import { useState } from 'react'
import { Archive, Download, FileCheck, KeyRound, ShieldCheck } from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useCompliancePacks } from '@/hooks/useCompliancePacks'
import { refreshProjectCompliancePacks } from '@/hooks/useProjectCompliancePacks'
import {
  compliancePackService,
  packSizeText,
  type CompliancePackVerification,
} from '@/services/compliancePackService'
import { usePermissions } from '@/hooks/usePermissions'
import { formatCompactDateTime } from '@/utils/formatters'
import PackLifecycleControls, { PackHoldChip } from './PackLifecycleControls'
import {
  chipText,
  nonOkLines,
  PUBLIC_KEY_FAILED,
  RESULT_TONE_CLASS,
  resultTone,
  VERIFY_FAILED,
} from './packVerificationModel'

interface Props {
  releaseId: string
  releaseName?: string | null
}

/** One row's last Verify answer, or why it could not be asked. */
type VerifyState = { verification: CompliancePackVerification } | { error: string }

/**
 * Release compliance export pack panel — Tier 1 item 4.
 *
 * QA Lead + generates a ZIP that reconstructs the full release decision for
 * regulated customers, sealed with a tamper-evident SHA-256 manifest. HON-07:
 * the panel's description still says "tamper-evident" only; whether ONE pack
 * is signed is the server's statement about that pack (W11 REL-11: packs are
 * signed with the deployment's Ed25519 key since then), shown on its row. The
 * panel shows:
 *
 * * A generate button that triggers ``POST /releases/{id}/compliance-pack``.
 * * A history list of every pack ever generated for this release, with
 *   SHA-256 of the manifest + download button; per row (W11) a signature chip,
 *   **Verify** (``POST /compliance-packs/{id}/verify``, its answer under the
 *   row as text) and, for a signed pack, **Public key** (the PEM download).
 *
 * Non-QA_LEAD users see the history but not the generate button; every role
 * that sees the panel may verify.
 */
export default function CompliancePackPanel({ releaseId, releaseName }: Props) {
  const { canAccessManagement: canGenerate } = usePermissions()
  const { packs, isLoading, isError, refresh } = useCompliancePacks(releaseId)
  const [generating, setGenerating] = useState(false)
  const [notes, setNotes] = useState('')
  const [verifying, setVerifying] = useState<string | null>(null)
  const [verified, setVerified] = useState<Record<string, VerifyState>>({})

  async function handleVerify(packId: string) {
    if (verifying) return
    setVerifying(packId)
    try {
      const verification = await compliancePackService.verify(packId)
      setVerified((prev) => ({ ...prev, [packId]: { verification } }))
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      setVerified((prev) => ({
        ...prev,
        [packId]: { error: typeof detail === 'string' && detail ? `${VERIFY_FAILED} ${detail}` : VERIFY_FAILED },
      }))
    } finally {
      setVerifying(null)
    }
  }

  async function handlePublicKey(keyId: string) {
    try {
      await compliancePackService.downloadPublicKey(keyId)
    } catch {
      toast.error(PUBLIC_KEY_FAILED)
    }
  }

  async function handleGenerate() {
    if (generating) return
    setGenerating(true)
    try {
      const pack = await compliancePackService.generate(releaseId, {
        notes: notes || undefined,
      })
      await refresh()
      // REL-04: the /releases rail lists the project's packs; it follows.
      void refreshProjectCompliancePacks()
      setNotes('')
      toast.success(
        `Compliance pack generated (${packSizeText(pack.bytes)})`,
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
        Generate a ZIP that reconstructs the full release decision (run
        snapshot, release gate policy, AI decision trail, clusters, defects,
        and audit events), sealed with a tamper-evident SHA-256 manifest. Each
        pack is retained for seven years.
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
        <p className="text-xs text-[var(--status-failed)]">Could not load compliance pack history.</p>
      )}

      {!isLoading && !isError && packs.length === 0 && (
        <p className="text-xs text-[var(--color-text-faint)]">
          No compliance packs generated yet for this release.
        </p>
      )}

      {packs.length > 0 && (
        <ul className="space-y-1.5">
          {packs.map((pack) => {
            const chip = chipText(pack)
            const state = verified[pack.id]
            return (
              <li
                key={pack.id}
                className="text-xs px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)]"
              >
                {/* Three lines, so a ~400 px panel never squeezes the chip or the date:
                    1. when · size · files (one unbroken line) and the signature chip in full (it wraps
                       to its own line rather than truncating), 2. the manifest digest (truncated, full
                       value in its title), 3. the actions (each unbroken; the row wraps). */}
                <div className="flex items-start gap-2">
                  <FileCheck className="h-3 w-3 mt-0.5 text-[var(--status-passed)] shrink-0" />
                  <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="inline-flex items-center gap-2 whitespace-nowrap">
                        <span className="text-[var(--color-text)]">
                          {formatCompactDateTime(pack.generated_at)}
                        </span>
                        <span className="text-[var(--color-text-faint)]">
                          {packSizeText(pack.bytes)} · {pack.file_count} files
                        </span>
                      </span>
                      {chip && (
                        <span
                          data-pack-signature={chip.signed ? 'signed' : 'unsigned'}
                          title={chip.title}
                          className={clsx(
                            'whitespace-nowrap rounded border px-1.5 text-xs',
                            chip.signed
                              ? 'border-[var(--ui-success-bd)] bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)]'
                              : 'border-[var(--color-border)] text-[var(--color-text-muted)]',
                          )}
                        >
                          {chip.text}
                        </span>
                      )}
                      <PackHoldChip pack={pack} />
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
                    <div className="flex flex-wrap items-center gap-2 pt-0.5">
                      <button
                        type="button"
                        onClick={() => handleVerify(pack.id)}
                        disabled={verifying !== null}
                        className="btn-secondary btn-sm whitespace-nowrap"
                      >
                        {verifying === pack.id ? 'Verifying…' : 'Verify'}
                      </button>
                      {chip?.signed && pack.signing_key_id && (
                        <button
                          type="button"
                          onClick={() => handlePublicKey(pack.signing_key_id as string)}
                          title={`Download the public key ${pack.signing_key_id} (PEM)`}
                          className="btn-secondary btn-sm whitespace-nowrap flex items-center gap-1"
                        >
                          <KeyRound className="h-3 w-3" /> Public key
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDownload(pack.id, pack.generated_at)}
                        className="text-[var(--color-accent)] hover:underline whitespace-nowrap flex items-center gap-0.5"
                      >
                        <Download className="h-3 w-3" /> ZIP
                      </button>
                      <PackLifecycleControls
                        pack={pack}
                        onChanged={() => {
                          void refresh()
                          void refreshProjectCompliancePacks()
                        }}
                      />
                    </div>
                  </div>
                </div>
                {state && (
                  <div data-pack-verification className="mt-1.5 pl-5 space-y-0.5">
                    {'verification' in state ? (
                      <>
                        <p className={clsx('text-xs font-medium', RESULT_TONE_CLASS[resultTone(state.verification)])}>
                          {state.verification.summary}
                        </p>
                        {nonOkLines(state.verification).map((line) => (
                          <p key={line} className="text-xs text-[var(--color-text-muted)] break-words">
                            {line}
                          </p>
                        ))}
                      </>
                    ) : (
                      <p className="text-xs text-[var(--ui-danger-ink)]">{state.error}</p>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
