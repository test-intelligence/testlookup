import { describe, expect, it } from 'vitest'
import type { Release } from '@/types/releases'
import { releaseOptionsFrom, UNATTRIBUTED_OPTION_LABEL } from './releaseOptions'

const release = (id: string, name: string, status: string) => ({ id, name, status }) as Release

describe('releaseOptionsFrom', () => {
  it('lists releases, marks archived ones, and ends with unattributed runs', () => {
    expect(releaseOptionsFrom([release('a', '2026.09', 'in_progress'), release('b', '2026.02', 'ARCHIVED')])).toEqual([
      { value: 'a', label: '2026.09' },
      { value: 'b', label: '2026.02 (archived)' },
      { value: 'unattributed', label: UNATTRIBUTED_OPTION_LABEL },
    ])
  })
})
