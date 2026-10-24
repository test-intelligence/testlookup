/**
 * W11 "release-planning-policy" hermetic answers (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §1.1, §2.1-§2.10,
 * §3, §6.2 G-E): the worlds of the eight W11 specs (`tests/ci-e2e/releases-plan-phases`, `releases-schedule`,
 * `releases-portfolio-trend`, `release-attribution-rules`, `policy-bands`, `digests-release-verdict`,
 * `release-gate-follows-release`, `compliance-pack-verify`), of the extended `policy-editor.spec.ts`, and of
 * `prod-release-planning.visual.spec.ts`.
 *
 * Each factory COMPOSES the existing worlds (`releasesInPageWorld()`, `releasesWorld()`, `TRENDS_ON`, `OVERVIEW_ON`,
 * `releaseGateOn()`, `digestsWorld()`, `LAYOUT`; never edits one) and lists its own W11 handlers FIRST (first match
 * wins, `production-pages.ts`): every W11 path is answered before the page's reads, and a W11 path an existing world
 * would capture (`GET /releases/schedule.ics` and `/releases/portfolio-trend` match the release DETAIL's
 * `/^\/api\/v1\/releases\/[^/]+$/`, which would answer 404 `Release not found`) is answered by its own handler. Each
 * returns fresh state per call and records every write it answered (`writes`: `METHOD path` and the parsed JSON body;
 * `drafts`: the multipart parts of a PRD draft; `downloads`: an export's whole target and its `Authorization`), so a
 * spec pins a whole body with `toEqual`.
 *
 * Every answer has the server's shape (§1.1) and refuses what the real route refuses (§3.1's sentences, verbatim):
 * the worlds are contract doubles, not stubs. Users: `ADMIN`, `QA_LEAD` (the fixture `USER`, a member of the one
 * project Checkout) and `QA_ENGINEER`. No key material: the public key PEM is built at run time from a fixed byte
 * pattern (no private key exists anywhere in the fixtures), and the PRD text is synthetic.
 * Not a spec file.
 */
import type { Route } from '@playwright/test'
import { ApiResponse, respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, LAYOUT, NOW, OVERVIEW_ON, PROJECT_ID, releaseGateOn, RUN_ID, runs, RUNS, TRENDS_ON, USER } from './fixtures'
import { bodyOf, delegate, multipartOf, releasesInPageWorld, type MultipartPart, type RecordedWrite } from './fixtures-in-page'
import { DIGEST_PREVIEW_PATH, digestsWorld } from './fixtures-notify-flaky'
import {
  gateSummary,
  releasePhase,
  releasesReadHandlers,
  releasesWorld,
  RELEASE_ACTIVE_ID,
  RELEASE_PACK,
  RELEASE_PLANNING_ID,
  RELEASE_SHIPPED_ID,
  type ReleasesWorld,
} from './fixtures-pages'
import { ADMIN, QA_ENGINEER, VIEWER } from './fixtures-releases'

export type { RecordedWrite }
export { ADMIN, QA_ENGINEER, RELEASE_ACTIVE_ID, RELEASE_PLANNING_ID, RELEASE_SHIPPED_ID, VIEWER }
/** The QA lead of the fixtures: a member of Checkout only (`PROJECTS`), not an administrator. */
export const QA_LEAD = USER

const P = PROJECT_ID
const DAY_MS = 86_400_000

/** A request's target as the server saw it: the path and the query, whole (a spec compares the whole value). */
export const targetOf = (request: ApiRequest): string => `${request.url.pathname}${request.url.search}`

/** Record `request` in `writes` and return its parsed JSON body. */
function recordIn(writes: RecordedWrite[], request: ApiRequest): unknown {
  const body = bodyOf(request)
  writes.push({ line: `${request.method} ${request.path}`, body })
  return body
}

/**
 * `RawResponse` (`production-pages.ts`) carries a body and a content type, not headers. An export's filename travels
 * in `Content-Disposition` (`releasesService.exportSchedule` reads it), so the handler adds its headers to the ONE
 * fulfil the harness makes next on this request's route: the harness passes `contentType`, Playwright merges it over
 * `headers`, and nothing else about the answer changes.
 */
function withHeaders<T>(request: ApiRequest, headers: Record<string, string>, answer: T): T {
  const route = request.route as Route
  const fulfill = route.fulfill.bind(route)
  route.fulfill = (options = {}) => fulfill({ ...options, headers: { ...(options.headers ?? {}), ...headers } })
  return answer
}

/** An ISO instant moved by `deltaMs` (null stays null). */
const shifted = (iso: string | null, deltaMs: number): string | null => (iso === null ? null : new Date(Date.parse(iso) + deltaMs).toISOString())
/** `YYYY-MM-DD` at 00:00 UTC, as an ISO instant. */
const utcDay = (day: string): string => `${day}T00:00:00.000Z`

// ── The vocabularies and sentences of §1.2 (`core/release_planning.py`), verbatim ──

export const PHASE_TYPES = ['planning', 'development', 'code_freeze', 'qa_testing', 'uat', 'staging', 'production'] as const
export const SENTENCES = {
  phaseType: 'Phase type must be one of: planning, development, code_freeze, qa_testing, uat, staging, production.',
  phaseWindow: 'A phase cannot end before it starts.',
  phaseRepeated: (name: string) => `Phase '${name}' appears twice in this request`,
  targetHasPhases: 'This release already has phases. Remove them first, or add phases one at a time.',
  sourceHasNoPhases: 'That release has no phases to copy.',
  sourceIsTarget: 'Choose another release to copy from.',
  noShiftAnchor: 'Choose when the first phase starts: this release has no cutoff start.',
  releaseNotFound: 'Release not found',
  prdEmpty: 'Paste the PRD text or choose a .md, .txt or .docx file.',
  prdBoth: 'Send the PRD as text or as a file, not both.',
  prdBadType: 'Only .md, .txt and .docx files can be read.',
} as const

// ── /releases: the planning world (REL-05, REL-07, REL-06, REL-03, REL-11) ─────────────────────────────

type PhaseFixture = ReturnType<typeof releasePhase>

/** A phase of the fixtures with a planned window (and its exit criteria), typed as the world's phase rows. */
function datedPhase(
  releaseId: string,
  id: string,
  name: string,
  phaseType: string,
  status: string,
  order: number,
  start: string | null,
  end: string | null,
  extra: Record<string, unknown> = {},
): PhaseFixture {
  return { ...releasePhase(releaseId, id, name, phaseType, status, order), planned_start: start, planned_end: end, ...extra } as unknown as PhaseFixture
}

/**
 * The clone source: 2026.10's four phases, dated 2026-08-03 .. 2026-08-25 (a 22-day span), completed. UAT carries a
 * phase-gate layer (`exit_criteria`, a partial PolicyDocument) that the copy keeps verbatim.
 */
export const SOURCE_PHASES = [
  { id: 'ph-1010-smoke', name: 'Smoke', phase_type: 'qa_testing', start: '2026-08-03', end: '2026-08-05' },
  { id: 'ph-1010-regression', name: 'Regression', phase_type: 'qa_testing', start: '2026-08-06', end: '2026-08-14' },
  { id: 'ph-1010-uat', name: 'UAT', phase_type: 'uat', start: '2026-08-17', end: '2026-08-21', exit_criteria: { thresholds: { pass_rate_minimum: 98 } } },
  { id: 'ph-1010-cutover', name: 'Production cutover', phase_type: 'production', start: '2026-08-24', end: '2026-08-25' },
] as const

/** 2026.11's own phases with planned windows in September (the calendar's bars: the week of the 7th, 14th and 21st). */
export const ACTIVE_PHASE_WINDOWS: Record<string, [string, string]> = {
  'ph-build': ['2026-09-07', '2026-09-11'],
  'ph-soak': ['2026-09-14', '2026-09-18'],
  'ph-uat': ['2026-09-21', '2026-09-25'],
}

/** The day the clone spec chooses for the first copied phase (63 days after 2026-08-03). */
export const CLONE_FIRST_START = '2026-10-05'
export const CLONE_SHIFT_DAYS = 63

/** The synthetic PRD the draft spec pastes: an Overview (a non-phase section, skipped) and three phase headings. */
export const PRD_TEXT = [
  '# Checkout v2 test plan',
  '',
  '## Overview',
  'The checkout revamp ships in 2026.12.',
  '',
  '## Smoke testing',
  'Run the smoke pack on every build.',
  '',
  '## Regression (5 days)',
  'Full regression across checkout and payments.',
  '',
  '## UAT',
  'Customer acceptance on staging.',
  '',
  'Exit criteria:',
  '- No open P0 defects',
  '- Sign-off from the product owner',
].join('\n')

/** The heading parser's draft of `PRD_TEXT` (`PhaseDraft`, §1.1): what the hermetic `POST …/phases/generate` answers. */
export const DRAFTED_PHASES = [
  { name: 'Smoke testing', phase_type: 'qa_testing', description: 'Run the smoke pack on every build.', exit_criteria_text: null, planned_days: null, heading: 'Smoke testing' },
  { name: 'Regression', phase_type: 'qa_testing', description: 'Full regression across checkout and payments.', exit_criteria_text: null, planned_days: 5, heading: 'Regression (5 days)' },
  { name: 'UAT', phase_type: 'uat', description: 'Customer acceptance on staging.', exit_criteria_text: 'No open P0 defects; Sign-off from the product owner', planned_days: null, heading: 'UAT' },
] as const

/** The draft's badge reason: the rules draft, because the deployment is offline (`FALLBACK_REASON_TEXT.offline_mode`). */
export const DRAFT_FALLBACK = { reason: 'offline_mode', text: 'the deployment is offline (AI_OFFLINE_MODE)' } as const

/** The deployment's one signing key (§2.10): `k-` + 16 hex. */
export const SIGNING_KEY_ID = 'k-1a2b3c4d5e6f7a8b'

/** A SubjectPublicKeyInfo PEM for an Ed25519 key, built from a fixed byte pattern (a PUBLIC key; never a private one). */
export const SIGNING_PUBLIC_PEM = (() => {
  // The 12-byte SPKI prefix of an Ed25519 public key, then 32 fixed bytes.
  const prefix = [0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00]
  const spki = btoa(String.fromCharCode(...prefix, ...Array.from({ length: 32 }, () => 0x5a)))
  return `-----BEGIN PUBLIC KEY-----\n${spki}\n-----END PUBLIC KEY-----\n`
})()

/** The twelve files a pack's manifest lists (`compliance_pack_service._build_pack_payload`'s names, and its README). */
export const PACK_FILES = [
  'README.md',
  'agent_activity.json',
  'ai_history.json',
  'audit_events.json',
  'clusters.json',
  'decision.json',
  'decision_trail.json',
  'defects.json',
  'policy_snapshot.json',
  'release.json',
  'release_gate_decision.json',
  'run.json',
] as const

export const VERIFIED_SUMMARY = `Verified · 12 files · signed by key ${SIGNING_KEY_ID}`
export const TAMPERED_SUMMARY = 'Tampered: decision.json does not match the manifest'

const sha = (seed: string) => Array.from({ length: 64 }, (_, i) => '0123456789abcdef'[(seed.charCodeAt(i % seed.length) + i) % 16]).join('')

/** `CompliancePackVerification` (§1.1) for the signed pack: verified, or one byte changed in `decision.json`. */
export function packVerification(kind: 'verified' | 'tampered') {
  const files = PACK_FILES.map((name) => {
    const expected = sha(name)
    const bad = kind === 'tampered' && name === 'decision.json'
    return { name, status: bad ? 'mismatch' : 'ok', expected_sha256: expected, actual_sha256: bad ? sha(`${name}-edited`) : expected }
  })
  const ok = files.filter((f) => f.status === 'ok').length
  return {
    pack_id: RELEASE_PACK.id,
    result: kind,
    chain: kind === 'verified' ? 'intact' : 'broken',
    manifest_matches_record: true,
    signature: 'valid',
    signing_key_id: SIGNING_KEY_ID,
    files_checked: files.length,
    files_ok: ok,
    files,
    summary: kind === 'verified' ? VERIFIED_SUMMARY : TAMPERED_SUMMARY,
    checked_at: NOW.toISOString(),
  }
}

/** The Monday on or before `NOW` (2026-09-14, 00:00 UTC). */
const LAST_MONDAY = Date.UTC(2026, 8, 14)

/**
 * `ReleasePortfolioTrend` (§1.1, §2.5) for Checkout over 12 weeks: 13 points (12 Mondays, then NOW), `null` for the
 * two weeks before the first release existed, and a LAST point equal to each card's own count over this world
 * (In progress 1 · Ready to ship 0 · Blocked 1 · Released · 30d 1). Totals: 2 releases evaluated in the window (GO 1,
 * No-Go 1), 1 released, with a pack.
 */
export function portfolioTrend() {
  const pointsAt = [...Array.from({ length: 12 }, (_, i) => new Date(LAST_MONDAY - (11 - i) * 7 * DAY_MS).toISOString()), NOW.toISOString()]
  return {
    project_id: P,
    weeks: 12,
    points_at: pointsAt,
    in_progress: [null, null, 1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 1],
    ready_to_ship: [null, null, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0],
    blocked: [null, null, 0, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1],
    released_30d: [null, null, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1],
    evaluated_in_week: [0, 0, 0, 1, 1, 2, 1, 1, 2, 1, 0, 1, 1],
    go_in_week: [0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 0, 0, 0],
    totals: { evaluated: 2, go: 1, conditional_go: 0, no_go: 1, not_evaluated: 0, released: 1, released_with_pack: 1 },
    approximated: ['in_progress', 'ready_to_ship', 'blocked'],
    generated_at: NOW.toISOString(),
  }
}

/** The two captions `portfolioTrend()`'s totals give (§2.5). */
export const PORTFOLIO_CAPTIONS = {
  ready: 'GO on 1 of 2 evaluated (12 w)',
  pack: '1 of 1 released with a pack (12 w)',
} as const

/** The schedule export's filename for Checkout on NOW's day (`schedule_filename`). */
export const scheduleFilename = (fmt: 'ics' | 'csv') => `testlookup-releases-checkout-20260918.${fmt}`

/** One export a spec observed: the whole target (path + query) and the `Authorization` header it carried. */
export interface RecordedDownload {
  target: string
  authorization: string | null
}

/** One PRD draft request: its target, content type and multipart parts (never the PRD in a URL). */
export interface RecordedDraft {
  target: string
  contentType: string
  parts: MultipartPart[]
}

const icsDate = (iso: string) => iso.slice(0, 10).replace(/-/g, '')
const icsNextDate = (iso: string) => icsDate(new Date(Date.parse(utcDay(iso.slice(0, 10))) + DAY_MS).toISOString())
const icsStamp = (iso: string) => `${iso.slice(0, 19).replace(/[-:]/g, '')}Z`

/** RFC 5545 for the world's releases and planned phases (the shape `write_ics` writes; CRLF, nothing over 75 octets). */
function scheduleIcs(world: ReleasesWorld): string {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//TestLookup//Release schedule//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:TestLookup releases · Checkout']
  for (const r of world.rows) {
    if (['cancelled', 'canceled', 'archived'].includes(r.status)) continue
    const day = r.released_at ?? r.planned_date
    if (day) {
      lines.push(
        'BEGIN:VEVENT',
        `UID:release-${r.id}@testlookup`,
        `DTSTAMP:${icsStamp(NOW.toISOString())}`,
        `DTSTART;VALUE=DATE:${icsDate(day)}`,
        `DTEND;VALUE=DATE:${icsNextDate(day)}`,
        `SUMMARY:Release ${r.name} (${r.released_at ? 'released' : 'planned'})`,
        'END:VEVENT',
      )
    }
    for (const ph of r.phases as Array<{ id: string; name: string; planned_start: string | null; planned_end: string | null }>) {
      if (!ph.planned_start || !ph.planned_end) continue
      lines.push(
        'BEGIN:VEVENT',
        `UID:phase-${ph.id}@testlookup`,
        `DTSTAMP:${icsStamp(NOW.toISOString())}`,
        `DTSTART;VALUE=DATE:${icsDate(ph.planned_start)}`,
        `DTEND;VALUE=DATE:${icsNextDate(ph.planned_end)}`,
        `SUMMARY:${r.name} · ${ph.name}`,
        'END:VEVENT',
      )
    }
  }
  lines.push('END:VCALENDAR')
  return `${lines.join('\r\n')}\r\n`
}

/** The UTF-8 byte-order mark the CSV starts with (Excel), as a code point (no invisible character in the source). */
export const BOM = String.fromCharCode(0xfeff)

export const SCHEDULE_CSV_HEADER =
  'project,release,version,release_status,planned_date,released_at,phase_order,phase,phase_type,phase_status,planned_start,planned_end,actual_start,actual_end'

/** The CSV `write_csv` writes: a BOM, the header, one CRLF row per phase (a release without phases: one row). */
function scheduleCsv(world: ReleasesWorld): string {
  const rows = [SCHEDULE_CSV_HEADER]
  for (const r of world.rows) {
    const phases = r.phases as Array<{ name: string; phase_type: string; status: string; order_index: number; planned_start: string | null; planned_end: string | null }>
    const head = ['Checkout', r.name, r.version, r.status, r.planned_date ?? '', r.released_at ?? '']
    if (phases.length === 0) rows.push([...head, '', '', '', '', '', '', '', ''].join(','))
    for (const ph of phases) rows.push([...head, String(ph.order_index), ph.name, ph.phase_type, ph.status, ph.planned_start ?? '', ph.planned_end ?? '', '', ''].join(','))
  }
  return `${BOM}${rows.join('\r\n')}\r\n`
}

export interface PlanningOptions {
  /** The portfolio trend's answer: the 12-week series (default), or a 500. */
  trend?: 'ok' | 'error'
  /** What Verify answers for the signed pack (default verified). */
  verify?: 'verified' | 'tampered'
}

/**
 * The W27a `/releases` world (`releasesInPageWorld()`: the W05 reads and writes, create / delete / link) plus W11:
 *  - 2026.10 (released) carries four dated phases (the clone source, `SOURCE_PHASES`) and its pack is SIGNED
 *    (`signing_key_id`); 2026.11's three phases get September windows (`ACTIVE_PHASE_WINDOWS`, the calendar's bars);
 *    2026.12 (planning, no phases) is the target of Copy and Draft;
 *  - `POST /releases/{id}/phases/clone` (§2.1), `/phases/generate` (§2.2, multipart, persists nothing), `/phases/bulk`
 *    (§2.2), each judged with the server's rules and sentences and changing the world, so the detail re-read shows it;
 *  - `GET /releases/schedule.ics` / `.csv` (§2.3) with the server's `Content-Disposition`, `GET /releases/portfolio-trend`
 *    (§2.5), `POST /compliance-packs/{id}/verify` and the public key's PEM (§2.10).
 */
export function releasePlanningWorld(options: PlanningOptions = {}) {
  const base = releasesInPageWorld()
  const { world, writes } = base
  const drafts: RecordedDraft[] = []
  const downloads: RecordedDownload[] = []
  const row = (id: string) => world.rows.find((r) => r.id === id)
  const releaseIdOf = (path: string) => path.split('/')[4]

  const shipped = row(RELEASE_SHIPPED_ID)
  if (shipped) {
    shipped.phases = SOURCE_PHASES.map((p, i) =>
      datedPhase(RELEASE_SHIPPED_ID, p.id, p.name, p.phase_type, 'completed', i, utcDay(p.start), utcDay(p.end), 'exit_criteria' in p ? { exit_criteria: p.exit_criteria } : {}),
    )
  }
  const active = row(RELEASE_ACTIVE_ID)
  if (active) {
    active.phases = active.phases.map((p) => {
      const window = ACTIVE_PHASE_WINDOWS[p.id]
      return window ? ({ ...p, planned_start: utcDay(window[0]), planned_end: utcDay(window[1]) } as unknown as PhaseFixture) : p
    })
  }
  world.packs = world.packs.map((pack) => ({ ...pack, signing_key_id: SIGNING_KEY_ID }))

  let created = 0
  /** Append `items` as the target's first schedule (status pending, order 0..n-1), as `create_phases_bulk` does. */
  function addPhases(target: NonNullable<ReturnType<typeof row>>, items: Array<Record<string, unknown>>) {
    const rows = items.map((item, i) => {
      created += 1
      return datedPhase(
        target.id,
        `ph-w11-${String(created).padStart(2, '0')}`,
        String(item.name).trim(),
        String(item.phase_type ?? 'qa_testing'),
        'pending',
        i,
        (item.planned_start as string | null | undefined) ?? null,
        (item.planned_end as string | null | undefined) ?? null,
        {
          description: (item.description as string | null | undefined) ?? null,
          notes: (item.notes as string | null | undefined) ?? null,
          exit_criteria: item.exit_criteria === undefined || item.exit_criteria === null ? null : structuredClone(item.exit_criteria),
          created_at: NOW.toISOString(),
          updated_at: NOW.toISOString(),
        },
      )
    })
    target.phases = rows
    target.updated_at = NOW.toISOString()
    return rows
  }

  const handlers: ApiHandlers = [
    // REL-05: copy another release's phases (§2.1). The body id is read with the target's project FIRST.
    [/^\/api\/v1\/releases\/[^/]+\/phases\/clone$/, (request) => {
      const body = (recordIn(writes, request) ?? {}) as { from_release_id?: string; shift_to?: string | null }
      const target = row(releaseIdOf(request.path))
      if (!target) return respond(404, { detail: SENTENCES.releaseNotFound })
      const source = world.rows.find((r) => r.id === body.from_release_id && r.project_id === target.project_id)
      if (!source) return respond(404, { detail: SENTENCES.releaseNotFound })
      if (source.id === target.id) return respond(422, { detail: SENTENCES.sourceIsTarget })
      if (source.phases.length === 0) return respond(422, { detail: SENTENCES.sourceHasNoPhases })
      if (target.phases.length > 0) return respond(409, { detail: SENTENCES.targetHasPhases })
      const ordered = [...source.phases].sort((a, b) => a.order_index - b.order_index) as unknown as Array<Record<string, unknown> & { planned_start: string | null; planned_end: string | null }>
      const dated = ordered.filter((p) => p.planned_start)
      const anchor = dated.length ? Math.min(...dated.map((p) => Date.parse(p.planned_start as string))) : null
      const newStart = body.shift_to ?? ((target as Record<string, unknown>).cutoff_start_at as string | null | undefined) ?? null
      if (anchor !== null && !newStart) return respond(422, { detail: SENTENCES.noShiftAnchor })
      const delta = anchor !== null && newStart ? Date.parse(newStart) - anchor : 0
      const rows = addPhases(
        target,
        ordered.map((p) => ({ ...p, planned_start: shifted(p.planned_start, delta), planned_end: shifted(p.planned_end, delta) })),
      )
      return respond(201, {
        release_id: target.id,
        created: rows.length,
        phases: rows,
        source_release_id: source.id,
        shifted_by_days: anchor === null ? null : Math.round(delta / DAY_MS),
      })
    }, 'POST'],
    // REL-07: the PRD draft (§2.2). Multipart, text OR file; persists nothing. The hermetic answer is the rules draft.
    [/^\/api\/v1\/releases\/[^/]+\/phases\/generate$/, (request) => {
      const multipart = multipartOf(request)
      drafts.push({ target: targetOf(request), ...multipart })
      const target = row(releaseIdOf(request.path))
      if (!target) return respond(404, { detail: SENTENCES.releaseNotFound })
      const text = multipart.parts.find((p) => p.name === 'text' && p.filename === undefined)
      const file = multipart.parts.find((p) => p.name === 'file')
      if (text && file) return respond(422, { detail: SENTENCES.prdBoth })
      if (!text && !file) return respond(422, { detail: SENTENCES.prdEmpty })
      if (file && !/\.(md|txt|docx)$/i.test(file.filename ?? '')) return respond(422, { detail: SENTENCES.prdBadType })
      const read = (text?.value ?? file?.value ?? '').trim()
      if (!read) return respond(422, { detail: SENTENCES.prdEmpty })
      return {
        release_id: target.id,
        source: 'rules',
        model_name: null,
        fallback_reason: DRAFT_FALLBACK.reason,
        phases: DRAFTED_PHASES.map((p) => ({ ...p })),
        warnings: [],
        text_chars: read.length,
      }
    }, 'POST'],
    // REL-07: create the reviewed draft in one request (§2.2), the server's rules and sentences.
    [/^\/api\/v1\/releases\/[^/]+\/phases\/bulk$/, (request) => {
      const body = (recordIn(writes, request) ?? {}) as { phases?: Array<Record<string, unknown>>; source?: string }
      const target = row(releaseIdOf(request.path))
      if (!target) return respond(404, { detail: SENTENCES.releaseNotFound })
      const items = body.phases ?? []
      if (items.length < 1 || items.length > 30) return respond(422, { detail: [{ loc: ['body', 'phases'], msg: 'List should have at least 1 item', type: 'too_short' }] })
      if (target.phases.length > 0) return respond(409, { detail: SENTENCES.targetHasPhases })
      const seen = new Set<string>()
      for (const item of items) {
        const name = String(item.name ?? '').trim()
        if (seen.has(name.toLowerCase())) return respond(409, { detail: SENTENCES.phaseRepeated(name) })
        seen.add(name.toLowerCase())
        if (!PHASE_TYPES.includes(String(item.phase_type ?? 'qa_testing') as (typeof PHASE_TYPES)[number])) return respond(422, { detail: SENTENCES.phaseType })
        if (item.planned_start && item.planned_end && Date.parse(String(item.planned_end)) < Date.parse(String(item.planned_start))) {
          return respond(422, { detail: SENTENCES.phaseWindow })
        }
      }
      const rows = addPhases(target, items)
      return respond(201, { release_id: target.id, created: rows.length, phases: rows, source_release_id: null, shifted_by_days: null })
    }, 'POST'],
    // REL-06: the schedule exports (§2.3), declared before the release detail's pattern, which would capture them.
    ...(['ics', 'csv'] as const).map((fmt) => [
      `/api/v1/releases/schedule.${fmt}`,
      (request: ApiRequest) => {
        downloads.push({ target: targetOf(request), authorization: request.route.request().headers()['authorization'] ?? null })
        const body = fmt === 'ics' ? scheduleIcs(world) : scheduleCsv(world)
        return withHeaders(
          request,
          { 'Content-Disposition': `attachment; filename="${scheduleFilename(fmt)}"`, 'Cache-Control': 'no-store' },
          respondRaw(body, fmt === 'ics' ? 'text/calendar; charset=utf-8' : 'text/csv; charset=utf-8'),
        )
      },
    ] as const),
    // REL-03: the 12-week trend (§2.5), read only when the "Release health · this week" disclosure is open.
    ['/api/v1/releases/portfolio-trend', () => (options.trend === 'error' ? respond(500, { detail: 'Internal Server Error' }) : portfolioTrend())],
    // REL-11: a pack's rows carry `signing_key_id` (§1.1); Verify (§2.10) writes nothing.
    [/^\/api\/v1\/releases\/[^/]+\/compliance-packs$/, (request) => {
      const answer = delegate(base.handlers, request)
      if (!Array.isArray(answer)) return answer
      return answer.map((pack: Record<string, unknown>) => ({ ...pack, signing_key_id: SIGNING_KEY_ID }))
    }],
    [/^\/api\/v1\/compliance-packs\/[^/]+\/verify$/, (request) => {
      recordIn(writes, request)
      const id = request.path.split('/')[4]
      if (!world.packs.some((p) => p.id === id)) return respond(404, { detail: 'Compliance pack not found' })
      return packVerification(options.verify ?? 'verified')
    }, 'POST'],
    ['/api/v1/compliance-packs/signing-keys', () => ({
      active_key_id: SIGNING_KEY_ID,
      keys: [{ key_id: SIGNING_KEY_ID, algorithm: 'ed25519', public_key_pem: SIGNING_PUBLIC_PEM, created_at: isoAgo(9), retired_at: null, active: true }],
    })],
    [/^\/api\/v1\/compliance-packs\/signing-keys\/[^/]+\/pem$/, (request) => {
      downloads.push({ target: targetOf(request), authorization: request.route.request().headers()['authorization'] ?? null })
      if (request.path.split('/')[5] !== SIGNING_KEY_ID) return respond(404, { detail: 'Signing key not found' })
      return withHeaders(
        request,
        { 'Content-Disposition': `attachment; filename="testlookup-compliance-${SIGNING_KEY_ID}.pem"` },
        respondRaw(SIGNING_PUBLIC_PEM, 'application/x-pem-file'),
      )
    }],
    ...base.handlers,
  ]
  return { handlers, world, writes, drafts, downloads }
}

// ── /releases/attribution-rules (REL-09) ─────────────────────────────────────────────────────────────────────

export const RULES_PATH = `/api/v1/projects/${P}/attribution-rules`

/** `AttributionRuleFieldsResponse` (§1.1): `AttributionMatchField`'s declaration order, labels and help (§1.2). */
export const ATTRIBUTION_FIELDS = {
  fields: [
    { value: 'branch', label: 'Branch', glob: true, help: 'The run\'s git branch. Wildcards: * and ?, e.g. release/2.5*.' },
    { value: 'build_number', label: 'Build number', glob: true, help: 'The run\'s build label. Wildcards: * and ?, e.g. nightly-*.' },
    { value: 'environment', label: 'Environment', glob: false, help: 'The run\'s environment, matched exactly (case does not matter), e.g. staging.' },
    { value: 'tag', label: 'Tag', glob: true, help: 'Any of the run\'s tags. Wildcards: * and ?, e.g. rc-*.' },
  ],
}

export const RULE_IDS = {
  hotfix: '99999999-0000-4000-8000-0000000000a1',
  staging: '99999999-0000-4000-8000-0000000000a2',
  nightly: '99999999-0000-4000-8000-0000000000a3',
  created: '99999999-0000-4000-8000-0000000000b1',
} as const

/** A rule as the router serialises it (`_serialize`). */
export function attributionRule(id: string, name: string, field: string, pattern: string, target: string, priority: number, enabled: boolean, createdAt: string) {
  return {
    id,
    project_id: P,
    name,
    match_field: field,
    match_pattern: pattern,
    target_release_name: target,
    priority,
    is_enabled: enabled,
    created_at: createdAt,
  }
}

export type AttributionRuleFixture = ReturnType<typeof attributionRule>

/** The project's three rules, in their evaluation order (priority, then created_at). */
export function attributionRules(): AttributionRuleFixture[] {
  return [
    attributionRule(RULE_IDS.hotfix, 'Hotfix branches', 'branch', 'hotfix/*', '2026.11.1', 10, true, isoAgo(20)),
    attributionRule(RULE_IDS.staging, 'Staging soak', 'environment', 'staging', '2026.11', 50, true, isoAgo(12)),
    attributionRule(RULE_IDS.nightly, 'Nightly builds', 'build_number', 'nightly-*', '2026.12', 100, false, isoAgo(5)),
  ]
}

/** What the spec's New rule types (§2.4's demo rule). */
export const NEW_RULE = { name: 'Release 2.5 line', match_field: 'branch', match_pattern: 'release/2.5*', target_release_name: '2.5', priority: 200, is_enabled: true } as const

/** The preview's numbers for a branch rule (§2.4): 12 of 200 match (183 carry a branch); 3 shadowed, 2 explicit; 4 unattributed. */
export const PREVIEW_COUNTS = { considered: 200, matched: 12, field_present: 183, shadowed: 3, explicit: 2, unattributed_recent: 4 } as const

/** The first ten matched runs, newest first: 5 would land here, 3 on an earlier rule, 2 stay (named). */
export function previewSamples() {
  const landing = ['rule', 'earlier_rule', 'rule', 'explicit', 'rule', 'earlier_rule', 'rule', 'explicit', 'earlier_rule', 'rule'] as const
  return landing.map((would, i) => ({
    run_id: `55555555-5555-4555-8555-${String(100000000000 + i)}`,
    build_number: String(431 - i),
    branch: i % 3 === 0 ? `release/2.5-hotfix-${i}` : `release/2.5-rc${i}`,
    environment: i % 2 === 0 ? 'staging' : 'qa',
    tags: i % 4 === 0 ? ['rc', 'checkout'] : ['checkout'],
    created_at: isoAgo(i, 3),
    current_release_name: would === 'explicit' ? '2.5.0-rc' : i === 9 ? null : '2026.11',
    would_land: would,
  }))
}

/** The preview sentences the page prints for `PREVIEW_COUNTS` and a missing `2.5` (§2.4), verbatim. */
export const PREVIEW_SENTENCES = {
  matches: 'Matches 12 of the last 200 runs (183 carry a branch).',
  shadowed: '3 of them already match an earlier rule, which wins.',
  explicit: '2 named their release outright; a rule does not move them.',
  missingTarget: 'No release named "2.5" yet: the first matching run creates it.',
  footer: '4 of the last 200 runs have no release (Unattributed).',
} as const

/**
 * `/releases/attribution-rules` (§2.4) over the W27a `/releases` world (so a spec can start at `/releases` and take
 * the ⋯ item): the rules list (`{items, total}`, server order), the fields route, the preview (exactly today's four
 * keys without `detail`; the five new fields and the grown samples with `detail=true`), create (201), update (PUT) and
 * delete (204), each changing the list the next read returns. `target_exists` is judged against the world's release
 * names, case-insensitively (the linker's own match).
 */
export function attributionRulesWorld() {
  const releases = releasesInPageWorld()
  const writes = releases.writes
  const rules = attributionRules()
  const sorted = () => [...rules].sort((a, b) => a.priority - b.priority || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
  const ruleOf = (path: string) => rules.find((r) => r.id === path.split('/').pop())
  const RULE = new RegExp(`^${RULES_PATH}/[0-9a-f-]{36}$`)
  const previews: { target: string; body: unknown }[] = []
  const handlers: ApiHandlers = [
    [`${RULES_PATH}/fields`, () => ATTRIBUTION_FIELDS],
    [`${RULES_PATH}/preview`, (request) => {
      const body = (bodyOf(request) ?? {}) as { target_release_name?: string }
      previews.push({ target: targetOf(request), body })
      const old = { considered: PREVIEW_COUNTS.considered, matched: PREVIEW_COUNTS.matched, field_present: PREVIEW_COUNTS.field_present }
      const samples = previewSamples()
      if (request.url.searchParams.get('detail') !== 'true') {
        return { ...old, sample: samples.map(({ run_id, build_number, branch, environment }) => ({ run_id, build_number, branch, environment })) }
      }
      const wanted = (body.target_release_name ?? '').trim().toLowerCase()
      return {
        ...old,
        sample: samples,
        shadowed: PREVIEW_COUNTS.shadowed,
        explicit: PREVIEW_COUNTS.explicit,
        target_exists: releases.world.rows.some((r) => r.name.toLowerCase() === wanted),
        unattributed_recent: PREVIEW_COUNTS.unattributed_recent,
      }
    }, 'POST'],
    [RULES_PATH, () => ({ items: sorted(), total: rules.length })],
    [RULES_PATH, (request) => {
      const body = (recordIn(writes, request) ?? {}) as Record<string, unknown>
      if (!ATTRIBUTION_FIELDS.fields.some((f) => f.value === body.match_field)) {
        return respond(422, { detail: "match_field must be one of ['branch', 'build_number', 'environment', 'tag'] — a value the evaluator cannot read would store cleanly and never match." })
      }
      const rule = attributionRule(
        RULE_IDS.created,
        String(body.name),
        String(body.match_field),
        String(body.match_pattern),
        String(body.target_release_name),
        Number(body.priority ?? 100),
        body.is_enabled !== false,
        NOW.toISOString(),
      )
      rules.push(rule)
      return respond(201, rule)
    }, 'POST'],
    [RULE, (request) => {
      const body = (recordIn(writes, request) ?? {}) as Partial<AttributionRuleFixture>
      const rule = ruleOf(request.path)
      if (!rule) return respond(404, { detail: 'Rule not found' })
      Object.assign(rule, body)
      return rule
    }, 'PUT'],
    [RULE, (request) => {
      recordIn(writes, request)
      const index = rules.findIndex((r) => r.id === request.path.split('/').pop())
      if (index < 0) return respond(404, { detail: 'Rule not found' })
      rules.splice(index, 1)
      return respond(204)
    }, 'DELETE'],
    ...releases.handlers,
  ]
  return { handlers, writes, rules, previews }
}

// ── GATE-04: the readiness policy on /trends and /overview ────────────────────────────────────────────────

/** `ReadinessPolicy` (§1.1): Checkout's published project policy "Payments gate" v4, bands 92 / 97 / 99. */
export const PAYMENTS_POLICY = {
  id: 'bbbbbbbb-0000-4000-8000-0000000000c4',
  name: 'Payments gate',
  version: 4,
  level: 'project',
  bands: { orange_min: 92, yellow_min: 97, green_min: 99 },
} as const

const SUMMARY_PATH = '/api/v1/metrics/summary'

/** `base`'s handlers with the summary answer grown by `extra` (the summary is the one read `/trends` and `/overview` take the policy from). */
function withSummary(base: ApiHandlers, extra: Record<string, unknown>): ApiHandlers {
  return [[SUMMARY_PATH, (request) => ({ ...(delegate(base, request) as Record<string, unknown>), ...extra })], ...base]
}

/** `/trends` (`TRENDS_ON`) with the summary naming `PAYMENTS_POLICY`: GO line 97, No-Go zone below 92. */
export const trendsPolicyWorld = (): ApiHandlers => withSummary(TRENDS_ON, { readiness_policy: PAYMENTS_POLICY })

/** The hard cap the Overview spec forces No-Go with (`release_readiness_downgrades`) and its sentence (§2.6). */
export const P0_DOWNGRADE = { token: 'p0_defects:2>0', text: 'Open critical (P0) defects: 2, cap 0' } as const

/** `/overview` (`OVERVIEW_ON`) with the summary naming `PAYMENTS_POLICY` and a P0 hard cap that forced No-Go. */
export const overviewPolicyWorld = (): ApiHandlers =>
  withSummary(OVERVIEW_ON, {
    readiness_policy: PAYMENTS_POLICY,
    release_readiness: 'RED',
    release_readiness_band: 'red',
    release_readiness_downgrades: [P0_DOWNGRADE.token],
  })

/** The Overview disclosure's policy sentence for `PAYMENTS_POLICY` (§2.6). */
export const DECIDED_BY = 'Decided by Payments gate v4 (project policy): Go at 97% or more, Conditional from 92%, No-Go below 92%.'

// ── GATE-02: policy versions, New version from this, Compare, the simulator's run picker ──────────────────

export const POLICY_IDS = {
  v3: 'bbbbbbbb-0000-4000-8000-0000000000d3',
  v4: 'bbbbbbbb-0000-4000-8000-0000000000d4',
  v5: 'bbbbbbbb-0000-4000-8000-0000000000d5',
} as const

/** A policy document (`PolicyDocument`), GO threshold `go`. */
export function policyDocument(go: number, yellow = 95) {
  return {
    schema_version: 1,
    thresholds: { go_threshold: go, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7 },
    dimension_weights: {
      user_impact: 0.25, env_sensitivity: 0.1, reproducibility: 0.15, regression_likely: 0.2,
      hist_recurrence: 0.1, blast_radius: 0.15, diagnosis_conf: 0.05,
    },
    rules: [
      { id: 'flaky-1', name: 'Flaky Recurrence Limit', type: 'flaky_recurrence', enabled: true, params: { max_flaky_tests: 10, action: 'BLOCK' } },
      { id: 'defects-1', name: 'Open Defect Limit', type: 'open_defect_limit', enabled: true, params: { max_open_defects: 5, action: 'WARN' } },
    ],
    pass_rate_bands: { orange_min: 90, yellow_min: yellow, green_min: 99 },
    hard_caps: { max_p0_defects: 0, max_flaky_count: 10, max_new_failures_24h: 20 },
    kind_rules: { enabled: false },
  }
}

const PRIYA = { id: '00000000-0000-4000-8000-0000000000b1', name: 'priya' }

/** A `ReleaseGatePolicyResponse` row; the two history routes add `created_by_name` / `activated_by_name` (§3.2). */
export function policyVersion(id: string, version: number, state: 'draft' | 'published' | 'active', extra: Record<string, unknown> = {}) {
  return {
    id,
    project_id: P as string | null,
    version,
    name: 'Payments gate',
    description: 'Nightly release gate for Checkout payments',
    rules: policyDocument(20, version >= 4 ? 97 : 95),
    is_active: state === 'active',
    is_draft: state === 'draft',
    created_by: PRIYA.id as string | null,
    activated_by: state === 'draft' ? null : (PRIYA.id as string | null),
    activated_at: state === 'draft' ? null : isoAgo(30 - version * 5),
    created_at: isoAgo(31 - version * 5),
    updated_at: isoAgo(30 - version * 5),
    ...extra,
  }
}

export type PolicyVersionFixture = ReturnType<typeof policyVersion>

/** The simulator's answer (`SimulateResponse`). */
export const SIMULATION = {
  original_recommendation: 'NO_GO',
  simulated_recommendation: 'CONDITIONAL_GO',
  original_composite: 61.2,
  simulated_composite: 48.4,
  rule_evaluations: [
    { rule_id: 'flaky-1', rule_name: 'Flaky Recurrence Limit', rule_type: 'flaky_recurrence', passed: true, action: 'BLOCK', message: '3 flaky ≤ 10', actual_value: 3, threshold_value: 10 },
  ],
  diff_summary: 'A GO threshold of 25 turns this NO_GO into a CONDITIONAL_GO.',
}

/**
 * The GATE-02 reads and writes over a list of policy rows (`rows`, fresh per world): the project's history newest
 * first with the creators' names (`GET /history/{project_id}`; `/system-history` for the system default), "New
 * version from this" (the existing create: a draft of the next version), Save (PATCH a draft; a published one answers
 * the server's 409), the policy read, the simulator (recorded in `simulated`), and the run picker's search over the
 * policy's project (`GET /runs?project_id=…&q=&page=&size=20&days=0`) with its selected-run label (`GET /runs/{id}`).
 * Composed by `policyVersionsWorld()` and by `policy-editor.spec.ts`' own handler set.
 */
export function policyHistoryHandlers(rows: PolicyVersionFixture[], writes: RecordedWrite[], simulated: unknown[]): ApiHandlers {
  const LIST = '/api/v1/release-gate-policies'
  const named = (r: PolicyVersionFixture) => ({
    ...r,
    created_by_name: r.created_by === PRIYA.id ? PRIYA.name : null,
    activated_by_name: r.activated_by === PRIYA.id ? PRIYA.name : null,
  })
  const newestFirst = (project: string | null) => rows.filter((r) => r.project_id === project).sort((a, b) => b.version - a.version).map(named)
  return [
    [`${LIST}/simulate`, (request) => {
      simulated.push(bodyOf(request))
      return SIMULATION
    }, 'POST'],
    [new RegExp(`^${LIST}/history/[^/]+$`), (request) => newestFirst(request.path.split('/').pop() ?? null)],
    [`${LIST}/system-history`, () => newestFirst(null)],
    [LIST, (request) => {
      const body = (recordIn(writes, request) ?? {}) as { project_id?: string | null; name: string; description?: string; rules?: unknown }
      const scope = body.project_id ?? null
      const next = Math.max(0, ...rows.filter((r) => r.project_id === scope).map((r) => r.version)) + 1
      const row = policyVersion(next === 5 ? POLICY_IDS.v5 : `bbbbbbbb-0000-4000-8000-0000000000e${next}`, next, 'draft', {
        project_id: scope,
        name: body.name,
        description: body.description ?? null,
        rules: body.rules ?? {},
        created_at: NOW.toISOString(),
        updated_at: NOW.toISOString(),
      })
      rows.push(row)
      return respond(201, row)
    }, 'POST'],
    [new RegExp(`^${LIST}/[0-9a-f-]{36}$`), (request) => {
      const body = (recordIn(writes, request) ?? {}) as Partial<PolicyVersionFixture>
      const row = rows.find((r) => r.id === request.path.split('/').pop())
      if (!row) return respond(404, { detail: 'Policy not found' })
      if (!row.is_draft) return respond(409, { detail: 'Cannot edit a published policy. Create a new version instead.' })
      Object.assign(row, body, { updated_at: NOW.toISOString() })
      return row
    }, 'PATCH'],
    [new RegExp(`^${LIST}/[0-9a-f-]{36}$`), (request) => rows.find((r) => r.id === request.path.split('/').pop()) ?? respond(404, { detail: 'Policy not found' })],
    [LIST, () => rows],
    // The run picker (`RunPicker` with the policy's project): the runs search and the selected run's label.
    [/^\/api\/v1\/runs\/[0-9a-f-]{36}$/, (request) => runs().find((r) => r.id === request.path.split('/').pop()) ?? respond(404, { detail: 'Run not found' })],
    ...RUNS,
  ]
}

/** `/policies/:id` for Checkout's "Payments gate": v3 published, v4 active (the one opened), then whatever the spec creates. */
export function policyVersionsWorld() {
  const writes: RecordedWrite[] = []
  const simulated: unknown[] = []
  const rows = [policyVersion(POLICY_IDS.v3, 3, 'published', { is_active: false }), policyVersion(POLICY_IDS.v4, 4, 'active')]
  const handlers: ApiHandlers = [...policyHistoryHandlers(rows, writes, simulated), ...LAYOUT]
  return { handlers, writes, simulated, rows }
}

// ── GATE-08b: the digest preview's release verdict ────────────────────────────────────────────────────────

/** `DigestReleaseVerdict` (§1.1): 2026.11, the project's active release, its current release-level decision No-Go with two reasons. */
export const RELEASE_VERDICT = {
  release_id: RELEASE_ACTIVE_ID,
  release_name: '2026.11',
  source: 'active',
  verdict: 'NO_GO',
  decided_at: isoAgo(0, 2),
  blocker_count: 2,
} as const

/** The line's start, verbatim (§1.2 `release_verdict_line`); the page's decided-at words are its own. */
export const RELEASE_VERDICT_LINE = 'Release 2026.11 — No-Go (2 blockers)'

/** The W09 digests world (`digestsWorld()`) whose preview also carries `release_verdict` (§2.8). */
export function digestsReleaseVerdictWorld() {
  const base = digestsWorld()
  const previews: string[] = []
  const handlers: ApiHandlers = [
    [DIGEST_PREVIEW_PATH, (request) => {
      previews.push(targetOf(request))
      const answer = delegate(base.handlers, request)
      // A refusal (the saved view's 422) stays the refusal: only a preview body grows.
      if (answer instanceof ApiResponse || !answer || typeof answer !== 'object') return answer
      return { ...(answer as Record<string, unknown>), release_verdict: { ...RELEASE_VERDICT } }
    }],
    ...base.handlers,
  ]
  return { handlers, state: base.state, previews }
}

// ── GATE-08a: /release-gate follows the top-bar release ───────────────────────────────────────────────────

/** 2026.11's two release-gate reasons (the strip counts them). */
export const ACTIVE_GATE_REASONS = ['test_pay (checkout-e2e) is FAILED', 'Open P0 defect CHK-412: card declined on retry']

/** 2026.11's newest run, still running (the auto-navigation passes it), and its newest FINISHED run. */
export const RELEASE_RUNS = {
  running: '33333333-3333-4333-8333-0000000011a1',
  finished: '33333333-3333-4333-8333-0000000011a2',
} as const

/** The strip's text for 2026.11 (§2.9), from its start; the decided-at words follow. */
export const STRIP_TEXT = 'Release 2026.11 (top bar) · release gate: No-Go · 2 blockers'

/**
 * `/release-gate/<run>` (`releaseGateOn()`, its floored NO_GO decision for any run) over the W05 release list, with
 * 2026.11's recorded gate carrying two reasons. `GET /runs` answers by its `release_id`: for 2026.11 its two runs
 * (the newest still running, then a finished one), otherwise the fixture's runs; `GET /runs/{id}` names the run's
 * release. `GET /releases/{id}/gate` is the W05 world's (404 for a release never evaluated: 2026.12).
 */
export function releaseGateFollowsWorld() {
  const world = releasesWorld()
  const active = world.rows.find((r) => r.id === RELEASE_ACTIVE_ID)
  if (active?.gate) active.gate = gateSummary({ ...active.gate, verdict: 'NO_GO', decided_at: isoAgo(0, 2) }, ACTIVE_GATE_REASONS)
  world.reasons[RELEASE_ACTIVE_ID] = [...ACTIVE_GATE_REASONS]
  const fixtureRuns = runs()
  const releaseRuns = [
    { ...fixtureRuns[1], id: RELEASE_RUNS.running, status: 'RUNNING', build_number: 251, run_seq: 71, created_at: isoAgo(0, 1), release_name: '2026.11' },
    { ...fixtureRuns[2], id: RELEASE_RUNS.finished, status: 'FAILED', build_number: 250, run_seq: 70, created_at: isoAgo(0, 4), release_name: '2026.11' },
  ]
  const runRelease = (id: string) => (releaseRuns.some((r) => r.id === id) ? { release_id: RELEASE_ACTIVE_ID, release_name: '2026.11' } : { release_id: null, release_name: null })
  const runsSeen: string[] = []
  const reads = releasesReadHandlers(world).filter(([matcher]) => matcher !== '/api/v1/compliance-packs')
  const handlers: ApiHandlers = [
    ['/api/v1/runs', (request) => {
      runsSeen.push(targetOf(request))
      if (request.url.searchParams.get('release_id') === RELEASE_ACTIVE_ID) {
        return { items: releaseRuns, total: releaseRuns.length, page: 1, size: 10, pages: 1 }
      }
      return delegate(RUNS, request)
    }],
    [/^\/api\/v1\/runs\/[0-9a-f-]{36}$/, (request) => {
      const id = request.path.split('/').pop() as string
      const run = [...releaseRuns, ...fixtureRuns].find((r) => r.id === id)
      if (!run) return respond(404, { detail: 'Run not found' })
      return { ...run, ...runRelease(id) }
    }],
    ...reads,
    ...releaseGateOn(),
  ]
  return { handlers, world, runsSeen }
}

/** The run the gate opens first (no release selected), the fixtures' newest build. */
export const GATE_RUN_A = RUN_ID
