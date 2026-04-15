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

export const compliancePackService = {
  list: (releaseId: string) =>
    getData<CompliancePackRead[]>(`/api/v1/releases/${releaseId}/compliance-packs`),

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
