/**
 * NTF-03: the release a Per Release digest is about, from the top bar
 * project's releases (the top bar's own cached list: no request of its own).
 * Ordered by `releaseOptions`: the active release, then in-progress, then the
 * rest. Names are React text.
 */
import type { Release } from '@/types/releases'
import { releaseOptions } from './digestScopeModel'

export const CHOOSE_RELEASE = 'Choose a release…'

export default function DigestReleaseSelect({
  id,
  releases,
  value,
  onChange,
  className = '',
  disabled = false,
}: {
  id?: string
  releases: readonly unknown[]
  value: string
  onChange: (releaseId: string) => void
  className?: string
  disabled?: boolean
}) {
  const options = releaseOptions(releases)
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      onChange={e => onChange(e.target.value)}
      data-digest-release-select=""
      className={`bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm max-w-[240px] ${className}`}
    >
      <option value="">{options.length ? CHOOSE_RELEASE : 'No releases in this project'}</option>
      {options.map((r: Release) => (
        <option key={r.id} value={r.id}>
          {r.name}
        </option>
      ))}
    </select>
  )
}
