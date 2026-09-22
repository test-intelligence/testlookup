import type { MultiSelectOption } from '@/components/ui/MultiSelect'
import { UNATTRIBUTED_RELEASE } from '@/lib/viz/contracts'
import type { Release } from '@/types/releases'

export const UNATTRIBUTED_OPTION_LABEL = 'Unattributed runs'

/** Release filter options: the project's releases (archived ones say so), then unattributed runs. */
export function releaseOptionsFrom(releases: readonly Release[]): MultiSelectOption[] {
  return [
    ...releases.map((r) => ({
      value: r.id,
      label: r.status?.toLowerCase() === 'archived' ? `${r.name} (archived)` : r.name,
    })),
    { value: UNATTRIBUTED_RELEASE, label: UNATTRIBUTED_OPTION_LABEL },
  ]
}
