/**
 * Storage footprint contracts (S3).
 *
 * Every figure carries two independent honesty flags, and the UI must respect
 * both:
 *
 *  - `measured: false` — the store could not be reached. `bytes` and `items`
 *    are then `null`, never `0`. Zero and unreachable are opposite findings.
 *  - `exact: false` — the byte figure is an estimate over a store shared with
 *    other projects; `estimate_basis` says how it was derived.
 */

export interface StoreFootprint {
  store: string
  measured: boolean
  exact: boolean
  /** False when a safety cap left part of the project's namespace unscanned. */
  complete: boolean
  /** null when `measured` is false — render "not measured", never 0. */
  bytes: number | null
  items: number | null
  estimate_basis: string | null
  unreachable_reason: string | null
}

export interface ProjectStorage {
  project_id: string
  computed_at: string
  stores: StoreFootprint[]
  /** null when nothing was measurable — an unreachable everything is not 0 B. */
  total_bytes: number | null
  /** True when any contributing store was estimated or incompletely scanned. */
  total_is_estimate: boolean
  fully_measured: boolean
}

export interface DeletedProjectStorageEntry {
  project_id: string
  name: string
  /**
   * Whether the nightly purge will ever reach this deleted project. False is
   * the interesting case: retention defaults to off, and the beat only sweeps
   * projects that opted in, so a project deleted without it on is stranded.
   */
  reachable_by_retention: boolean
  footprint: ProjectStorage
}

export interface DeletedProjectsStorage {
  computed_at: string
  projects_total: number
  projects_measured: number
  /** True when the scan was capped — the total is a floor, not a total. */
  truncated: boolean
  projects: DeletedProjectStorageEntry[]
  total_bytes: number | null
  total_is_estimate: boolean
  /** Deleted projects no retention policy will ever reclaim. */
  unreachable_by_retention: number
}
