/**
 * W06 "defects" hermetic answers (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §2.1-§2.7, §4.6): the
 * `/defects` world of `fixtures.ts` (`defectRows`, `DEFECTS`) plus what the W06 flows read and write, each write
 * changing the world so the reads that follow show it:
 *
 *  - `PATCH /projects/{project}/defects/{id}` changes the status, the assignee, the team or the Jira link the way
 *    the server does OFFLINE (the demonstrated default): a key is saved locally and linked to the configured Jira
 *    domain, and a linked defect's status change is not written back (DEF-01);
 *  - the project's members (the assignee select) and the Jira metadata (offline: the link is not checked);
 *  - the release scope: with `release_match=affects` the list, the trend and its totals cover only the defects that
 *    affect the selected release (DEF-05), and the list's `meta` names the release (the "Affecting …" chip);
 *  - the trend and the escape rate, the latter measured or below its floor (DEF-04 / DEF-03).
 *
 * Every factory returns fresh state per call (a write in one test never leaks into the next) and records the
 * writes it answered (`writes`), so a spec asserts the body, not only that "something was called".
 * Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  DEFECTS,
  defectEscapeBody,
  defectList,
  defectMeta,
  defectRows,
  defectTrendBody,
  DEFECT_ID,
  isActiveDefect,
  NOW,
  PROJECT_ID,
  RELEASE_ID,
  RELEASES,
  trendTotalsOf,
  USER,
  type DefectRowFixture,
} from './fixtures'
import { projectMembers, RELEASE_PEOPLE } from './fixtures-pages'
import { QA_ENGINEER, VIEWER } from './fixtures-releases'

/** The roles the triage menu is for (QA engineer and above: `QA_ENGINEER`), and two it is not for. */
export { DEFECT_ID, QA_ENGINEER, RELEASE_PEOPLE, VIEWER }
export const TESTER = { ...USER, id: '00000000-0000-4000-8000-0000000000e3', username: 'tester', full_name: 'Tester', role: 'TESTER' }

/** The configured Jira domain the server builds a locally saved key's link with (`jira_browse_url`). */
export const JIRA_DOMAIN = 'jira.example.test'

/** The release the scope specs pick in the top bar (`2026.09`, shipped 10 days ago). */
export const SCOPE_RELEASE = { id: RELEASE_ID.current, name: '2026.09', status: 'released' } as const

/** The rows that affect `SCOPE_RELEASE` (asserted impact, else found in it): an open P0 and an open P1. */
export const AFFECTING_SCOPE_RELEASE: readonly string[] = [DEFECT_ID.checkoutTotal, DEFECT_ID.loginRejects]

/** The server's refusal texts (contract §1.1, §1.3), verbatim. */
export const STATUS_INVALID_DETAIL = 'resolution_status must be one of OPEN, IN_PROGRESS, RESOLVED, INVALID.'
export const ASSIGNEE_INELIGIBLE = 'That user cannot be assigned defects in this project.'
export const JIRA_KEY_INVALID = 'Not a Jira issue key (expected something like ABC-123).'

/** One answered write: `METHOD path` and its JSON body. */
export interface DefectWrite {
  line: string
  body: unknown
}

export interface DefectsWorldOptions {
  /** The rows to start from (default: `defectRows()`, two open P0s). */
  rows?: DefectRowFixture[]
  /** The escape rate below its 5-defect floor ("Not measured" with the server's reason). */
  escapeBelowFloor?: boolean
  /** The top bar lists the fixture's releases (`RELEASES`), so one can be picked (default: no release). */
  releases?: boolean
}

export interface DefectsWorld {
  handlers: ApiHandlers
  /** The live rows the reads answer from. */
  rows: DefectRowFixture[]
  /** Every write answered, in order. */
  writes: DefectWrite[]
}

/** The base rows with the two open P0s resolved: the banner then states "Net this week" (it shows the oldest open P0 while one is open). */
export function rowsWithoutOpenP0(): DefectRowFixture[] {
  return defectRows().map((row) =>
    row.priority === 'P0' && isActiveDefect(row) ? { ...row, resolution_status: 'RESOLVED', resolved_at: NOW.toISOString(), jira_status: row.jira_ticket_id ? 'Done' : null } : row,
  )
}

const bodyOf = (request: ApiRequest): Record<string, unknown> | null => {
  try {
    return request.route.request().postDataJSON() as Record<string, unknown> | null
  } catch {
    return null
  }
}

const intParam = (request: ApiRequest, name: string, fallback: number) => {
  const n = Number(request.url.searchParams.get(name) ?? NaN)
  return Number.isFinite(n) ? n : fallback
}

/** The request narrows to the defects that affect `SCOPE_RELEASE` (the "Only this release" toggle). */
const affectsScope = (request: ApiRequest) =>
  request.url.searchParams.get('release_match') === 'affects' && request.url.searchParams.getAll('release_id').includes(SCOPE_RELEASE.id)

const ACTIVE = new Set(['OPEN', 'IN_PROGRESS'])
const STATUSES = new Set(['OPEN', 'IN_PROGRESS', 'RESOLVED', 'INVALID'])
const JIRA_KEY = /^[A-Z][A-Z0-9_]+-[0-9]+$/
const PATCH_FIELDS = new Set(['resolution_status', 'assignee_user_id', 'owner_team', 'jira_key', 'note'])

/** A member the server lets own a defect: a person, active (the W05 owner rule). */
function eligibleMember(userId: string) {
  return projectMembers().find((m) => m.user_id === userId && m.is_active && !m.is_synthetic && !m.is_service_account)
}

/**
 * `PATCH /projects/{project}/defects/{id}` (contract §2.2) as an OFFLINE install answers it: only the fields
 * sent are applied; a key is saved locally (`saved_locally / offline_mode`) with the server-built link; a status
 * change of a linked defect is not written back (`not_sent / offline_mode`).
 */
function patchDefect(rows: DefectRowFixture[], request: ApiRequest, body: Record<string, unknown> | null) {
  const [, , , , projectId, , defectId] = request.path.split('/')
  const row = rows.find((r) => r.id === defectId && r.project_id === projectId)
  if (!row) return respond(404, { detail: 'Defect not found in this project.' })
  const sent = Object.keys(body ?? {})
  const extra = sent.filter((key) => !PATCH_FIELDS.has(key))
  if (extra.length > 0) return respond(422, { detail: [{ loc: ['body', extra[0]], msg: 'Extra inputs are not permitted', type: 'extra_forbidden' }] })
  const fields = sent.filter((key) => key !== 'note')
  if (fields.length === 0) return respond(422, { detail: 'Nothing to change.' })
  const patch = body as Record<string, string | null>
  const next = { ...row }
  const changed: string[] = []
  let jira: Record<string, unknown> | null = null
  let statusChanged = false

  if ('resolution_status' in patch) {
    const target = String(patch.resolution_status ?? '').trim().toUpperCase()
    if (!STATUSES.has(target)) return respond(422, { detail: STATUS_INVALID_DETAIL })
    if (target !== row.resolution_status) {
      next.resolution_status = target
      next.resolved_at = ACTIVE.has(target) ? null : row.resolved_at ?? NOW.toISOString()
      changed.push('resolution_status')
      statusChanged = true
    }
  }
  if ('assignee_user_id' in patch) {
    const value = patch.assignee_user_id
    if (value === null || value === '') {
      if (row.assignee_user_id) changed.push('assignee')
      next.assignee_user_id = null
      next.assignee_name = null
    } else {
      const member = eligibleMember(String(value))
      if (!member) return respond(422, { detail: ASSIGNEE_INELIGIBLE })
      if (member.user_id !== row.assignee_user_id) changed.push('assignee')
      next.assignee_user_id = member.user_id
      next.assignee_name = member.full_name ?? member.username
    }
  }
  if ('owner_team' in patch) {
    const team = String(patch.owner_team ?? '').trim()
    const cleared = team === '' || team.toLowerCase() === 'unknown'
    if ((cleared ? null : team) !== row.owner_team) changed.push('owner_team')
    next.owner_team = cleared ? null : team
    next.owner_source = cleared ? null : 'defect'
  }
  if ('jira_key' in patch) {
    const raw = patch.jira_key
    if (raw === null || raw === '') {
      if (row.jira_ticket_id) changed.push('jira')
      Object.assign(next, { jira_ticket_id: null, jira_ticket_url: null, jira_status: null, external_status_at: null, external_status_conflict: false })
      jira = { status: 'unlinked', reason: null, jira_key: null, jira_url: null, summary: null }
    } else {
      const key = String(raw).trim().toUpperCase()
      if (!JIRA_KEY.test(key) || key.length > 50) return respond(422, { detail: JIRA_KEY_INVALID })
      const url = `https://${JIRA_DOMAIN}/browse/${key}`
      changed.push('jira')
      Object.assign(next, { jira_ticket_id: key, jira_ticket_url: url, jira_status: null, jira_filing_status: null, jira_filing_reason: null })
      jira = { status: 'saved_locally', reason: 'offline_mode', jira_key: key, jira_url: url, summary: null }
    }
  }
  rows[rows.indexOf(row)] = next
  const writeBack = statusChanged && next.jira_ticket_id ? { status: 'not_sent', reason: 'offline_mode' } : null
  return { defect: next, changed_fields: changed, jira, write_back: writeBack }
}

/** Jira as an offline install reports it: not reachable, so a pasted key is saved without a check. */
const JIRA_METADATA_OFFLINE = {
  available: false,
  reason: 'offline_mode',
  projects: [],
  issue_types: [],
  default_project_key: null,
  webhook_available: false,
}

/**
 * The W06 `/defects` world with its writes. `rows` is the live state (the list, the trend's totals and the
 * PATCH answer from it); `writes` lists every write answered, in order.
 */
export function defectsWorld(options: DefectsWorldOptions = {}): DefectsWorld {
  const rows = options.rows ?? defectRows()
  const writes: DefectWrite[] = []
  const inScope = (request: ApiRequest) => (affectsScope(request) ? rows.filter((r) => AFFECTING_SCOPE_RELEASE.includes(r.id)) : rows)

  const handlers: ApiHandlers = [
    [
      '/api/v1/analytics/defects',
      (request) => defectList(inScope(request), affectsScope(request) ? defectMeta(null, [{ ...SCOPE_RELEASE }]) : undefined),
    ],
    ['/api/v1/analytics/defects/trend', (request) => defectTrendBody(intParam(request, 'days', 30), trendTotalsOf(inScope(request)))],
    ['/api/v1/analytics/defects/escape-rate', (request) => defectEscapeBody(intParam(request, 'days', 30), !options.escapeBelowFloor)],
    [
      /^\/api\/v1\/projects\/[^/]+\/defects\/[^/]+$/,
      (request) => {
        const body = bodyOf(request)
        writes.push({ line: `PATCH ${request.path}`, body })
        return patchDefect(rows, request, body)
      },
      'PATCH',
    ],
    [`/api/v1/projects/${PROJECT_ID}/members`, () => projectMembers()],
    [/^\/api\/v1\/projects\/[^/]+\/defects\/jira\/metadata$/, () => JIRA_METADATA_OFFLINE],
    ...(options.releases
      ? ([['/api/v1/releases', () => ({ items: RELEASES, total: RELEASES.length, page: 1, size: 100, pages: 1 })]] as ApiHandlers)
      : []),
    ...DEFECTS,
  ]
  return { handlers, rows, writes }
}
