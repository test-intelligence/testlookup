/**
 * Fixtures for the retention-hold and keep-last-floor vitests (W22a ACT-05 / ACT-04).
 *
 * - `holdRow(overrides)`: one `RetentionHold` in the REAL list / place shape,
 *   every field present (a live release hold by default).
 * - `holdList(rows)`: the REAL `GET …/retention-holds` answer — `{items,
 *   total, counts}`, never a bare array; `counts` derived from the rows' states.
 * - `holdRef(overrides)`: a `HoldRef` as the release detail / pack list /
 *   preview carry it.
 * - `previewWithHolds(...)` / `previewWithFloor(...)`: a full `RetentionPreview`
 *   with the W22a top-level `holds` / `floor` / `references_broken` facts.
 *
 * Sentinel reasons are non-hex words ("Audit Q4 sentinel"), so they never
 * collide with a uuid or read as a phone number. Timestamps are built at CALL
 * time (never a module constant).
 */
import type {
  ReferenceBroken,
  RetentionPreview,
  RetentionPreviewFloor,
  RetentionPreviewHold,
} from '@/types/retention'
import type { HoldRef, HoldState, RetentionHold, RetentionHoldList } from '@/types/retentionHolds'

export const HOLD_PROJECT_ID = '00000000-0000-4000-8000-00000000a0a1'
export const HOLD_RELEASE_ID = '00000000-0000-4000-8000-00000000a0b2'
export const SENTINEL_REASON = 'Audit Q4 sentinel'

let sequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
function holdUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0xd000 + sequence).padStart(12, '0')}`
}

function daysFromNow(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString()
}

export function holdRow(overrides: Partial<RetentionHold> = {}): RetentionHold {
  return {
    id: holdUuid(),
    project_id: HOLD_PROJECT_ID,
    entity_type: 'release',
    entity_id: HOLD_RELEASE_ID,
    entity_label: 'release Payments 2026-Q4',
    reason: SENTINEL_REASON,
    expires_at: null,
    state: 'live',
    placed_by_name: 'Quinn Lead',
    placed_at: daysFromNow(-1),
    released_at: null,
    released_by_name: null,
    release_reason: null,
    ...overrides,
  }
}

export function holdList(rows: RetentionHold[], total?: number): RetentionHoldList {
  const counts: Record<HoldState, number> = { live: 0, expired: 0, released: 0 }
  for (const row of rows) {
    if (row.state === 'live' || row.state === 'expired' || row.state === 'released') counts[row.state] += 1
  }
  return { items: rows, total: total ?? rows.length, counts }
}

export function holdRef(overrides: Partial<HoldRef> = {}): HoldRef {
  return {
    hold_id: holdUuid(),
    entity_type: 'release',
    entity_id: HOLD_RELEASE_ID,
    entity_label: 'release Payments 2026-Q4',
    reason: SENTINEL_REASON,
    expires_at: null,
    ...overrides,
  }
}

function basePreview(): RetentionPreview {
  return {
    cutoffs: {
      raw_events: daysFromNow(-90),
      runs: daysFromNow(-365),
      artifacts: daysFromNow(-180),
      audit: daysFromNow(-2555),
    },
    candidates: {
      runs: 4,
      test_cases: 120,
      mongo_docs: { execution_logs: 4 },
      minio_objects: 8,
      event_archive_rows: 2,
      audit_rows: 0,
      provenance_rows: 0,
      compliance_packs_expired: 0,
      evidence_artifact_rows: 0,
      memory_entries_expired: 0,
      analysis_cache_entries: 0,
      search_index_documents: 0,
      ai_history_versions: 0,
      search_usage_rows: 0,
      shadow_pairs_unlabelled: 0,
    },
    unmeasured: [],
    holds: { project_held: false, runs_kept: 0, packs_kept: 0, active: [] },
    floor: null,
    references_broken: [],
    references_measured: true,
  }
}

/** A preview whose live holds keep runs / packs (one release hold keeping 5 runs by default). */
export function previewWithHolds(
  active: Array<Partial<RetentionPreviewHold>> = [{ runs_kept: 5, packs_kept: 0 }],
  options: { projectHeld?: boolean; references?: ReferenceBroken[] } = {},
): RetentionPreview {
  const rows: RetentionPreviewHold[] = active.map((hold) => ({
    ...holdRef(),
    runs_kept: 0,
    packs_kept: 0,
    ...hold,
  }))
  const preview = basePreview()
  return {
    ...preview,
    holds: {
      project_held: options.projectHeld ?? rows.some((row) => row.entity_type === 'project'),
      runs_kept: rows.reduce((sum, row) => sum + row.runs_kept, 0),
      packs_kept: rows.reduce((sum, row) => sum + row.packs_kept, 0),
      active: rows,
    },
    references_broken: options.references ?? [],
  }
}

/** A preview whose keep-last floor keeps runs (10 kept by the newest-10 rule by default). */
export function previewWithFloor(
  floor: Partial<RetentionPreviewFloor> = {},
  options: { references?: ReferenceBroken[]; referencesMeasured?: boolean } = {},
): RetentionPreview {
  return {
    ...basePreview(),
    floor: { keep_last_runs: 10, runs_kept: 10, latest_passed_kept: true, suites_kept: 2, ...floor },
    references_broken: options.references ?? [
      {
        table: 'run_baselines',
        column: 'baseline_run_id',
        count: 3,
        label: '3 runs lose their baseline link',
      },
    ],
    references_measured: options.referencesMeasured ?? true,
  }
}
