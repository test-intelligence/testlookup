/**
 * VIZ-307: a frame names each filter the server declared it could not apply
 * ("Not filtered by release"), with the reason on hover and focus — and shows
 * no badge unless the API declared one (no client-side guess list).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { GALLERY_HEATMAP_DATA } from '@/pages/dev/chartGalleryFixtures'
import { STATES_META } from '@/pages/dev/chartStatesFixtures'
import ChartFrame from './ChartFrame'
import type { ChartState } from './chartState'

const ready = (meta: EnvelopeMeta | null): ChartState => ({
  status: 'ready',
  data: GALLERY_HEATMAP_DATA,
  meta,
  revalidating: false,
})

const frame = (state: ChartState, scope?: string) =>
  render(
    <ChartFrame title="Pass rate by suite" headingLevel={2} state={state} scope={scope}>
      <div>plot</div>
    </ChartFrame>,
  )

describe('ChartFrame "ignores filter" badges (VIZ-307)', () => {
  it('shows one badge per declared dimension, with the server’s reason on hover and focus', () => {
    frame(
      ready({
        ...STATES_META,
        ignored_filters: [
          { dimension: 'release', reason: 'Defect counts are project-wide.' },
          { dimension: 'window', reason: 'Clusters have no time window.' },
          { dimension: 'release', reason: 'a duplicate' },
        ],
      }),
    )
    const release = screen.getByText('Not filtered by release').closest('[data-scope-badge]') as HTMLElement
    expect(release).toHaveAttribute('data-scope-badge', 'release')
    expect(release).toHaveAttribute('title', 'Not filtered by release. Defect counts are project-wide.')
    expect(release).toHaveAttribute('tabindex', '0')
    expect(screen.getByText('Not filtered by time window').closest('[data-scope-badge]')).toHaveAttribute(
      'title',
      'Not filtered by time window. Clusters have no time window.',
    )
    // A dimension declared twice is one badge.
    expect(document.querySelectorAll('[data-scope-badge="release"]')).toHaveLength(1)
  })

  it('shows no badge when the API declares nothing ignored, or sent no meta', () => {
    const { unmount } = frame(ready(STATES_META))
    expect(document.querySelector('[data-scope-badge]')).toBeNull()
    expect(document.querySelector('[data-chart-scope]')).toBeNull()
    unmount()
    frame(ready(null))
    expect(document.querySelector('[data-scope-badge]')).toBeNull()
  })

  it('keeps the caller’s scope beside the badges', () => {
    frame(ready({ ...STATES_META, ignored_filters: [{ dimension: 'suite', reason: 'Per project.' }] }), 'Top 20 suites')
    const slot = document.querySelector('[data-chart-scope]') as HTMLElement
    expect(slot.textContent).toContain('Top 20 suites')
    expect(slot.querySelector('[data-scope-badge="suite"]')).not.toBeNull()
  })
})
