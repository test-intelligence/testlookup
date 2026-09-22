import { ChevronDown } from 'lucide-react'
import { ScopeSummaryButton } from './ScopeSummaryButton'
import { SEVERAL_SELECTED, suiteSelectOptions } from '@/lib/scopeControls'

export default function SuiteFilterSelect({
  value,
  onChange,
  options,
  disabled = false,
  allLabel = 'All suites',
  title = 'Filter by test suite',
  multiLabel,
}: {
  value: string
  onChange: (value: string) => void
  options: string[]
  disabled?: boolean
  allLabel?: string
  title?: string
  /**
   * Set when several suites are selected globally (VIZ-303, "2 suites"). The
   * control is then a read-only summary, not a `<select>`: a native select
   * commits the next option on a single ArrowDown, which silently collapsed
   * several suites into one (a11y M5). Activating it moves focus to the report
   * filter bar's suite control, or opens a menu where a suite is picked
   * explicitly.
   */
  multiLabel?: string
}) {
  if (multiLabel) {
    return (
      <span className="relative inline-flex items-center">
        <ScopeSummaryButton
          dimension="suite"
          ariaLabel="Test suite"
          label={multiLabel}
          title={`${multiLabel} selected — ${title.toLowerCase()}`}
          disabled={disabled}
          selected={SEVERAL_SELECTED}
          options={[{ value: '', label: allLabel }, ...options.map(name => ({ value: name, label: name }))]}
          onPick={onChange}
          className="text-[12.5px] font-medium rounded-md"
        />
      </span>
    )
  }
  return (
    <label className="relative inline-flex items-center" title={title}>
      <span className="sr-only">Test suite</span>
      <select
        value={value}
        onChange={event => onChange(event.target.value)}
        disabled={disabled}
        className="appearance-none pl-3 pr-8 py-1.5 text-[12.5px] font-medium rounded-md bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)] focus:outline-none focus:border-[var(--color-ring)] disabled:opacity-60 disabled:cursor-not-allowed max-w-[240px]"
      >
        <option value="">{allLabel}</option>
        {suiteSelectOptions(options, value).map(name => (
          <option key={name.toLowerCase()} value={name}>{name}</option>
        ))}
      </select>
      <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-[var(--color-text-muted)] pointer-events-none" />
    </label>
  )
}
