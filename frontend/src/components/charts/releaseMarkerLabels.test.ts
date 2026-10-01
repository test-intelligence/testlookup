/**
 * Wave 2.6 fix round (R2-10) — release marker labels never run together.
 *
 * At 375 px the Overview and Summary "Pass rate trend" drew "2026.09" and
 * "2026.10" side by side with no space between them, and the pair read as one
 * string, "2026.092026.10". The collision pass: a label that would touch the
 * one before it moves up one row; one that fits no row is dropped (the
 * tooltip and the release table still name it), and so is one whose own
 * marker line would run up under a neighbour's label.
 */
import { describe, expect, it } from 'vitest'
import { MARKER_LABEL_GAP, layoutMarkerLabels, markerLabelText } from './releaseMarkerLabels'

const at = (x: number, width = 40) => ({ x, width })

describe('layoutMarkerLabels', () => {
  it('keeps every label on the first row when none touches another', () => {
    expect(layoutMarkerLabels([at(50), at(150), at(250)])).toEqual([0, 0, 0])
  })

  it('moves a label that would touch the one before it up one row (the 375 px pair)', () => {
    // Centres 44 px apart, 40 px wide: 4 px between them is touching, not apart.
    expect(layoutMarkerLabels([at(100), at(100 + 40 + MARKER_LABEL_GAP - 1)])).toEqual([0, 1])
    expect(layoutMarkerLabels([at(100), at(100 + 40 + MARKER_LABEL_GAP)])).toEqual([0, 0])
  })

  it('returns to the first row as soon as there is room again', () => {
    expect(layoutMarkerLabels([at(100), at(130), at(200)])).toEqual([0, 1, 0])
  })

  it('drops a label that fits neither row', () => {
    expect(layoutMarkerLabels([at(100), at(125), at(135)])).toEqual([0, 1, null])
  })

  it('drops, rather than raises, a label whose marker line runs up under the label before it', () => {
    // The second marker is 10 px right of the first: the first's label (±20 px) covers its line.
    expect(layoutMarkerLabels([at(100), at(110)])).toEqual([0, null])
  })

  it('a long label is measured as long: widths decide, not the count of characters', () => {
    expect(layoutMarkerLabels([at(100, 120), at(155, 20)])).toEqual([0, null])
    expect(layoutMarkerLabels([at(100, 20), at(155, 20)])).toEqual([0, 0])
  })

  it('names every release on one marker, as the plot always did', () => {
    expect(markerLabelText({ names: ['2026.09', 'hotfix'] })).toBe('2026.09, hotfix')
  })
})
