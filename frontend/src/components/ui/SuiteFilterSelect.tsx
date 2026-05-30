import { ChevronDown } from 'lucide-react'

export default function SuiteFilterSelect({
  value,
  onChange,
  options,
  disabled = false,
  allLabel = 'All suites',
  title = 'Filter by test suite',
}: {
  value: string
  onChange: (value: string) => void
  options: string[]
  disabled?: boolean
  allLabel?: string
  title?: string
}) {
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
        {options.map(name => (
          <option key={name.toLowerCase()} value={name}>{name}</option>
        ))}
      </select>
      <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-[var(--color-text-muted)] pointer-events-none" />
    </label>
  )
}
