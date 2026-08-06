import { KeyRound, Smartphone } from 'lucide-react'
import { normalizeRecoveryInput, normalizeTotpInput } from '@/utils/mfaFormat'

export type MfaFactorMode = 'totp' | 'recovery_code'

interface Props {
  mode: MfaFactorMode
  value: string
  onValueChange: (next: string) => void
  /** Omit to hide the "use a recovery code instead" toggle. */
  onModeChange?: (next: MfaFactorMode) => void
  disabled?: boolean
  autoFocus?: boolean
  idPrefix?: string
  label?: string
}

/**
 * One field for both second factors.
 *
 * Deliberate choices: `autocomplete="one-time-code"` so iOS/Android offer the
 * code from the notification, `inputMode="numeric"` for a number pad on mobile,
 * and normalization on change rather than input restriction so pasting a
 * space- or hyphen-grouped code just works instead of silently truncating.
 */
export default function MfaCodeField({
  mode,
  value,
  onValueChange,
  onModeChange,
  disabled = false,
  autoFocus = false,
  idPrefix = 'mfa',
  label,
}: Props) {
  const isTotp = mode === 'totp'
  const inputId = `${idPrefix}-${isTotp ? 'code' : 'recovery-code'}`

  return (
    <div className="space-y-2">
      <label
        htmlFor={inputId}
        className="block text-sm font-medium text-[var(--color-text-secondary)]"
      >
        {label ?? (isTotp ? 'Authentication code' : 'Recovery code')}
      </label>

      <input
        id={inputId}
        name={inputId}
        type="text"
        required
        disabled={disabled}
        autoFocus={autoFocus}
        value={value}
        onChange={(e) =>
          onValueChange(
            isTotp ? normalizeTotpInput(e.target.value) : normalizeRecoveryInput(e.target.value),
          )
        }
        autoComplete={isTotp ? 'one-time-code' : 'off'}
        inputMode={isTotp ? 'numeric' : 'text'}
        pattern={isTotp ? '[0-9]*' : undefined}
        spellCheck={false}
        placeholder={isTotp ? '123456' : 'ABCD-EFGH-JKLM-NPQR'}
        aria-describedby={`${inputId}-hint`}
        className="block w-full appearance-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-3 py-2 font-mono tracking-[0.35em] text-[var(--color-text)] placeholder-[var(--color-text-faint)] shadow-sm focus:border-[var(--color-ring)] focus:outline-none focus:ring-[var(--color-ring)] disabled:opacity-50 sm:text-sm"
      />

      <p id={`${inputId}-hint`} className="text-xs text-[var(--color-text-muted)]">
        {isTotp
          ? 'Six digits from your authenticator app. It rotates every 30 seconds.'
          : 'One of the single-use codes you saved when you enabled MFA. It will be consumed.'}
      </p>

      {onModeChange && (
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            onModeChange(isTotp ? 'recovery_code' : 'totp')
            onValueChange('')
          }}
          className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-accent-ink)] transition-opacity hover:opacity-80 disabled:opacity-50"
        >
          {isTotp ? <KeyRound className="h-3 w-3" /> : <Smartphone className="h-3 w-3" />}
          {isTotp ? 'Use a recovery code instead' : 'Use my authenticator app instead'}
        </button>
      )}
    </div>
  )
}
