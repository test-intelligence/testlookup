/**
 * Fixed data for `/__primitives` (VIZ-109), shared with its e2e spec so the
 * spec asserts against the same numbers the page renders. No randomness.
 */
import type { ChipItem } from '@/components/ui/Chip'
import type { MultiSelectOption } from '@/components/ui/MultiSelect'

/** Must render as literal text. The `onerror` would set this global if it ever ran. */
export const HOSTILE_LABEL = '<img src=x onerror="window.__primitivesPwned=1"> Release <b>bold</b>'
export const HOSTILE_FLAG = '__primitivesPwned'

/** Exactly 300 characters, distinct at both ends. */
export const LONG_LABEL = `checkout-regression-${'nightly-'.repeat(40)}`.slice(0, 286) + '-payments-spec'

export const RELEASE_CAP = 3
export const RELEASE_OPTION_COUNT = 60
export const SUITE_OPTION_COUNT = 500

export const RELEASE_OPTIONS: MultiSelectOption[] = [
  ...Array.from({ length: RELEASE_OPTION_COUNT - 3 }, (_, i) => ({
    value: `r${i + 1}`,
    label: `Release ${i + 1}`,
    count: (i + 1) * 37,
  })),
  { value: 'r-archived', label: 'Release archived', count: 0, disabled: true, disabledReason: 'Archived' },
  { value: 'r-hostile', label: HOSTILE_LABEL, count: 12 },
  { value: 'r-long', label: LONG_LABEL, count: 1234567 },
]

export const SUITE_OPTIONS: MultiSelectOption[] = Array.from({ length: SUITE_OPTION_COUNT }, (_, i) => ({
  value: `suite-${i + 1}`,
  label: `suite-${String(i + 1).padStart(3, '0')}`,
  count: SUITE_OPTION_COUNT - i,
}))

export const BRANCH_OPTIONS: MultiSelectOption[] = [
  { value: 'main', label: 'main', count: 120 },
  { value: 'release/2.4', label: 'release/2.4', count: 18 },
  { value: 'feature/viz', label: 'feature/viz', count: 3 },
]

export const CHIP_LIMIT = 4

export const INITIAL_CHIPS: ChipItem[] = [
  { id: 'release:r2', label: 'Release', value: 'R2' },
  { id: 'release:r3', label: 'Release', value: 'R3' },
  { id: 'suite:checkout', label: 'Suite', value: 'checkout', warning: 'No runs in this window' },
  { id: 'suite:payments', label: 'Suite', value: 'payments' },
  { id: 'env:staging', label: 'Environment', value: 'staging' },
  { id: 'branch:main', label: 'Branch', value: 'main' },
  { id: 'suite:long', label: 'Suite', value: LONG_LABEL },
]
