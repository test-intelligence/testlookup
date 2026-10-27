/**
 * Settings › Data & storage (W20 ADM-10): the deployment's external secret provider (a Vault-compatible KV v2 store).
 *
 * Shows whether Vault is configured (from the deployment's environment, never the UI), the host and auth method,
 * whether login works, and how many stored secrets are references to it. Any secret field accepts
 * `vault:<path>#<key>` instead of a value; the value then stays in Vault.
 */
import { useEffect, useState } from 'react'

import { getSecretProviders, type SecretProviderStatus } from '@/services/appSettingsService'
import { extractErrorMessage } from '@/services/apiErrors'

export default function SecretProvidersCard() {
  const [providers, setProviders] = useState<SecretProviderStatus[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getSecretProviders()
      .then((r) => { if (live) setProviders(r.providers) })
      .catch((err) => {
        if (live) setError(extractErrorMessage((err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail, 'The secret provider status could not be read'))
      })
    return () => { live = false }
  }, [])

  return (
    <section className="card space-y-2" data-testid="secret-providers" aria-label="Secrets">
      <h3 className="text-sm font-semibold text-[var(--color-text)]">Secrets</h3>
      <p className="text-xs text-[var(--color-text-muted)]">
        Secrets are encrypted in the database. Any secret field also accepts a Vault reference,{' '}
        <code>vault:path#key</code>: the value then stays in Vault (HashiCorp Vault or OpenBao, KV v2).
      </p>
      {error && <p role="alert" className="text-xs text-[var(--ui-danger)]">{error}</p>}
      {providers?.map((p) => (
        <div key={p.name} className="text-sm text-[var(--color-text)]" data-testid={`secret-provider-${p.name}`}>
          <span className="font-medium">Vault</span>{' '}
          <span className={p.healthy ? 'text-[var(--status-passed)]' : p.configured ? 'text-[var(--ui-danger)]' : 'text-[var(--color-text-muted)]'}>
            {p.healthy ? 'healthy' : p.configured ? 'unhealthy' : 'not configured'}
          </span>
          {p.configured && <span className="text-xs text-[var(--color-text-muted)]"> · {p.address_host} · {p.auth_method} auth</span>}
          <span className="text-xs text-[var(--color-text-muted)]"> · {p.references} {p.references === 1 ? 'secret references' : 'secrets reference'} it</span>
          <p className="text-xs text-[var(--color-text-muted)]">{p.message}</p>
        </div>
      ))}
    </section>
  )
}
