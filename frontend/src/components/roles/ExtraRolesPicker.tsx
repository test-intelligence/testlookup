import { EXTRA_ROLE_OPTIONS } from '@/content/roles'
import type { JobRole } from '@/types/permissions'

/**
 * Enterprise roles D-16: pick the job roles a person holds beside their role (e.g. a QA engineer who is also
 * the release manager). The primary role gives the rank; extra roles only add what that job may do.
 * The primary role is never offered again as an extra one.
 */
export function ExtraRolesPicker({
  value,
  onChange,
  primary,
  idPrefix,
  disabled = false,
  hint,
  legend = 'Extra roles (optional)',
  exclude = [],
}: {
  value: readonly JobRole[]
  onChange: (next: JobRole[]) => void
  primary?: string
  idPrefix: string
  disabled?: boolean
  hint?: string
  legend?: string
  /** Roles not offered (a QA lead is not offered the manager roles, D-17). A role already held stays shown. */
  exclude?: readonly string[]
}) {
  const options = EXTRA_ROLE_OPTIONS.filter(
    (option) => option.value !== primary && (!exclude.includes(option.value) || value.includes(option.value)),
  )
  function toggle(role: JobRole) {
    const next = value.includes(role) ? value.filter((r) => r !== role) : [...value, role]
    onChange(EXTRA_ROLE_OPTIONS.map((o) => o.value).filter((r) => next.includes(r)))
  }
  return (
    <div data-extra-roles="">
      <p className="block text-sm text-[var(--color-text-muted)] mb-1">{legend}</p>
      <div className="grid grid-cols-2 gap-1 bg-[var(--color-bg-input)] border border-[var(--color-border)] rounded p-2">
        {options.map((option) => (
          <label
            key={option.value}
            htmlFor={`${idPrefix}-${option.value}`}
            className="flex items-center gap-2 text-sm text-[var(--color-text)]"
            title={option.description}
          >
            <input
              id={`${idPrefix}-${option.value}`}
              type="checkbox"
              checked={value.includes(option.value)}
              disabled={disabled}
              onChange={() => toggle(option.value)}
            />
            <span className="truncate">{option.label}</span>
          </label>
        ))}
      </div>
      <p className="mt-1 text-xs text-[var(--color-text-faint)]">
        {hint ?? 'Their role sets what they may change everywhere; extra roles add what those jobs may do.'}
      </p>
    </div>
  )
}
