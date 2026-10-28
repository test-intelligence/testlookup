/**
 * W21d ACT-02 hermetic world (`docs/deferred-scope-2026-10-13/work/W21d-contract.md` §6.2 G-A): the activity live
 * tail, Inbox › Activity and test-case mentions, for G-C's and G-D's specs (`tests/ci-e2e/activity-live.spec.ts`,
 * `inbox-activity.spec.ts`, `test-case-mentions.spec.ts`) and visual specs.
 *
 * `activityLiveWorld({ role, projectId, projects, feed, eventTypes, mine, stream, members, comments, writes, base })`
 * answers, with the REAL server shapes, and lists its own handlers FIRST (first match wins) in front of `base`
 * (default `LAYOUT`; pass `inboxHandlers(writes)` for `/my-failures`, `testCaseEditingWorld(...).handlers` for
 * `/test-management`):
 *
 *  - `GET /api/v1/activity/event-types` — `{categories, actor_types, entity_types, events}`;
 *  - `GET /api/v1/projects/{id}/activity` — `{items, next_cursor, ledger_started_at, window}` (the world's feed rows
 *    of that project, filtered by `category` / `actor_type` / `q`, newest first); another project → the guard's 403;
 *  - `GET /api/v1/activity/mine` — `{items, total, next_cursor, window, view, projects}`, filtered by `view` /
 *    `project_id` / `since` from the world's rows, paged by `limit` + an opaque cursor; `total` ignores the cursor;
 *    a `project_id` the user is no member of → 403 (an ADMIN reads every project);
 *  - `GET /api/v1/projects/{id}/activity/stream` — the FIRST request answers ONE `text/event-stream` body (the
 *    world's frames: `retry`, `ready`, then each `activity` / `refresh` / `bye`) that then ENDS (Playwright cannot
 *    hold a response open); a 2nd and later request answers `stream.after` — by default 503 `live_unavailable`, so a
 *    reconnecting client lands on "unavailable" and backs off: no spec spins. `stream.refuse` refuses the FIRST
 *    request instead (`{detail: {code, message}}`, or the guard's plain string). Every request's `Authorization`,
 *    `Accept` and `Last-Event-ID` headers and its full URL are recorded in `streamRequests`. A streamed row is also
 *    in the feed's next page-0 read (it committed);
 *  - `GET /api/v1/projects/{id}/members` — a BARE list of `ProjectMemberResponse` (default `tmMembers()`: active
 *    members plus a synthetic, a service account and an inactive one);
 *  - `GET /api/v1/test-management/cases/{id}/comments` — a BARE list, each with `mentions: []`;
 *  - `POST /api/v1/test-management/cases/{id}/comments` — records `{line, body}` in `writes` and answers 201 with the
 *    GROWN response: `mentions` resolved like the server (the longest active, non-synthetic, non-service member
 *    username at each `@`, never the author, at most 10); a `parent_id` not on that case → 404
 *    `Parent comment not found on this test case.`; a VIEWER → 403 `Insufficient permissions`.
 *
 * Everything else falls through to `base`, and what `base` does not answer fails closed (`production-pages.ts`).
 * Rendered text is data; no id here looks like an e-mail, a phone or a card number. Not a spec file.
 */
import { respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { STREAM_ERROR_SENTENCES, type StreamErrorCode } from '../../../src/types/activityLive'
import { isoAgo, LAYOUT, NOW, PROJECT_ID, PROJECTS, USER } from './fixtures'
import { bodyOf, type RecordedWrite } from './fixtures-in-page'
import { tmMembers } from './fixtures-in-page-b'
import { RELEASE_PEOPLE } from './fixtures-pages'

export type { RecordedWrite }

/** A second project the signed-in user is a member of (Inbox › Activity in All Projects names it). */
export const SECOND_PROJECT = {
  id: '21212121-2121-4121-8121-212121212121',
  name: 'Payments',
  slug: 'payments',
  description: '',
  is_active: true,
  created_at: '2026-02-02T09:00:00Z',
}
/** The world's default projects: Checkout (`PROJECT_ID`) and Payments. */
export const LIVE_PROJECTS = [...PROJECTS, SECOND_PROJECT]

/** The test case the default rows and comments are about (the mention spec opens it). */
export const LIVE_CASE = { id: 'c2000000-0000-4000-8000-0000000000a1', title: 'Login works' }
/** The case's existing comment (Priya's); a reply's `parent_id` must name it. */
export const LIVE_COMMENT_ID = 'c3000000-0000-4000-8000-000000000001'
/** A defect assigned to the signed-in user in Payments (the "assigned" Inbox row). */
export const LIVE_DEFECT = { id: 'd2000000-0000-4000-8000-0000000000b1', title: 'Card form loses the postcode' }

/** The guard's own refusal (a string detail, unchanged by W21d). */
export const NO_ACCESS = 'You do not have access to this project'
export const PARENT_NOT_FOUND = 'Parent comment not found on this test case.'

type Row = Record<string, unknown>
/** One feed item exactly as `activity.query.serialize(row)` answers it (the stream sends the same bytes). */
export type ActivityRow = Row & { id: string; project_id: string; occurred_at: string | null; category: string; summary: string }
/** One `/activity/mine` item: the feed item plus why it is listed. */
export type MineRow = ActivityRow & { involvement: string }

/** A feed item with every field the server sends; `over` replaces any of them. */
export function activityRow(over: Partial<ActivityRow> & { id: string }): ActivityRow {
  const entityId = (over.entity as { id?: string } | undefined)?.id ?? LIVE_CASE.id
  return {
    project_id: PROJECT_ID,
    release_id: null,
    occurred_at: isoAgo(0, 1),
    category: 'test_management',
    event_type: 'test_case.commented',
    actor: { type: 'user', id: RELEASE_PEOPLE.priya.id, name: RELEASE_PEOPLE.priya.name, ref: null },
    entity: { type: 'test_case', id: entityId, label: LIVE_CASE.title, href: `/test-management?case=${entityId}` },
    target: null,
    summary: `${RELEASE_PEOPLE.priya.name} commented on test case ${LIVE_CASE.title}`,
    context: { comment_id: LIVE_COMMENT_ID, mentions: [], mention_count: 0, step_number: null },
    has_diff: false,
    source: { table: 'test_case_comments', id: LIVE_COMMENT_ID },
    group_key: null,
    ...over,
  }
}

export function mineRow(over: Partial<MineRow> & { id: string }): MineRow {
  return { ...activityRow(over), involvement: over.involvement ?? 'actor' }
}

/** One SSE frame of the stream body; `data` that is not a string is JSON-encoded. `comment` writes a `:` line. */
export interface LiveFrame {
  event?: string
  id?: string
  data?: unknown
  retry?: number
  comment?: string
}

export function encodeFrames(frames: readonly LiveFrame[]): string {
  return frames
    .map((frame) => {
      if (frame.comment !== undefined) return `: ${frame.comment}\n\n`
      const lines: string[] = []
      if (frame.retry !== undefined) lines.push(`retry: ${frame.retry}`)
      if (frame.event !== undefined) lines.push(`event: ${frame.event}`)
      if (frame.id !== undefined) lines.push(`id: ${frame.id}`)
      if (frame.data !== undefined) {
        const text = typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data)
        for (const line of text.split('\n')) lines.push(`data: ${line}`)
      }
      return `${lines.join('\n')}\n\n`
    })
    .join('')
}

/** A Redis stream id `n` ms after the world's pinned clock (`NOW`), e.g. `1758196800001-0`. */
export const streamId = (n: number): string => `${NOW.getTime() + n}-0`

// ── The default rows ───────────────────────────────────────────────────────

const RELEASE = { id: 'e2000000-0000-4000-8000-0000000000c1', label: 'R-2026.10' }
const SAVED_VIEW = { id: 'f2000000-0000-4000-8000-0000000000d1', label: 'Flaky this week' }
const QA_LEAD_ACTOR = { type: 'user', id: USER.id, name: USER.full_name, ref: null }

/** Checkout's feed, newest first: Priya's comment, the lead's saved view, a release phase. */
export function defaultFeed(): ActivityRow[] {
  return [
    activityRow({ id: 'a2000000-0000-4000-8000-000000000001' }),
    activityRow({
      id: 'a2000000-0000-4000-8000-000000000002',
      occurred_at: isoAgo(0, 4),
      category: 'configuration',
      event_type: 'saved_view.created',
      actor: QA_LEAD_ACTOR,
      entity: { type: 'saved_view', id: SAVED_VIEW.id, label: SAVED_VIEW.label, href: '/search' },
      summary: `Saved view ${SAVED_VIEW.label} was created`,
      context: null,
      source: null,
    }),
    activityRow({
      id: 'a2000000-0000-4000-8000-000000000003',
      occurred_at: isoAgo(1),
      category: 'release',
      event_type: 'release.phase_advanced',
      release_id: RELEASE.id,
      actor: QA_LEAD_ACTOR,
      entity: { type: 'release', id: RELEASE.id, label: RELEASE.label, href: `/releases/${RELEASE.id}` },
      summary: `Release ${RELEASE.label} advanced to Regression`,
      context: { phase: 'Regression' },
      source: null,
    }),
  ]
}

/** The row the default stream delivers: Dana's comment, a minute before the pinned clock. */
export function defaultStreamedRow(): ActivityRow {
  return activityRow({
    id: 'a2000000-0000-4000-8000-000000000010',
    occurred_at: new Date(NOW.getTime() - 60_000).toISOString(),
    actor: { type: 'user', id: RELEASE_PEOPLE.dana.id, name: RELEASE_PEOPLE.dana.name, ref: null },
    summary: `${RELEASE_PEOPLE.dana.name} commented on test case ${LIVE_CASE.title}`,
    context: { comment_id: 'c3000000-0000-4000-8000-000000000010', mentions: [], mention_count: 0, step_number: null },
    source: { table: 'test_case_comments', id: 'c3000000-0000-4000-8000-000000000010' },
  })
}

/** The QA lead's Inbox › Activity rows, one per involvement, across both projects, newest first. */
export function defaultMine(): MineRow[] {
  return [
    mineRow({
      id: 'b2000000-0000-4000-8000-000000000001',
      involvement: 'mentioned',
      context: { comment_id: LIVE_COMMENT_ID, mentions: [USER.id], mention_count: 1, step_number: null },
    }),
    mineRow({
      id: 'b2000000-0000-4000-8000-000000000002',
      involvement: 'actor',
      occurred_at: isoAgo(0, 4),
      category: 'configuration',
      event_type: 'saved_view.created',
      actor: QA_LEAD_ACTOR,
      entity: { type: 'saved_view', id: SAVED_VIEW.id, label: SAVED_VIEW.label, href: '/search' },
      summary: `Saved view ${SAVED_VIEW.label} was created`,
      context: null,
      source: null,
    }),
    mineRow({
      id: 'b2000000-0000-4000-8000-000000000003',
      involvement: 'assigned',
      project_id: SECOND_PROJECT.id,
      occurred_at: isoAgo(1, 2),
      category: 'quality',
      event_type: 'defect.updated',
      actor: { type: 'user', id: RELEASE_PEOPLE.dana.id, name: RELEASE_PEOPLE.dana.name, ref: null },
      entity: { type: 'defect', id: LIVE_DEFECT.id, label: LIVE_DEFECT.title, href: `/defects?defect=${LIVE_DEFECT.id}` },
      summary: `Defect ${LIVE_DEFECT.title} changed: severity`,
      context: { changed: 'severity' },
      has_diff: true,
      source: null,
    }),
    mineRow({
      id: 'b2000000-0000-4000-8000-000000000004',
      involvement: 'authored',
      occurred_at: isoAgo(2),
      event_type: 'test_case.updated',
      // the lifecycle rows carry the spec's entity type `test` (contract F-1)
      entity: { type: 'test', id: LIVE_CASE.id, label: LIVE_CASE.title, href: `/test-management?test=${LIVE_CASE.id}` },
      summary: `Test case ${LIVE_CASE.title} changed: steps`,
      context: { changed: 'steps' },
      has_diff: true,
      source: { table: 'test_case_audit_logs', id: 'c4000000-0000-4000-8000-000000000001' },
    }),
  ]
}

const CATEGORIES = ['runs', 'analysis', 'release', 'quality', 'configuration', 'membership', 'test_management', 'integration', 'agent', 'system']
const SOME_EVENTS: Record<string, { event_type: string; entity_type: string }[]> = {
  test_management: [{ event_type: 'test_case.commented', entity_type: 'test_case' }, { event_type: 'test_case.updated', entity_type: 'test' }],
  configuration: [{ event_type: 'saved_view.created', entity_type: 'saved_view' }],
  release: [{ event_type: 'release.phase_advanced', entity_type: 'release' }],
  quality: [{ event_type: 'defect.updated', entity_type: 'defect' }],
}

/** The real `GET /activity/event-types` vocabulary (every category, as the filter select sizes to it). */
export const EVENT_TYPES = {
  categories: CATEGORIES,
  actor_types: ['user', 'api_key', 'service_account', 'system', 'agent'],
  entity_types: ['run', 'test', 'suite', 'release', 'defect', 'project', 'member', 'saved_view', 'test_case', 'test_library'],
  // the server groups the registry under EVERY category (an empty list included)
  events: Object.fromEntries(CATEGORIES.map((category) => [category, SOME_EVENTS[category] ?? []])),
}

/** Priya's comment on `LIVE_CASE` (the comments tab's existing row). */
export function defaultComments(): Row[] {
  return [{
    id: LIVE_COMMENT_ID,
    test_case_id: LIVE_CASE.id,
    author_id: RELEASE_PEOPLE.priya.id,
    content: 'Step 2 fails on a slow network.',
    comment_type: 'general',
    parent_id: null,
    step_number: 2,
    is_resolved: false,
    created_at: isoAgo(0, 1),
    updated_at: isoAgo(0, 1),
  }]
}

// ── The world ──────────────────────────────────────────────────────────────

/** What the stream route answers (§1.2). */
export interface LiveStreamScript {
  /** The FIRST request's frames, verbatim. Default: `retry`, `ready`, one `activity` per `rows` entry. */
  frames?: LiveFrame[]
  /** Rows the default frames stream (default `[defaultStreamedRow()]`); each also lands in the feed (it committed). */
  rows?: ActivityRow[]
  /** Refuse the FIRST request: the route's own `{code, message}` (a code) or the guard's plain string (`detail`). */
  refuse?: { status: number; code?: StreamErrorCode; detail?: string }
  /** The 2nd and later requests: `'unavailable'` (503 `live_unavailable`, default) or `'repeat'` the first answer. */
  after?: 'unavailable' | 'repeat'
}

export interface ActivityLiveWorldOptions {
  /** The signed-in user (default `USER`, the QA lead), or just a role on it; ADMIN reads every project. */
  role?: typeof USER | string
  /** The active project (default `PROJECT_ID`). */
  projectId?: string
  /** The user's projects, answered at `/api/v1/projects` too (default `LIVE_PROJECTS`). */
  projects?: Row[]
  /** Every project's feed rows (default `defaultFeed()`, all Checkout). */
  feed?: ActivityRow[]
  eventTypes?: unknown
  /** `/activity/mine`'s rows across projects (default `defaultMine()`). */
  mine?: MineRow[]
  stream?: LiveStreamScript
  /** The project's members, a BARE list (default `tmMembers()`). */
  members?: Row[]
  /** Comments across cases (default `defaultComments()`). */
  comments?: Row[]
  /** Where the comment POSTs are recorded (default a new list). */
  writes?: RecordedWrite[]
  /** The page's own world, answered AFTER this one (default `LAYOUT`). */
  base?: ApiHandlers
}

/** One stream request as the page sent it (headers lower-cased by Playwright). */
export interface StreamRequestRecord {
  url: string
  authorization: string | null
  accept: string | null
  lastEventId: string | null
}

const STREAM_PATH = /^\/api\/v1\/projects\/([^/]+)\/activity\/stream$/
const FEED_PATH = /^\/api\/v1\/projects\/([^/]+)\/activity$/
const MEMBERS_PATH = /^\/api\/v1\/projects\/([^/]+)\/members$/
const COMMENTS_PATH = /^\/api\/v1\/test-management\/cases\/([^/]+)\/comments$/
const UNKNOWN_VIEW = 'Unknown view. Valid: all, mine, involving.'
const MINE_VIEWS = ['all', 'mine', 'involving']
const BEFORE = 'A-Za-z0-9_.@\\-'
const AFTER = 'A-Za-z0-9_\\-'

const newestFirst = (a: ActivityRow, b: ActivityRow) =>
  String(b.occurred_at ?? '').localeCompare(String(a.occurred_at ?? '')) || b.id.localeCompare(a.id)

/** The server's mention rule (`services/activity/mentions.find_mentions`): longest username at each `@`, ≤ 10. */
export function resolveMentions(text: string, members: readonly Row[], authorId: string) {
  const eligible = members.filter((m) => m.is_active !== false && !m.is_synthetic && !m.is_service_account)
  const byLower = new Map(eligible.map((m) => [String(m.username).toLowerCase(), m]))
  if (!text.includes('@') || byLower.size === 0) return []
  const names = [...byLower.keys()].sort((a, b) => b.length - a.length || a.localeCompare(b))
  const escaped = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`(?<![${BEFORE}])@(${escaped.join('|')})(?![${AFTER}])`, 'gi')
  const found: Row[] = []
  for (const match of text.matchAll(pattern)) {
    const member = byLower.get(match[1].toLowerCase())
    if (!member || found.includes(member)) continue
    found.push(member)
    if (found.length >= 10) break
  }
  return found
    .filter((m) => m.user_id !== authorId)
    .map((m) => ({ user_id: m.user_id, username: m.username, display_name: m.full_name || m.username }))
}

/** The hermetic world (see the module docstring). Fresh state per call: nothing leaks between tests. */
export function activityLiveWorld(options: ActivityLiveWorldOptions = {}) {
  const user = typeof options.role === 'string' ? { ...USER, role: options.role } : (options.role ?? USER)
  const isAdmin = user.role === 'ADMIN'
  const projectId = options.projectId ?? PROJECT_ID
  const projects = options.projects ?? LIVE_PROJECTS
  const memberOf = new Set(projects.map((p) => String(p.id)))
  const reachable = (id: string) => isAdmin || memberOf.has(id)
  const feed: ActivityRow[] = [...(options.feed ?? defaultFeed())]
  const mine: MineRow[] = [...(options.mine ?? defaultMine())]
  const members = options.members ?? tmMembers()
  const comments: Row[] = [...(options.comments ?? defaultComments())]
  const writes: RecordedWrite[] = options.writes ?? []
  const streamRequests: StreamRequestRecord[] = []
  const script = options.stream ?? {}
  const streamedRows = script.rows ?? [defaultStreamedRow()]
  let posted = 0

  // GET /projects/{id}/activity: the project's rows, the feed's filters, newest first.
  const readFeed = ({ path, url }: ApiRequest) => {
    const id = FEED_PATH.exec(path)?.[1] ?? ''
    if (!reachable(id)) return respond(403, { detail: NO_ACCESS })
    const own = feed.filter((r) => r.project_id === id)
    const categories = url.searchParams.getAll('category')
    const actorType = url.searchParams.get('actor_type')
    const q = (url.searchParams.get('q') ?? '').trim().toLowerCase()
    const since = url.searchParams.get('since')
    const items = own
      .filter((r) => categories.length === 0 || categories.includes(r.category))
      .filter((r) => !actorType || (r.actor as { type?: string } | undefined)?.type === actorType)
      .filter((r) => q.length < 3 || r.summary.toLowerCase().includes(q))
      .filter((r) => !since || String(r.occurred_at ?? '') >= new Date(since).toISOString())
      .sort(newestFirst)
    const oldest = [...own].sort(newestFirst).pop()
    return {
      items,
      next_cursor: null,
      // ignores every filter: when this project's ledger begins
      ledger_started_at: oldest?.occurred_at ?? null,
      window: { since, until: url.searchParams.get('until') },
    }
  }

  // GET /activity/mine: view, pin, window; `total` ignores the cursor.
  const readMine = ({ url }: ApiRequest) => {
    const view = url.searchParams.get('view')?.trim() || 'all'
    if (!MINE_VIEWS.includes(view)) return respond(422, { detail: UNKNOWN_VIEW })
    const pin = url.searchParams.get('project_id')
    if (pin && !reachable(pin)) return respond(403, { detail: NO_ACCESS })
    const since = url.searchParams.get('since') ?? new Date(NOW.getTime() - 7 * 86_400_000).toISOString()
    const until = url.searchParams.get('until')
    const limit = Number(url.searchParams.get('limit') ?? 50)
    const rows = mine
      .filter((r) => (pin ? r.project_id === pin : reachable(r.project_id)))
      .filter((r) => view === 'all' || (view === 'mine' ? r.involvement === 'actor' : r.involvement !== 'actor'))
      .filter((r) => String(r.occurred_at ?? '') >= new Date(since).toISOString())
      .filter((r) => !until || String(r.occurred_at ?? '') <= new Date(until).toISOString())
      .sort(newestFirst)
    const cursor = url.searchParams.get('cursor')
    const start = cursor?.startsWith('w21d-mine-') ? Number(cursor.slice('w21d-mine-'.length)) || 0 : 0
    const items = rows.slice(start, start + limit)
    return {
      items,
      total: rows.length,
      next_cursor: start + limit < rows.length ? `w21d-mine-${start + limit}` : null,
      window: { since, until },
      view,
      projects: pin ? 1 : isAdmin ? null : memberOf.size,
    }
  }

  // The stream: ONE body that then ends; later requests answer `after` (default 503 live_unavailable).
  const streamFrames = (id: string): LiveFrame[] =>
    script.frames ?? [
      { retry: 5_000 },
      { event: 'ready', id: '0-0', data: { project_id: id, last_event_id: '0-0', heartbeat_seconds: 15, max_seconds: 900 } },
      ...streamedRows.map((row, n) => ({ event: 'activity', id: streamId(n + 1), data: row })),
    ]
  const refusal = (status: number, code: StreamErrorCode) => respond(status, { detail: { code, message: STREAM_ERROR_SENTENCES[code] } })
  const firstAnswer = (id: string) => {
    if (script.refuse) {
      const { status, code, detail } = script.refuse
      return code ? refusal(status, code) : respond(status, { detail: detail ?? NO_ACCESS })
    }
    // what streamed has committed: the next page-0 read of the feed lists it too
    for (const row of streamedRows) if (!feed.some((r) => r.id === row.id)) feed.push(row)
    return respondRaw(encodeFrames(streamFrames(id)), 'text/event-stream')
  }
  const openStream = ({ path, url, route }: ApiRequest) => {
    const headers = route.request().headers()
    streamRequests.push({
      url: url.toString(),
      authorization: headers.authorization ?? null,
      accept: headers.accept ?? null,
      lastEventId: headers['last-event-id'] ?? null,
    })
    const id = STREAM_PATH.exec(path)?.[1] ?? ''
    if (!reachable(id)) return respond(403, { detail: NO_ACCESS })
    if (streamRequests.length === 1 || script.after === 'repeat') return firstAnswer(id)
    return refusal(503, 'live_unavailable')
  }

  // POST …/cases/{id}/comments: recorded; the grown response with the resolved mentions.
  const postComment = (request: ApiRequest) => {
    const body = (bodyOf(request) ?? {}) as { content?: string; comment_type?: string; parent_id?: string | null; step_number?: number | null }
    writes.push({ line: `POST ${request.path}`, body })
    if (user.role === 'VIEWER') return respond(403, { detail: 'Insufficient permissions' })
    const caseId = COMMENTS_PATH.exec(request.path)?.[1] ?? ''
    if (body.parent_id && !comments.some((c) => c.id === body.parent_id && c.test_case_id === caseId)) {
      return respond(404, { detail: PARENT_NOT_FOUND })
    }
    posted += 1
    const at = NOW.toISOString()
    const comment = {
      id: `c3000000-0000-4000-8000-${String(100 + posted).padStart(12, '0')}`,
      test_case_id: caseId,
      author_id: user.id,
      content: String(body.content ?? ''),
      comment_type: body.comment_type ?? 'general',
      parent_id: body.parent_id ?? null,
      step_number: body.step_number ?? null,
      is_resolved: false,
      created_at: at,
      updated_at: at,
    }
    comments.push(comment)
    return respond(201, { ...comment, mentions: resolveMentions(comment.content, members, user.id) })
  }

  const handlers: ApiHandlers = [
    ['/api/v1/activity/event-types', () => options.eventTypes ?? EVENT_TYPES],
    ['/api/v1/activity/mine', readMine],
    [STREAM_PATH, openStream],
    [FEED_PATH, readFeed],
    [MEMBERS_PATH, ({ path }) => {
      const id = MEMBERS_PATH.exec(path)?.[1] ?? ''
      return reachable(id) ? members.map((m) => ({ ...m, project_id: id })) : respond(403, { detail: NO_ACCESS })
    }],
    // a list read answers every stored comment of the case, `mentions: []` (mentions are not stored)
    [COMMENTS_PATH, ({ path }) => {
      const caseId = COMMENTS_PATH.exec(path)?.[1] ?? ''
      return comments.filter((c) => c.test_case_id === caseId).map((c) => ({ ...c, mentions: [] }))
    }],
    [COMMENTS_PATH, postComment, 'POST'],
    ['/api/v1/projects', () => projects],
    ...(options.base ?? LAYOUT),
  ]
  return { handlers, writes, streamRequests, feed, mine, comments, members, user, projectId, projects }
}

