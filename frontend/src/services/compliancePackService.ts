import { getData, postData } from './http'
import { api } from './api'
import { downloadBlob } from '@/utils/download'

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
  /**
   * W11 REL-11: the key that signed the pack; `null` = unsigned (generated
   * before signing existed, or while the key was unreadable). Optional so old
   * fixtures still type-check: an ABSENT field means the server did not say.
   */
  signing_key_id?: string | null
}

/** One file of a verification (`CompliancePackFileCheck`). */
export interface CompliancePackFileCheck {
  name: string
  /** `ok` | `mismatch` | `missing` | `unexpected` */
  status: string
  expected_sha256?: string | null
  actual_sha256?: string | null
}

/** `POST /api/v1/compliance-packs/{id}/verify` (`CompliancePackVerification`). */
export interface CompliancePackVerification {
  pack_id: string
  /** `verified` | `tampered` | `unreadable` */
  result: string
  /** `intact` | `broken` */
  chain: string
  manifest_matches_record: boolean
  /** `valid` | `invalid` | `unsigned` | `unknown_key` */
  signature: string
  signing_key_id?: string | null
  files_checked: number
  files_ok: number
  files: CompliancePackFileCheck[]
  /** The server's one sentence, shown as text. */
  summary: string
  checked_at: string
}

export interface CompliancePackSigningKey {
  key_id: string
  algorithm: string
  /** The PUBLIC key; the private key never leaves the server. */
  public_key_pem: string
  created_at: string
  retired_at?: string | null
  active: boolean
}

/** `GET /api/v1/compliance-packs/signing-keys`. */
export interface CompliancePackSigningKeys {
  /** `null` until the first signed pack creates the deployment's key. */
  active_key_id?: string | null
  keys: CompliancePackSigningKey[]
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

  /**
   * W11 REL-11: check the stored pack against its manifest, the recorded
   * digest and its signature. Writes nothing; the panel renders the answer
   * (and any failure) under the row, so no toast.
   */
  verify: (packId: string) =>
    postData<CompliancePackVerification>(
      `/api/v1/compliance-packs/${packId}/verify`,
      undefined,
      { suppressToast: true },
    ),

  /** Every signing key's public half (deployment-wide); never creates one. */
  signingKeys: () => getData<CompliancePackSigningKeys>('/api/v1/compliance-packs/signing-keys'),

  /** Save one public key as `testlookup-compliance-<key>.pem` (an authenticated blob read). */
  downloadPublicKey: async (keyId: string): Promise<void> => {
    const response = await api.get(
      `/api/v1/compliance-packs/signing-keys/${encodeURIComponent(keyId)}/pem`,
      { responseType: 'blob' },
    )
    downloadBlob(response.data as Blob, `testlookup-compliance-${keyId}.pem`)
  },
}
