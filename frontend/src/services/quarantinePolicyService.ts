/**
 * W09 FLK-02: a project's quarantine lifecycle policy
 * (`/api/v1/projects/{project_id}/quarantine/policy`), the Policy tab of
 * Flaky tests and the Quarantined tab's over-cap warning.
 *
 * Its own module, NOT `flakyQuarantineService.ts`: that module is
 * fixed-factory mocked in seven test files, where a function added to it
 * would be `undefined`.
 *
 * Both calls opt out of the global error toast: the policy card renders a
 * failed read (a sentence and Retry) and a refused save (the server's
 * detail) itself, and the Quarantined tab reads the policy only for its
 * warning, which a failed read simply leaves out.
 */
import { getData, putData } from './http'

export interface QuarantinePolicy {
  project_id: string
  sla_days: number
  auto_create_defect: boolean
  auto_promote: boolean
  promote_after_passes: number
  detection_flip_rate_threshold: number
  detection_min_runs: number
  /** Warn past this many active quarantines; 0 = no limit. */
  max_active_quarantined: number
  /** True when the project has no row yet and the code defaults apply. */
  is_default: boolean
}

/** The PUT replaces every field (the server's schema; an omitted one resets to its default). */
export type QuarantinePolicyBody = Omit<QuarantinePolicy, 'project_id' | 'is_default'>

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/**
 * The defensive guard over the policy answer: `null` for anything missing or
 * malformed, so no reader seeds a form from (or warns on) a shape it did not
 * check. `max_active_quarantined` is optional on the wire (a server before
 * W09 does not send it): absent reads the default 8; present must be a number.
 */
export function parseQuarantinePolicy(raw: unknown): QuarantinePolicy | null {
  if (!isRecord(raw)) return null
  const {
    project_id, sla_days, auto_create_defect, auto_promote, promote_after_passes,
    detection_flip_rate_threshold, detection_min_runs, is_default,
  } = raw
  if (typeof project_id !== 'string' || project_id === '') return null
  if (!finite(sla_days) || !finite(promote_after_passes) || !finite(detection_flip_rate_threshold) || !finite(detection_min_runs)) {
    return null
  }
  if (typeof auto_create_defect !== 'boolean' || typeof auto_promote !== 'boolean') return null
  if (is_default !== undefined && typeof is_default !== 'boolean') return null
  const cap = raw.max_active_quarantined
  if (cap !== undefined && cap !== null && !finite(cap)) return null
  return {
    project_id,
    sla_days,
    auto_create_defect,
    auto_promote,
    promote_after_passes,
    detection_flip_rate_threshold,
    detection_min_runs,
    max_active_quarantined: finite(cap) ? cap : 8,
    is_default: is_default === true,
  }
}

const policyPath = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/quarantine/policy`

export const quarantinePolicyService = {
  /** The raw answer: read it through `parseQuarantinePolicy` (the hook does). */
  get(projectId: string): Promise<QuarantinePolicy> {
    return getData<QuarantinePolicy>(policyPath(projectId), { suppressToast: true })
  },

  /** Replaces the whole policy (QA lead and above, a member of the project). */
  update(projectId: string, body: QuarantinePolicyBody): Promise<QuarantinePolicy> {
    return putData<QuarantinePolicy, QuarantinePolicyBody>(policyPath(projectId), body, { suppressToast: true })
  },
}
