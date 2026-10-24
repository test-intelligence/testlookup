/**
 * W11 REL-11: the pack panel's words for a pack's signature and a
 * verification, in one pure module so the tests import one string.
 *
 * Copy rule (HON-07, kept): the server says whether a pack is signed; the
 * panel never claims more. A pack whose `signing_key_id` field is ABSENT (an
 * answer from a server older than W11, or a hand-built fixture) gets no chip:
 * nothing was said, so nothing is shown. `null` is the server saying
 * "unsigned".
 */
import type { CompliancePackRead, CompliancePackVerification } from '@/services/compliancePackService'

export const UNSIGNED_CHIP = 'Unsigned (tamper-evident only)'
export const VERIFY_FAILED = 'Could not verify this pack.'
export const PUBLIC_KEY_FAILED = 'Could not download the public key.'

/** The non-ok statuses as the panel, the MCP tool and the CLI word them. */
export const STATUS_PHRASES: Record<string, string> = {
  mismatch: 'does not match the manifest',
  missing: 'is missing from the pack',
  unexpected: 'is not listed in the manifest',
}

export interface PackChip {
  text: string
  /** The full key id (or the unsigned explanation), for the native tooltip. */
  title: string
  signed: boolean
}

/** `Signed · k-1a2b3c4d` (title = the full id), `Unsigned (tamper-evident only)`, or null (not said). */
export function chipText(pack: Pick<CompliancePackRead, 'signing_key_id'>): PackChip | null {
  if (pack.signing_key_id === undefined) return null
  if (pack.signing_key_id === null || pack.signing_key_id === '') {
    return {
      text: UNSIGNED_CHIP,
      title: 'No signature: the pack predates signing, or the signing key could not be used. Its SHA-256 chain still shows tampering.',
      signed: false,
    }
  }
  return { text: `Signed · ${pack.signing_key_id.slice(0, 10)}`, title: pack.signing_key_id, signed: true }
}

export type ResultTone = 'success' | 'danger' | 'muted'

/** verified -> success ink, tampered -> danger ink, unreadable (or anything else) -> muted. */
export function resultTone(v: Pick<CompliancePackVerification, 'result'>): ResultTone {
  if (v.result === 'verified') return 'success'
  if (v.result === 'tampered') return 'danger'
  return 'muted'
}

/** Static classes per tone (tokens only: no `--status-*` read). */
export const RESULT_TONE_CLASS: Record<ResultTone, string> = {
  success: 'text-[var(--ui-success-ink)]',
  danger: 'text-[var(--ui-danger-ink)]',
  muted: 'text-[var(--color-text-muted)]',
}

/**
 * The lines under the summary: one per file that is not ok
 * (`decision.json — does not match the manifest`), else `12 of 12 files
 * match`. Nothing for an unreadable pack (no file was read).
 */
export function nonOkLines(v: Pick<CompliancePackVerification, 'result' | 'files' | 'files_ok' | 'files_checked'>): string[] {
  if (v.result === 'unreadable') return []
  const bad = (v.files ?? []).filter((f) => f.status !== 'ok')
  if (bad.length === 0) return [`${v.files_ok} of ${v.files_checked} files match`]
  return bad.map((f) => `${f.name} — ${STATUS_PHRASES[f.status] ?? 'was changed'}`)
}
