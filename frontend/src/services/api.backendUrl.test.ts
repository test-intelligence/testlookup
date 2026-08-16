/**
 * `backendUrl(path)` resolves an absolute backend endpoint the same way the
 * axios client resolves its requests, so copy-paste command snippets (the
 * first-run guide's ingest `curl`) target the same backend the running UI does.
 *
 * The whole bundle is deploy-target-agnostic: when `VITE_API_BASE_URL` is unset
 * the UI talks to the backend same-origin behind an ingress; when it is set the
 * UI (and therefore the snippet) targets that origin. A snippet that hardcoded
 * `localhost:8000` was wrong on every real self-host — this locks the
 * resolution against that regression.
 *
 * `BASE_URL` is read from `import.meta.env` at module-eval time, so each case
 * stubs the env and re-imports a fresh module rather than relying on whatever
 * `VITE_API_BASE_URL` the ambient environment happens to set (CI sets it to
 * `http://localhost:8000`; local dev leaves it unset).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/authStore', () => ({
  useAuthStore: {
    getState: () => ({ token: null, refreshAccessToken: vi.fn() }),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

async function loadBackendUrl(base: string) {
  vi.resetModules()
  vi.stubEnv('VITE_API_BASE_URL', base)
  const mod = await import('./api')
  return mod.backendUrl
}

describe('backendUrl', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('resolves same-origin when VITE_API_BASE_URL is empty', async () => {
    // The default deploy posture: same host as the page (the ingress routes
    // /api and / to the same origin — see k8s/base/ingress.yaml).
    const backendUrl = await loadBackendUrl('')
    const url = backendUrl('/api/v1/ingest/file')
    expect(url).toBe(`${window.location.origin}/api/v1/ingest/file`)
    expect(url).not.toContain('localhost:8000')
  })

  it('resolves against an explicit VITE_API_BASE_URL origin', async () => {
    // A split-origin deploy (UI and API on different hosts): the snippet must
    // follow the configured backend, not the page origin.
    const backendUrl = await loadBackendUrl('https://tl.example.com')
    expect(backendUrl('/api/v1/ingest/file')).toBe(
      'https://tl.example.com/api/v1/ingest/file',
    )
  })

  it('normalizes a path missing its leading slash', async () => {
    const backendUrl = await loadBackendUrl('')
    expect(backendUrl('api/v1/ingest/file')).toBe(
      `${window.location.origin}/api/v1/ingest/file`,
    )
  })
})
