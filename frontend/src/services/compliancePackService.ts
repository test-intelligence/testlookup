import { getData, postData } from './http'
import { api } from './api'

export interface CompliancePackRead {
  id: string
  release_id: string | null
  project_id: string
  test_run_id: string | null
  minio_key: string
  manifest_sha256: string
  file_count: number
  bytes: number
  retention_expires_at: string
  generated_at: string
  generated_by_user_id: string | null
  metadata_snapshot: Record<string, unknown> | null
  notes: string | null
}

export interface CompliancePackGenerateBody {
  notes?: string
  retention_days?: number
}

/**
 * One pack of the project-wide list (`GET /api/v1/compliance-packs`,
 * `CompliancePackSummaryRead`). Deliberately without `minio_key`,
 * `metadata_snapshot` and `notes`: storage keys and snapshots do not belong in
 * a rail.
 */
export interface ProjectCompliancePack {
  id: string
  release_id: string | null
  /** `null` when the release was deleted (the pack outlives it). */
  release_name: string | null
  release_version: string | null
  project_id: string
  project_name: string | null
  bytes: number
  file_count: number
  /** The full 64 hex; the rail shows the first 8. */
  manifest_sha256: string
  generated_at: string
  retention_expires_at: string
  /** `retention_expires_at <= now`. */
  retired: boolean
}

export interface ProjectCompliancePackList {
  items: ProjectCompliancePack[]
  /** Packs in scope, not `items.length` (the list is capped by `limit`). */
  total: number
}

/**
 * A pack's size as every pack surface prints it, so the rail's figure and the
 * release panel's are the same text for the same pack.
 */
export const packSizeText = (bytes: number): string => `${(bytes / 1024).toFixed(1)} KB`

export const compliancePackService = {
  list: (releaseId: string) =>
    getData<CompliancePackRead[]>(`/api/v1/releases/${releaseId}/compliance-packs`),

  /**
   * The packs of one project, or of every project the caller can see when
   * `projectId` is `null` (the server scopes it by membership), newest first.
   */
  listForProject: ({ projectId, limit }: { projectId: string | null; limit?: number }) =>
    getData<ProjectCompliancePackList>('/api/v1/compliance-packs', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        ...(limit != null ? { limit } : {}),
      },
    }),

  generate: (releaseId: string, body: CompliancePackGenerateBody = {}) =>
    postData<CompliancePackRead>(
      `/api/v1/releases/${releaseId}/compliance-pack`,
      body,
    ),

  /** Download the pack as a browser-saved blob. */
  download: async (packId: string, filename?: string): Promise<void> => {
    const response = await api.get(`/api/v1/compliance-packs/${packId}/download`, {
      responseType: 'blob',
    })
    const blob = response.data as Blob
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename || `compliance-pack-${packId}.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
}
