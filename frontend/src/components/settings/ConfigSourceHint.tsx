import type { ConfigSource } from '@/services/appSettingsService'

/**
 * Where a Settings › AI value comes from, under its control (W04 AIC-01/02/04).
 *
 * `ai_config` = a valid value is stored on this page ("Set here"); anything else
 * (`env_default`, or a field an older backend does not send) = the server's
 * environment default applies ("Server default"). The note says when the change
 * takes effect.
 *
 * Self-contained on purpose: STRAT-10 / D37 (W26) hides the Fine-Tuning toggle,
 * and dropping its hint must be one deleted usage, not a page refactor.
 */
export interface ConfigSourceHintProps {
  /** The `ai_config` key the hint describes, e.g. `deep_investigation_enabled`. */
  configKey: string
  source: ConfigSource | undefined
  /** When the value applies, e.g. "Applies within a minute." */
  note?: string
  /** A test hook for a hint a spec reads directly (e.g. `timeout-hint`). */
  testId?: string
}

function configSourceLabel(source: ConfigSource | undefined): string {
  return source === 'ai_config' ? 'Set here' : 'Server default'
}

export default function ConfigSourceHint({ configKey, source, note, testId }: ConfigSourceHintProps) {
  return (
    <p
      className="text-xs text-[var(--color-text-muted)] mt-1"
      data-config-source=""
      data-config-source-key={configKey}
      data-source={source ?? 'env_default'}
      data-testid={testId}
    >
      {configSourceLabel(source)}
      {note ? ` · ${note}` : ''}
    </p>
  )
}
