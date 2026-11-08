/**
 * W31 STRAT-12: the pure model of Settings › Users › Personal data — the row action, the erased row, and the
 * in-row Erase panel (preview first, blockers, the typed confirm, the phase line).
 *
 * Every sentence lives here (or verbatim in `@/types/subjectRights`), so a test imports the one string it asserts.
 * Rules (contract §2.3, §4.1):
 *  - the confirm enables ONLY on the exact, case-sensitive username (whitespace counts) and only with no blocker;
 *  - a count the server could not take (`measured: false`) reads "not counted (took too long)", never 0;
 *  - a job at `lockout` reads "Erasure in progress", never "Erased" — "Erased" means `users.erased_at` is set
 *    (E-11b), and the done line appears only at `phase === 'done'`;
 *  - service / synthetic accounts and the admin's OWN row get no Personal data action (refusals key on the flags).
 * Names, sentences and counts are React text.
 */
import { countNoun } from '@/pages/settings/projectDataModel'
import {
  ERASED_DISPLAY,
  ERROR_SENTENCES,
  fillSentence,
  isActiveJob,
  UI_SENTENCES,
  type ErasureBlocker,
  type ErasureLocationCount,
  type ErasurePreview,
  type SubjectJob,
} from '@/types/subjectRights'
import { formatDay, formatNumber } from '@/utils/formatters'

// ── Copy ─────────────────────────────────────────────────────────────────────

export const PERSONAL_DATA = 'Personal data'
/** The row icon's title / aria-label. */
export const personalDataLabel = (username: string) => `Personal data for ${username}`
/** The in-row section's heading. */
export const sectionTitle = (display: string) => `Personal data for ${display}`
export const ERASE_TITLE = UI_SENTENCES.erase_title
export const ERASE_BUTTON = UI_SENTENCES.erase_button
export const RESUME_BUTTON = 'Resume erasure'
export const CHANGES_TITLE = 'What changes'
export const KEPT_TITLE = UI_SENTENCES.erase_kept_title
export const OPEN_ITEMS_TITLE = 'Open items a lead may reassign'
export const NOT_COUNTED = 'not counted (took too long)'
export const ERASED_BADGE = UI_SENTENCES.erased_badge
export const CONFIRM_MISMATCH = ERROR_SENTENCES.confirm_mismatch
export const IN_PROGRESS = 'Erasure in progress — signed out and disabled.'
export const LOAD_PREVIEW_FAILED = 'Could not load what an erasure would change.'
export const ERASE_FAILED = 'The erasure could not be started.'
/** An erased row whose erasure job is not in the person's job list (an erasure from before the list's window). */
export const ERASED_NO_JOB = 'This account was erased.'
/** The erased row's section heading for the counts of a finished erasure. */
export const ERASED_COUNTS_TITLE = 'Rows changed'

export const confirmLabel = (username: string) => fillSentence(UI_SENTENCES.erase_confirm_label, { username })
export const eraseBody = (pseudonym: string) => fillSentence(UI_SENTENCES.erase_body, { pseudonym })
export const erasedLine = (pseudonym: string) => fillSentence(UI_SENTENCES.erase_done, { pseudonym })

/** W38: the preview's `pending_request` — "This person asked for erasure on Sep 18, 2026." */
export const askedLine = (requestedAt: string) =>
  fillSentence(UI_SENTENCES.erasure_request_asked, { date: formatDay(requestedAt) })

/** "Requested by Morgan Admin." under an erasure job's line. */
export const requestedByLine = (display: string) => `Requested by ${display}.`

/**
 * Who started an erasure job (W38 D-11): a SCIM `DELETE` in erase mode has no requester (`requested_by_display`
 * null) and reads "Your identity provider (SCIM)"; otherwise the requester's display ("Erased user" once erased);
 * null when the job names nobody.
 */
export function requesterDisplay(job: Pick<SubjectJob, 'source' | 'requested_by_display'> | null | undefined): string | null {
  if (!job) return null
  if (job.source === 'scim') return UI_SENTENCES.requester_scim
  return job.requested_by_display?.trim() || null
}

// ── The phase line ───────────────────────────────────────────────────────────

/** The four committed steps before `done` (job.phase = the LAST step committed). */
export const ERASURE_STEPS = ['lockout', 'postgres', 'objects', 'redis'] as const
type ErasureStep = (typeof ERASURE_STEPS)[number]
export const STEP_LABELS: Record<ErasureStep, string> = {
  lockout: 'account locked',
  postgres: 'records pseudonymised',
  objects: 'stored exports removed',
  redis: 'cached sessions cleared',
}

/** "Erasure in progress — signed out and disabled. Step 2 of 4: records pseudonymised." */
export function phaseLine(job: Pick<SubjectJob, 'phase'> | null | undefined): string {
  const index = (ERASURE_STEPS as readonly string[]).indexOf(job?.phase ?? '')
  if (index < 0) return IN_PROGRESS
  const step = ERASURE_STEPS[index]
  return `${IN_PROGRESS} Step ${index + 1} of ${ERASURE_STEPS.length}: ${STEP_LABELS[step]}.`
}

export type ErasureViewKind = 'none' | 'in_progress' | 'incomplete' | 'done'

export interface ErasureView {
  kind: ErasureViewKind
  /** The phase line, or the done line. */
  line: string
  /** incomplete: the server's sentence ("The erasure stopped after the objects step; …"). */
  detail: string | null
  /** incomplete: "Resume erasure" (the same typed confirm) is offered. */
  resumable: boolean
}

/** A job the admin can resume: an unfinished erasure whose last attempt stopped (never "failed": it resumes). */
export function isResumable(job: SubjectJob | null | undefined): boolean {
  if (!job || job.kind !== 'erasure' || job.phase === 'done' || job.status === 'succeeded') return false
  return job.error?.code === 'erasure_incomplete' && isActiveJob(job)
}

/** What the panel shows for an erasure job (`null`: none — the preview and the confirm). */
export function erasureView(job: SubjectJob | null | undefined, pseudonym: string): ErasureView {
  if (!job) return { kind: 'none', line: '', detail: null, resumable: false }
  if (job.phase === 'done' || job.status === 'succeeded') {
    return { kind: 'done', line: erasedLine(pseudonym), detail: null, resumable: false }
  }
  if (isResumable(job)) {
    return { kind: 'incomplete', line: phaseLine(job), detail: job.error?.message?.trim() || null, resumable: true }
  }
  return { kind: isActiveJob(job) ? 'in_progress' : 'incomplete', line: phaseLine(job), detail: null, resumable: false }
}

// ── The preview ──────────────────────────────────────────────────────────────

/** A resume (E-8) is the documented way past these two: the unfinished job is re-queued instead of a 409. */
const RESUME_OVERRIDES = new Set<string>(['erasure_running', 'user_erased'])

/** The blockers that disable the button (a resumable job's own "already in progress" / "erased" do not). */
export function effectiveBlockers(preview: ErasurePreview | null | undefined): ErasureBlocker[] {
  const blockers = Array.isArray(preview?.blockers) ? preview.blockers : []
  if (!isResumable(preview?.erasure_job)) return blockers
  return blockers.filter((blocker) => !RESUME_OVERRIDES.has(blocker.code))
}

/** The button enables ONLY with no blocker and the typed value EXACTLY the username (case and whitespace count). */
export function canErase(preview: ErasurePreview | null | undefined, typed: string): boolean {
  if (!preview) return false
  return effectiveBlockers(preview).length === 0 && typed === preview.username
}

/** A location's count as shown: a number, or "not counted (took too long)" — never 0 for an uncounted one. */
export function locationCount(location: Pick<ErasureLocationCount, 'count' | 'measured'>): string {
  if (location.measured === false || typeof location.count !== 'number') return NOT_COUNTED
  return formatNumber(location.count)
}

export interface LocationLine {
  key: string
  label: string
  count: string
  /** keep_audit / keep_record: why it is kept (the muted second line); otherwise null. */
  reason: string | null
}

export function locationLine(location: ErasureLocationCount): LocationLine {
  return {
    key: location.key,
    label: location.label,
    count: locationCount(location),
    reason: location.reason?.trim() || null,
  }
}

/** "6 changes · 4 kept · 2 open items" — a count of 1 in the singular. */
export function previewSummary(preview: Pick<ErasurePreview, 'changes' | 'kept' | 'open_items'>): string {
  const changes = Array.isArray(preview.changes) ? preview.changes.length : 0
  const kept = Array.isArray(preview.kept) ? preview.kept.length : 0
  const open = Array.isArray(preview.open_items) ? preview.open_items.length : 0
  return [countNoun(changes, 'change'), `${formatNumber(kept)} kept`, countNoun(open, 'open item')].join(' · ')
}

export interface ErasureCountLines {
  /** The locations the erasure changed, largest first, each by its inventory label (the key when it has none). */
  lines: { key: string; label: string; count: string }[]
  /** The sentence for the locations that held nothing of the person (counted 0), or null when there are none. */
  untouched: string | null
}

/**
 * A finished erasure's per-location counts for the erased row's read-only outcome: the changed locations by label,
 * largest first; the ones that held nothing in one line (local demo, 2026-11-04: thirty "0 rows" lines of storage
 * keys hid the eight that mattered).
 */
export function erasureCountLines(job: Pick<SubjectJob, 'counts' | 'count_labels'> | null | undefined): ErasureCountLines {
  const entries = Object.entries(job?.counts ?? {}).filter(([, n]) => typeof n === 'number')
  const labels = job?.count_labels ?? {}
  const lines = entries
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([key, n]) => ({ key, label: labels[key] || key, count: countNoun(n, 'row') }))
  const zero = entries.length - lines.length
  return { lines, untouched: zero > 0 ? `${countNoun(zero, 'other location')} held nothing of this person.` : null }
}

// ── The row ──────────────────────────────────────────────────────────────────

/** The fields of a Users row this feature reads. The two flags are optional: the list may not send them. */
export interface UserRowLike {
  id: string
  username: string
  full_name?: string | null
  is_erased?: boolean | null
  erased_at?: string | null
  is_service_account?: boolean | null
  is_synthetic?: boolean | null
}

/** "Erased" means `users.erased_at` is set (E-11b) — the list's `is_erased` / `erased_at`. */
export function isErasedUser(user: UserRowLike): boolean {
  return Boolean(user.is_erased || user.erased_at)
}

/** A service or synthetic account holds no personal data to export or erase (`not_a_person`). */
export function isMachineAccount(user: UserRowLike): boolean {
  return Boolean(user.is_service_account || user.is_synthetic)
}

/** The Personal data action: every row but a service / synthetic account and the admin's OWN row (no self-erasure). */
export function offersPersonalData(user: UserRowLike, selfId: string | null | undefined): boolean {
  return !isMachineAccount(user) && user.id !== selfId
}

/** An erased row's name line: "Erased user" (the server's `full_name`), over the pseudonym — never the e-mail. */
export function erasedName(user: UserRowLike): string {
  return user.full_name?.trim() || ERASED_DISPLAY
}
