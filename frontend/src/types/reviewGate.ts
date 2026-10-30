/**
 * W23c AGT-10: the review gate's enforcement date — the wire vocabulary, the shapes and their defensive
 * parser (`GET` / `PUT /api/v1/settings/review-gate`, ADMIN).
 *
 * Until a date is set nothing is withheld (audit only); would-be refusals are only counted. The constants
 * mirror `backend/app/services/review_gate_vocab.py` value for value (pinned by
 * `backend/tests/test_review_gate_inline_vocab_parity.py`, which reads this file as TEXT — keep every
 * constant a plain literal on its own `export const` line, strings in one piece, one `key: 'value',` per
 * record line). Enum-like fields are plain strings on the wire.
 *
 * Absence is not health: `would_refuse.measured === false` means the audit log could not be read, and then
 * every number is dropped (never shown as 0). Every label and sentence is rendered as React text.
 */

/** Why the gate is (not) enforced right now. */
export const GATE_SOURCES = ['env', 'date', 'unset', 'scheduled'] as const
export type GateSource = (typeof GATE_SOURCES)[number]

/** Every channel a distribution decision is recorded under; the labels come from the server's answer. */
export const GATED_CHANNEL_CODES = [
  'report_pdf',
  'share_link_html',
  'share_link_pdf',
  'intelligence_export',
  'ai_history_export',
  'analysis_report',
  'digest_attachment',
  'email_run_report',
  'ai_summary_notification',
  'ai_summary_notification_relay',
  'github_pr_comment',
  'gitlab_mr_note',
  'release.decided.webhook',
] as const

export const WOULD_REFUSE_WINDOW_DAYS = 30

/** The page's sentences; `{runs}`, `{decisions}`, `{date}` are filled by the page. */
export const GATE_SENTENCES = {
  unset: "Audit only — nothing is withheld. {runs} runs' AI reports would have been withheld in the last 30 days ({decisions} distribution attempts).",
  scheduled: "Audit only until {date} — then unreviewed AI reports are withheld. {runs} runs' AI reports would have been withheld in the last 30 days ({decisions} distribution attempts).",
  date: 'Enforced since {date}: unreviewed AI reports are withheld on the channels below.',
  env: 'Enforced by the server setting REVIEW_GATE_ENFORCED — the date below does not apply while it is on.',
  not_measured: 'Not measured: the audit log could not be read.',
  past_date_warning: 'Setting a date in the past enforces the gate at once: CI reads PENDING_REVIEW for every unreviewed AI decision, and PDF and shared links of unreviewed reports are refused.',
} as const

/** detail = {code, message} on a 403 / 422 of the PUT (`extractErrorMessage` reads the message). */
export const REVIEW_GATE_ERRORS = {
  naive_datetime: 'enforced_from must carry a timezone offset, for example 2026-11-02T09:00:00+01:00.',
  interactive_only: 'Only an administrator signed in interactively can change the review gate; API keys can read it.',
} as const

export interface WouldRefuseChannel {
  channel: string
  decisions: number
}

export interface WouldRefuseSummary {
  /** False = the audit read failed / timed out: every number below is null. */
  measured: boolean
  window_days: number
  /** max(now - 30 d, the oldest audit row) — less than 30 days of trail is said. */
  since: string | null
  decisions: number | null
  runs: number | null
  /** Every channel with >= 1 row, decisions desc, then channel. */
  by_channel: WouldRefuseChannel[] | null
}

export interface GatedChannel {
  channel: string
  label: string
}

export interface ReviewGateRead {
  enforced: boolean
  source: GateSource
  /** The stored instant (UTC ISO-8601), or null (unset). */
  enforced_from: string | null
  /** The server's REVIEW_GATE_ENFORCED flag. */
  env_override: boolean
  updated_at: string | null
  would_refuse: WouldRefuseSummary
  /** GATED_CHANNELS, in order. */
  channels: GatedChannel[]
}

export interface ReviewGateWrite {
  /** An ISO-8601 instant WITH an offset (a naive value is 422), or null to clear (back to audit only). */
  enforced_from: string | null
}

// ── the defensive parser ─────────────────────────────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function countOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null
}

function isGateSource(v: unknown): v is GateSource {
  return typeof v === 'string' && (GATE_SOURCES as readonly string[]).includes(v)
}

const NOT_MEASURED: WouldRefuseSummary = {
  measured: false,
  window_days: WOULD_REFUSE_WINDOW_DAYS,
  since: null,
  decisions: null,
  runs: null,
  by_channel: null,
}

/** `measured: false` (or a malformed block) drops every number: absence is never shown as 0. */
export function parseWouldRefuse(raw: unknown): WouldRefuseSummary {
  if (!isRecord(raw) || raw.measured !== true) return { ...NOT_MEASURED }
  const decisions = countOrNull(raw.decisions)
  const runs = countOrNull(raw.runs)
  if (decisions === null || runs === null) return { ...NOT_MEASURED }
  const byChannel: WouldRefuseChannel[] = []
  if (Array.isArray(raw.by_channel)) {
    for (const row of raw.by_channel) {
      if (!isRecord(row) || typeof row.channel !== 'string') continue
      const count = countOrNull(row.decisions)
      if (count !== null) byChannel.push({ channel: row.channel, decisions: count })
    }
  }
  return {
    measured: true,
    window_days: countOrNull(raw.window_days) ?? WOULD_REFUSE_WINDOW_DAYS,
    since: strOrNull(raw.since),
    decisions,
    runs,
    by_channel: byChannel,
  }
}

/** The GET / PUT answer, or `null` when it cannot be read (an unknown `source` included). */
export function parseReviewGate(raw: unknown): ReviewGateRead | null {
  if (!isRecord(raw) || !isGateSource(raw.source)) return null
  if (typeof raw.enforced !== 'boolean' || typeof raw.env_override !== 'boolean') return null
  const channels: GatedChannel[] = []
  if (Array.isArray(raw.channels)) {
    for (const row of raw.channels) {
      if (isRecord(row) && typeof row.channel === 'string' && typeof row.label === 'string') {
        channels.push({ channel: row.channel, label: row.label })
      }
    }
  }
  return {
    enforced: raw.enforced,
    source: raw.source,
    enforced_from: strOrNull(raw.enforced_from),
    env_override: raw.env_override,
    updated_at: strOrNull(raw.updated_at),
    would_refuse: parseWouldRefuse(raw.would_refuse),
    channels,
  }
}
