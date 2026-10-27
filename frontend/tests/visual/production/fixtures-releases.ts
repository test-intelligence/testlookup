/**
 * W05 "release-gate" hermetic answers (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.1-§2.6, §4.7):
 * the `/releases` world of `fixtures-pages.ts` (`releasesWorld`, read by `RELEASES_PAGE`) plus the WRITES the
 * W05 flows make, each changing the world so the reads that follow show it:
 *
 *  - `POST /releases/{id}/gate/evaluate` records the scripted verdict (REL-01);
 *  - `PUT /releases/{id}/phases/{phase_id}` refuses a non-GO completion / a skip with the server's 409 sentence
 *    until the body carries `gate_override_reason` / `skip_reason` (REL-10, enforcement on);
 *  - `PUT /releases/{id}` sets or clears the owner from the members list (REL-02);
 *  - `POST /releases/{id}/activate` moves the project's active release (REL-08);
 *  - `POST /releases/sync` answers the offline 503 (REL-08), or a scripted result;
 *  - `GET /compliance-packs/{id}/download` answers a ZIP (REL-04); `/activity` and its event drawer answer the
 *    ledger rows the rail links to.
 *
 * Every factory returns fresh state per call (a write in one test never leaks into the next) and records the
 * writes it answered (`writes`), so a spec asserts the body, not only that "something was called".
 * Not a spec file. The shared `RELEASES_PAGE` (three fail-closed CI specs) is edited in `fixtures-pages.ts` only.
 */
import { respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { daysAgo, LAYOUT, NOW, USER } from './fixtures'
import {
  gateSummary,
  phaseGate,
  projectMembers,
  RELEASE_ACTIVE_ID,
  RELEASE_PACK,
  RELEASE_PEOPLE,
  RELEASE_PLANNING_ID,
  RELEASE_SHIPPED_ID,
  releasePhase,
  releaseRow,
  releasesReadHandlers,
  releasesWorld,
  type ReleaseGateFixture,
  type ReleasesWorld,
} from './fixtures-pages'

export { RELEASE_ACTIVE_ID, RELEASE_PACK, RELEASE_PEOPLE, RELEASE_PLANNING_ID, RELEASE_SHIPPED_ID }

export const QA_ENGINEER = { ...USER, id: '00000000-0000-4000-8000-0000000000e1', username: 'qa_engineer', full_name: 'QA Engineer', role: 'QA_ENGINEER' }
export const VIEWER = { ...USER, id: '00000000-0000-4000-8000-0000000000e2', username: 'viewer', full_name: 'Viewer', role: 'VIEWER' }
/** All projects is an ADMIN's view (`TopBar` moves any other role onto its first project). */
export const ADMIN = { ...USER, id: '00000000-0000-4000-8000-0000000000ad', username: 'admin', email: 'admin@example.test', full_name: 'Admin', role: 'ADMIN' }

/** One answered write: `METHOD path` and its JSON body. */
export interface RecordedWrite {
  line: string
  body: unknown
}

const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

/** The in-progress hotfix: never evaluated until a QA lead presses Evaluate (REL-01). */
export const RELEASE_HOTFIX_ID = 'aaaaaaaa-0000-4000-8000-000000000004'

/** What `POST .../gate/evaluate` records for the hotfix: GO over 40 tests in 3 runs. */
export const HOTFIX_GO: ReleaseGateFixture = gateSummary({
  verdict: 'GO',
  decided_at: NOW.toISOString(),
  pass_rate: 97.5,
  denominator: 42,
  evidence_count: 40,
  run_count: 3,
  status_counts: { PASSED: 39, SKIPPED: 1 },
})

/** The 409 sentences of `release_service._enforce_phase_gate` (enforcement on), verbatim. */
export const PHASE_GATE_REFUSAL =
  "This phase's gate says NO_GO: test_pay (checkout-e2e) is FAILED. Fix it, or supply gate_override_reason to complete it anyway."
export const PHASE_SKIP_REFUSAL =
  "Skipping a phase needs a reason — it counts as done in the release's all-phases check, so an unexplained skip is indistinguishable from work that passed."

/** The sync's 503 while offline (`github_release_sync`), verbatim. */
export const SYNC_OFFLINE = 'AI_OFFLINE_MODE is enabled'

export interface ReleasesOptions {
  /** Add the never-evaluated in-progress hotfix (the Evaluate flow). */
  hotfix?: boolean
  /** The sync's answer: default the offline 503. */
  sync?: () => unknown
}

/** The ledger rows the rail links to, and the Activity page that opens one (its registry, feed and drawer). */
function activityPageHandlers(world: ReleasesWorld): ApiHandlers {
  return [
    [
      '/api/v1/activity/event-types',
      () => ({ categories: ['runs', 'release', 'configuration'], actor_types: ['user', 'api_key', 'system'], entity_types: ['release'], events: {} }),
    ],
    [/^\/api\/v1\/projects\/[^/]+\/activity\/[^/]+$/, ({ path }) => {
      const id = path.split('/').pop()
      const event = world.events.find((e) => e.id === id)
      if (!event) return respond(404, { detail: 'Event not found' })
      return { ...event, diff: { before: { active_release: '2026.10' }, after: { active_release: '2026.11' }, changed_fields: ['active_release'] } }
    }],
  ]
}

/**
 * The W05 `/releases` world with its writes. `world` is the live state (the reads answer from it);
 * `writes` lists every write answered, in order.
 */
export function releasesHandlers(options: ReleasesOptions = {}): { handlers: ApiHandlers; world: ReleasesWorld; writes: RecordedWrite[] } {
  const world = releasesWorld()
  const writes: RecordedWrite[] = []
  if (options.hotfix) {
    world.rows.splice(1, 0, releaseRow(RELEASE_HOTFIX_ID, '2026.11.1', 'in_progress', {
      description: 'Card-retry hotfix',
      planned_date: daysAgo(-12),
      test_run_count: 3,
      phases: [releasePhase(RELEASE_HOTFIX_ID, 'ph-hotfix-verify', 'Hotfix verification', 'qa_testing', 'in_progress', 1)],
    }))
    world.phaseGates[RELEASE_HOTFIX_ID] = [phaseGate('ph-hotfix-verify', 'Hotfix verification', 'GO', [], 40, 42)]
    world.baselines[RELEASE_HOTFIX_ID] = { comparable: false, reason: 'This release has no recorded verdict yet.' }
  }
  const record = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return body
  }
  const row = (id: string) => world.rows.find((r) => r.id === id)

  const handlers: ApiHandlers = [
    // W19 INT-05: the sync dialog asks whether the project has a GitLab integration (none: GitHub / Jira only).
    [/^\/api\/v1\/projects\/[^/]+\/integrations\/gitlab$/, () => respond(404, { detail: 'GitLab integration not configured' })],
    // REL-01: record a verdict. The hotfix goes GO; any other release keeps its stored verdict, re-dated.
    [/^\/api\/v1\/releases\/[^/]+\/gate\/evaluate$/, (request) => {
      record(request)
      const id = request.path.split('/')[4]
      const target = row(id)
      if (!target) return respond(404, { detail: 'Release not found' })
      target.gate = id === RELEASE_HOTFIX_ID ? { ...HOTFIX_GO } : { ...(target.gate ?? gateSummary({ verdict: 'NOT_EVALUATED', measured: false, insufficient_reason: '0 of 0 tests carry evidence; 5 required' })), decided_at: NOW.toISOString() }
      return {
        release_id: id,
        verdict: target.gate.verdict,
        // `evaluate_release` answers every reason, not the list's first 20.
        blocking_reasons: world.reasons[id] ?? target.gate.blocking_reasons,
        recorded: true,
        decision_id: `dec-${writes.length}`,
        scorecard: { pass_rate: target.gate.pass_rate, evidence_count: target.gate.evidence_count, denominator: target.gate.denominator, insufficient_reason: target.gate.insufficient_reason },
      }
    }, 'POST'],
    // REL-10: enforcement on. A completion over a non-GO phase gate, or a skip, needs its reason.
    [/^\/api\/v1\/releases\/[^/]+\/phases\/[^/]+$/, (request) => {
      const body = record(request) as { status?: string; gate_override_reason?: string; skip_reason?: string } | null
      const [, , , , releaseId, , phaseId] = request.path.split('/')
      const target = row(releaseId)
      const phase = target?.phases.find((p) => p.id === phaseId)
      if (!target || !phase) return respond(404, { detail: 'Phase not found' })
      const live = world.phaseGates[releaseId]?.find((g) => g.phase_id === phaseId)
      if (body?.status === 'completed' && live && live.verdict !== 'GO' && !body.gate_override_reason) {
        return respond(409, { detail: PHASE_GATE_REFUSAL })
      }
      if (body?.status === 'skipped' && !body.skip_reason) return respond(409, { detail: PHASE_SKIP_REFUSAL })
      if (body?.status) phase.status = body.status
      const allDone = target.phases.every((p) => p.status === 'completed' || p.status === 'skipped')
      return { ...phase, all_phases_completed: allDone }
    }, 'PUT'],
    // REL-02: the owner from the members list; null clears it.
    [/^\/api\/v1\/releases\/[^/]+$/, (request) => {
      const body = record(request) as Record<string, unknown> | null
      const target = row(request.path.split('/').pop() as string)
      if (!target) return respond(404, { detail: 'Release not found' })
      if (body && 'owner_user_id' in body) {
        const ownerId = body.owner_user_id as string | null
        const member = projectMembers().find((m) => m.user_id === ownerId)
        if (ownerId && (!member || member.is_synthetic || member.is_service_account || !member.is_active)) {
          return respond(422, { detail: 'That user cannot own releases in this project.' })
        }
        target.owner_user_id = ownerId
        target.owner = member ? { id: member.user_id, name: member.full_name ?? member.username } : null
      }
      for (const key of ['name', 'version', 'description', 'status'] as const) {
        if (body && typeof body[key] === 'string') (target as Record<string, unknown>)[key] = body[key]
      }
      target.updated_at = NOW.toISOString()
      return target
    }, 'PUT'],
    // REL-08: the project's active release moves.
    [/^\/api\/v1\/releases\/[^/]+\/activate$/, (request) => {
      record(request)
      const id = request.path.split('/')[4]
      const target = row(id)
      if (!target) return respond(404, { detail: 'Release not found' })
      if (target.is_active) return { release_id: id, is_active: true, deactivated: null, changed: false }
      const previous = world.rows.find((r) => r.is_active && r.project_id === target.project_id)
      if (previous) previous.is_active = false
      target.is_active = true
      target.activated_at = NOW.toISOString()
      return { release_id: id, is_active: true, deactivated: previous?.id ?? null, changed: true }
    }, 'POST'],
    ['/api/v1/releases/sync', (request) => {
      record(request)
      return options.sync ? options.sync() : respond(503, { detail: SYNC_OFFLINE })
    }, 'POST'],
    // REL-04: the pack itself, as the authenticated download route answers it.
    [/^\/api\/v1\/compliance-packs\/[^/]+\/download$/, (request) => {
      writes.push({ line: `GET ${request.path}`, body: null })
      return respondRaw('PK\u0003\u0004 testlookup compliance pack', 'application/zip')
    }],
    ...activityPageHandlers(world),
    ...releasesReadHandlers(world),
    ...LAYOUT,
  ]
  return { handlers, world, writes }
}

/** The release ids every W05 spec names. */
export const RELEASE = {
  active: RELEASE_ACTIVE_ID,
  planning: RELEASE_PLANNING_ID,
  shipped: RELEASE_SHIPPED_ID,
  hotfix: RELEASE_HOTFIX_ID,
} as const
