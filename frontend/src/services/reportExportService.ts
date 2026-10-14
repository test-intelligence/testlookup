import { api } from './api';

// ── Types ───────────────────────────────────────────────────────────────────

export type ShareLayout = 'executive' | 'engineering';

export interface ShareLink {
  id: string;
  /**
   * The raw token and URL exist only on the CREATE response: the token is
   * hashed at rest, so a listed link carries `null` for both (GATE-06).
   */
  token: string | null;
  share_url: string | null;
  report_layout: string;
  expires_at: string;
  created_by_name: string | null;
  access_count: number;
  is_revoked: boolean;
  created_at: string;
  /**
   * Create only: whether the run has the intelligence snapshot the recipient
   * will be served. `null`/absent = not evaluated (the list does not check).
   */
  snapshot_ready?: boolean | null;
  /** Create only: why the link will 404 for now (no snapshot yet). */
  warning?: string | null;
}

// ── PDF Export ──────────────────────────────────────────────────────────────

export async function downloadPdf(runId: string, layout: 'executive' | 'engineering'): Promise<void> {
  const response = await api.get(`/api/v1/reports/runs/${runId}/pdf`, {
    params: { layout },
    responseType: 'blob',
  });
  const url = URL.createObjectURL(response.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `report-${runId.slice(0, 8)}-${layout}.pdf`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Evidence Bundle ─────────────────────────────────────────────────────────

export async function downloadEvidenceBundle(runId: string): Promise<void> {
  const response = await api.get(`/api/v1/reports/runs/${runId}/evidence-bundle`, {
    responseType: 'blob',
  });
  const url = URL.createObjectURL(response.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `evidence-${runId.slice(0, 8)}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Share Links ─────────────────────────────────────────────────────────────

/** The expiries the API accepts are 1-30 days; these are the ones offered. */
export const SHARE_EXPIRY_DAYS = [1, 7, 14, 30] as const;

export async function createShareLink(
  runId: string,
  layout: ShareLayout,
  expiryDays: number = 7,
): Promise<ShareLink> {
  const { data } = await api.post<ShareLink>(`/api/v1/reports/runs/${runId}/share`, {
    layout,
    expiry_days: expiryDays,
  });
  return data;
}

export async function listShareLinks(runId: string): Promise<ShareLink[]> {
  const { data } = await api.get<ShareLink[]>(`/api/v1/reports/runs/${runId}/share-links`);
  return data;
}

export async function revokeShareLink(linkId: string): Promise<void> {
  await api.delete(`/api/v1/reports/share-links/${linkId}`);
}
